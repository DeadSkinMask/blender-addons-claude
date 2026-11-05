// SPDX-License-Identifier: GPL-3.0-or-later
//! From the integer-grid map to quads. The lattice of the map as a polygon mesh (proxy vertices rounding to the same
//! lattice point become one vertex, placed on the point; lattice neighbours are joined; faces traced), the odd faces
//! paired off along short paths and split so everything is quads, doublets merged, and a manifold repair for the
//! rare defects around folds of the map. Port of the extraction half of SculptForge's `QRemesh2.cpp`
//! (GPL-3.0-or-later). Anything iterated in an order that matters uses sorted containers, so the result is
//! deterministic.

use crate::field::Frames;
use crate::gridmap::GridMap;
use crate::proxy::{Proxy, NONE};
use crate::vec3::{v3, V3};
use crate::workmesh::edge_key;
use std::collections::{BTreeMap, HashMap};

pub struct Poly {
    pub p: Vec<V3>,
    pub faces: Vec<Vec<u32>>,
}

struct UnionFind(Vec<u32>);

impl UnionFind {
    fn new(n: usize) -> Self {
        UnionFind((0..n as u32).collect())
    }
    fn find(&mut self, mut x: u32) -> u32 {
        while self.0[x as usize] != x {
            self.0[x as usize] = self.0[self.0[x as usize] as usize];
            x = self.0[x as usize];
        }
        x
    }
    fn unite(&mut self, a: u32, b: u32) {
        let (a, b) = (self.find(a), self.find(b));
        if a != b {
            self.0[a.max(b) as usize] = a.min(b);
        }
    }
}

fn any_tangent(n: V3) -> V3 {
    let a = if n.x.abs() < 0.6 { v3(1.0, 0.0, 0.0) } else { v3(0.0, 1.0, 0.0) };
    n.cross(a).normalized()
}

/// Vertex -> triangles (CSR).
struct VertexFaces {
    off: Vec<u32>,
    tri: Vec<u32>,
}

impl VertexFaces {
    fn build(nv: usize, tris: &[[u32; 3]]) -> VertexFaces {
        let mut off = vec![0u32; nv + 1];
        for t in tris {
            for &v in t {
                off[v as usize + 1] += 1;
            }
        }
        for i in 0..nv {
            off[i + 1] += off[i];
        }
        let mut tri = vec![0u32; off[nv] as usize];
        let mut fill: Vec<u32> = off[..nv].to_vec();
        for (t, tr) in tris.iter().enumerate() {
            for &v in tr {
                tri[fill[v as usize] as usize] = t as u32;
                fill[v as usize] += 1;
            }
        }
        VertexFaces { off, tri }
    }
    fn of(&self, v: u32) -> &[u32] {
        &self.tri[self.off[v as usize] as usize..self.off[v as usize + 1] as usize]
    }
}

fn det2(a: [f32; 2], b: [f32; 2], c: [f32; 2]) -> f32 {
    (b[0] - a[0]) * (c[1] - a[1]) - (c[0] - a[0]) * (b[1] - a[1])
}

fn min3(a: f32, b: f32, c: f32) -> f32 {
    a.min(b).min(c)
}

/// Does the face repeat an edge (in either direction)? Returns the first repeated edge's ends.
fn repeated_edge(f: &[u32]) -> Option<(u32, u32)> {
    let mut seen: HashMap<u64, u8> = HashMap::with_capacity(f.len());
    for a in 0..f.len() {
        let (x, y) = (f[a], f[(a + 1) % f.len()]);
        let e = seen.entry(edge_key(x, y)).or_insert(0);
        *e += 1;
        if *e == 2 {
            return Some((x, y));
        }
    }
    None
}

/// The integer lattice of the map as a polygon mesh. `h`: the wanted quad edge (scales the merge tolerance).
pub fn extract_lattice(p: &Proxy, fr: &Frames, map: &GridMap, h: f32) -> Poly {
    let (nv, nt) = (p.p.len(), p.t.len());
    let l: Vec<[i32; 2]> = map.uv.iter().map(|uv| [uv[0].round() as i32, uv[1].round() as i32]).collect();
    let flipped: Vec<bool> = (0..nt).map(|t| det2(map.uv[3 * t], map.uv[3 * t + 1], map.uv[3 * t + 2]).is_nan() || det2(map.uv[3 * t], map.uv[3 * t + 1], map.uv[3 * t + 2]) <= 0.0).collect();

    // Lattice points no proxy corner rounds to (where the map stretches, often along a seam or a feature line next
    // to a singularity) become extra nodes: every integer point inside a triangle that none of its corners
    // represents, the same point from neighbouring triangles merged by position.
    struct Virt {
        tri: u32,
        node: u32,
        g: [i32; 2],
    }
    let mut virt: Vec<Virt> = Vec::new();
    let (mut virt_pos, mut virt_nrm): (Vec<V3>, Vec<V3>) = (Vec::new(), Vec::new());
    let mut virt_open: Vec<bool> = Vec::new();
    {
        let mut by_pos: HashMap<u64, u32> = HashMap::new();
        let q = 1e-4 * h.max(1e-12);
        for t in 0..nt {
            if flipped[t] {
                continue;
            }
            let (a, b, c) = (map.uv[3 * t], map.uv[3 * t + 1], map.uv[3 * t + 2]);
            let det = det2(a, b, c);
            if det.is_nan() || det <= 1e-9 {
                continue;
            }
            let (lo, hi) = ([min3(a[0], b[0], c[0]), min3(a[1], b[1], c[1])], [a[0].max(b[0]).max(c[0]), a[1].max(b[1]).max(c[1])]);
            if hi[0] - lo[0] > 64.0 || hi[1] - lo[1] > 64.0 {
                continue;
            }
            for gy in (lo[1] - 1e-4).ceil() as i32..=(hi[1] + 1e-4).floor() as i32 {
                for gx in (lo[0] - 1e-4).ceil() as i32..=(hi[0] + 1e-4).floor() as i32 {
                    let g = [gx, gy];
                    if l[3 * t] == g || l[3 * t + 1] == g || l[3 * t + 2] == g {
                        continue;
                    }
                    let pt = [gx as f32, gy as f32];
                    let w1 = ((pt[0] - a[0]) * (c[1] - a[1]) - (c[0] - a[0]) * (pt[1] - a[1])) / det;
                    let w2 = ((b[0] - a[0]) * (pt[1] - a[1]) - (pt[0] - a[0]) * (b[1] - a[1])) / det;
                    let w0 = 1.0 - w1 - w2;
                    if min3(w0, w1, w2) < -1e-4 {
                        continue;
                    }
                    let tri = p.t[t];
                    let at = p.p[tri[0] as usize] * w0 + p.p[tri[1] as usize] * w1 + p.p[tri[2] as usize] * w2;
                    let pack = |x: f32| ((x / q).floor() as i64 & 0x1f_ffff) as u64;
                    let key = (pack(at.x) << 42) | (pack(at.y) << 21) | pack(at.z);
                    let next_id = virt_pos.len() as u32;
                    let node = *by_pos.entry(key).or_insert(next_id);
                    if node == next_id {
                        virt_pos.push(at);
                        virt_nrm.push(fr.n[t]);
                        virt_open.push(false);
                    }
                    // On an open edge of the proxy (the weight of the opposite corner is ~0)?
                    let w = [w0, w1, w2];
                    for k in 0..3 {
                        if w[(k + 2) % 3] < 1e-4 && p.opp[3 * t + k] == NONE {
                            virt_open[node as usize] = true;
                        }
                    }
                    virt.push(Virt { tri: t as u32, node, g });
                }
            }
        }
    }
    let nn = nv + virt_pos.len();
    // Folded triangles (the map turned over) would merge unrelated lattice points: they don't join anything.
    let mut uf = UnionFind::new(nn);
    for t in 0..nt {
        if flipped[t] {
            continue;
        }
        for i in 0..3 {
            let j = (i + 1) % 3;
            if l[3 * t + i] == l[3 * t + j] {
                uf.unite(p.t[t][i], p.t[t][j]);
            }
        }
    }
    // A virtual node whose lattice point a vertex one ring away already rounds to (in the same chart: the shared
    // vertex has the same map value in both triangles) is that vertex's lattice point.
    if !virt.is_empty() {
        let vf = VertexFaces::build(nv, &p.t);
        for v in &virt {
            for k in 0..3 {
                let x = p.t[v.tri as usize][k];
                let at = map.uv[3 * v.tri as usize + k];
                for &t2 in vf.of(x) {
                    let t2 = t2 as usize;
                    if t2 == v.tri as usize || flipped[t2] {
                        continue;
                    }
                    let kx = (0..3).find(|&j| p.t[t2][j] == x).unwrap_or(0);
                    let o = map.uv[3 * t2 + kx];
                    if ((o[0] - at[0]).powi(2) + (o[1] - at[1]).powi(2)).sqrt() > 1e-4 {
                        continue; // across a seam
                    }
                    for j in 0..3 {
                        if l[3 * t2 + j] == v.g {
                            uf.unite((nv as u32) + v.node, p.t[t2][j]);
                        }
                    }
                }
            }
        }
    }
    let mut cluster = vec![NONE; nn];
    let mut nc = 0u32;
    for v in 0..nn as u32 {
        let r = uf.find(v) as usize;
        if cluster[r] == NONE {
            cluster[r] = nc;
            nc += 1;
        }
        cluster[v as usize] = cluster[r];
    }
    let nc = nc as usize;
    // Positions: the lattice point inside (or nearest) a triangle around the cluster, by its barycentric weights.
    let mut pos = vec![V3::ZERO; nc];
    let mut fit = vec![-1e30f32; nc];
    let mut nrm = vec![V3::ZERO; nc];
    for t in 0..nt {
        let (a, b, c) = (map.uv[3 * t], map.uv[3 * t + 1], map.uv[3 * t + 2]);
        let det = det2(a, b, c);
        for k in 0..3 {
            let cl = cluster[p.t[t][k] as usize] as usize;
            nrm[cl] += fr.n[t] * fr.area[t];
            if det.abs() < 1e-12 {
                continue;
            }
            let q = [l[3 * t + k][0] as f32, l[3 * t + k][1] as f32];
            let w1 = ((q[0] - a[0]) * (c[1] - a[1]) - (c[0] - a[0]) * (q[1] - a[1])) / det;
            let w2 = ((b[0] - a[0]) * (q[1] - a[1]) - (q[0] - a[0]) * (b[1] - a[1])) / det;
            let w0 = 1.0 - w1 - w2;
            let mut score = min3(w0, w1, w2);
            if flipped[t] {
                score -= 10.0;
            }
            if score > fit[cl] {
                fit[cl] = score;
                let (c0, c1, c2) = (w0.clamp(0.0, 1.0), w1.clamp(0.0, 1.0), w2.clamp(0.0, 1.0));
                let s = c0 + c1 + c2;
                let tri = p.t[t];
                pos[cl] = if s > 0.0 {
                    (p.p[tri[0] as usize] * c0 + p.p[tri[1] as usize] * c1 + p.p[tri[2] as usize] * c2) / s
                } else {
                    p.p[tri[k] as usize]
                };
            }
        }
    }
    for i in 0..virt_pos.len() {
        let cl = cluster[nv + i] as usize;
        pos[cl] = virt_pos[i];
        fit[cl] = 0.0;
        nrm[cl] += virt_nrm[i];
    }
    for n in nrm.iter_mut() {
        *n = n.normalized();
    }
    // Lattice edges.
    let mut adj: Vec<Vec<u32>> = vec![Vec::new(); nc];
    let link = |a: u32, b: u32, adj: &mut Vec<Vec<u32>>| {
        if a != b {
            adj[a as usize].push(b);
            adj[b as usize].push(a);
        }
    };
    for t in 0..nt {
        if flipped[t] {
            continue;
        }
        for i in 0..3 {
            let j = (i + 1) % 3;
            let d = [l[3 * t + i][0] - l[3 * t + j][0], l[3 * t + i][1] - l[3 * t + j][1]];
            if d[0].abs() + d[1].abs() != 1 {
                continue;
            }
            link(cluster[p.t[t][i] as usize], cluster[p.t[t][j] as usize], &mut adj);
        }
    }
    for (i, v) in virt.iter().enumerate() {
        let a = cluster[nv + v.node as usize];
        for k in 0..3 {
            let d = [l[3 * v.tri as usize + k][0] - v.g[0], l[3 * v.tri as usize + k][1] - v.g[1]];
            if d[0].abs() + d[1].abs() == 1 {
                link(a, cluster[p.t[v.tri as usize][k] as usize], &mut adj);
            }
        }
        for w in virt[i + 1..].iter().take_while(|w| w.tri == v.tri) {
            let d = [w.g[0] - v.g[0], w.g[1] - v.g[1]];
            if d[0].abs() + d[1].abs() == 1 {
                link(a, cluster[nv + w.node as usize], &mut adj);
            }
        }
    }
    for a in adj.iter_mut() {
        a.sort_unstable();
        a.dedup();
    }
    fn remove_edge(adj: &mut [Vec<u32>], a: u32, b: u32) {
        adj[a as usize].retain(|&x| x != b);
        adj[b as usize].retain(|&x| x != a);
    }
    fn has_edge(adj: &[Vec<u32>], a: u32, b: u32) -> bool {
        adj[a as usize].binary_search(&b).is_ok()
    }
    // A quad lattice has no 3-cycles: drop the longest edge of each.
    for _round in 0..6 {
        let mut drop: Vec<(u32, u32)> = Vec::new();
        for u in 0..nc as u32 {
            for &v in &adj[u as usize] {
                if v <= u {
                    continue;
                }
                for &w in &adj[u as usize] {
                    if w <= v || !has_edge(&adj, v, w) {
                        continue;
                    }
                    let luv = (pos[u as usize] - pos[v as usize]).length();
                    let lvw = (pos[v as usize] - pos[w as usize]).length();
                    let luw = (pos[u as usize] - pos[w as usize]).length();
                    if luv >= lvw && luv >= luw {
                        drop.push((u, v));
                    } else if lvw >= luw {
                        drop.push((v, w));
                    } else {
                        drop.push((u, w));
                    }
                }
            }
        }
        if drop.is_empty() {
            break;
        }
        for (a, b) in drop {
            if has_edge(&adj, a, b) {
                remove_edge(&mut adj, a, b);
            }
        }
    }
    let mut faces: Vec<Vec<u32>> = Vec::new();
    for round in 0..12 {
        // dangling edges go
        let mut stack: Vec<u32> = (0..nc as u32).filter(|&u| adj[u as usize].len() == 1).collect();
        while let Some(u) = stack.pop() {
            if adj[u as usize].len() != 1 {
                continue;
            }
            let v = adj[u as usize][0];
            remove_edge(&mut adj, u, v);
            if adj[v as usize].len() == 1 {
                stack.push(v);
            }
        }
        // neighbours of each vertex in counter-clockwise order in its tangent plane
        let mut ring: Vec<Vec<u32>> = vec![Vec::new(); nc];
        let mut he_off = vec![0u32; nc + 1];
        for u in 0..nc {
            let n = nrm[u];
            let q = any_tangent(n);
            let t = n.cross(q);
            let mut ang: Vec<(f32, u32)> = adj[u]
                .iter()
                .map(|&v| {
                    let d = pos[v as usize] - pos[u];
                    (d.dot(t).atan2(d.dot(q)), v)
                })
                .collect();
            ang.sort_by(|a, b| a.0.total_cmp(&b.0).then(a.1.cmp(&b.1)));
            ring[u] = ang.into_iter().map(|a| a.1).collect();
            he_off[u + 1] = he_off[u] + ring[u].len() as u32;
        }
        let slot = |ring: &[Vec<u32>], u: u32, v: u32| ring[u as usize].iter().position(|&x| x == v).unwrap_or(0) as u32;
        let mut used = vec![false; he_off[nc] as usize];
        faces.clear();
        let mut bad: Vec<(u32, u32)> = Vec::new();
        for u0 in 0..nc as u32 {
            for k0 in 0..ring[u0 as usize].len() as u32 {
                if used[(he_off[u0 as usize] + k0) as usize] {
                    continue;
                }
                let mut face: Vec<u32> = Vec::new();
                let (mut u, mut k) = (u0, k0);
                while !used[(he_off[u as usize] + k) as usize] {
                    used[(he_off[u as usize] + k) as usize] = true;
                    face.push(u);
                    let v = ring[u as usize][k as usize];
                    let back = slot(&ring, v, u);
                    let deg = ring[v as usize].len() as u32;
                    k = (back + deg - 1) % deg;
                    u = v;
                    if face.len() > he_off[nc] as usize {
                        break;
                    }
                }
                if let Some(e) = repeated_edge(&face) {
                    bad.push(e);
                }
                faces.push(face);
            }
        }
        if bad.is_empty() || round == 11 {
            break;
        }
        for (a, b) in bad {
            if has_edge(&adj, a, b) {
                remove_edge(&mut adj, a, b);
            }
        }
    }
    // The outside of an open surface traces as one big face (mostly on the boundary): leave it out. A face whose
    // vertices are mostly on open proxy edges is that.
    let mut open_cl = vec![false; nc];
    for t in 0..nt {
        for k in 0..3 {
            if p.opp[3 * t + k] == NONE {
                open_cl[cluster[p.t[t][k] as usize] as usize] = true;
                open_cl[cluster[p.t[t][(k + 1) % 3] as usize] as usize] = true;
            }
        }
    }
    for (i, &o) in virt_open.iter().enumerate() {
        if o {
            open_cl[cluster[nv + i] as usize] = true;
        }
    }
    // The outside walk (along the open edges) can bite into the surface where links along the open edge are missing:
    // each run of inside vertices between two open-edge vertices is a face of its own, else it would be a hole.
    {
        let mut bites: Vec<Vec<u32>> = Vec::new();
        for f in &faces {
            let on_b = f.iter().filter(|&&v| open_cl[v as usize]).count();
            if f.len() <= 32 && on_b != f.len() {
                continue;
            }
            if on_b * 10 < f.len() * 6 && f.len() <= 400 {
                continue; // kept whole above
            }
            if on_b == 0 || on_b == f.len() {
                continue;
            }
            let n = f.len();
            let start = (0..n).find(|&i| open_cl[f[i] as usize]).unwrap_or(0);
            let mut k = 0;
            while k < n {
                let i = (start + k) % n;
                if !open_cl[f[(i + 1) % n] as usize] {
                    let mut bite = vec![f[i]];
                    let (mut j, mut steps) = ((i + 1) % n, 0usize);
                    while !open_cl[f[j] as usize] && steps < n {
                        bite.push(f[j]);
                        j = (j + 1) % n;
                        steps += 1;
                    }
                    bite.push(f[j]);
                    if bite.len() >= 3 && bite.len() <= 16 && bite[0] != *bite.last().unwrap() {
                        bites.push(bite);
                    }
                    k += steps + 1;
                } else {
                    k += 1;
                }
            }
        }
        faces.extend(bites);
    }
    let mut out = Poly { p: Vec::new(), faces: Vec::new() };
    let mut remap = vec![NONE; nc];
    for mut f in faces {
        if f.len() < 3 {
            continue;
        }
        let on_b = f.iter().filter(|&&v| open_cl[v as usize]).count();
        if f.len() > 32 && (on_b * 10 >= f.len() * 6 || f.len() > 400) {
            continue; // the outside of an open surface, or a big hole in a closed one
        }
        if f.len() >= 5 && (on_b == f.len() || (f.len() > 12 && on_b * 10 >= f.len() * 8)) {
            continue; // the outside traces along the open edges only
        }
        if repeated_edge(&f).is_some() {
            continue;
        }
        for v in f.iter_mut() {
            if remap[*v as usize] == NONE {
                remap[*v as usize] = out.p.len() as u32;
                out.p.push(pos[*v as usize]);
            }
            *v = remap[*v as usize];
        }
        out.faces.push(f);
    }
    out
}

/// Splits an even polygon into quads by diagonals (shortest first, both halves even).
fn split_even(p: &[V3], f: &[u32], quads: &mut Vec<[u32; 4]>) {
    let n = f.len();
    if n < 4 {
        return;
    }
    if n == 4 {
        quads.push([f[0], f[1], f[2], f[3]]);
        return;
    }
    let mut best = 1e30f32;
    let (mut bi, mut bj) = (0usize, 3usize);
    for i in 0..n {
        let mut j = i + 3;
        while j < n {
            if n - (j - i) >= 3 && f[i] != f[j] {
                let l = (p[f[i] as usize] - p[f[j] as usize]).length();
                if l < best {
                    best = l;
                    bi = i;
                    bj = j;
                }
            }
            j += 2;
        }
    }
    let a: Vec<u32> = f[bi..=bj].to_vec();
    let mut b: Vec<u32> = f[bj..].to_vec();
    b.extend_from_slice(&f[..=bi]);
    split_even(p, &a, quads);
    split_even(p, &b, quads);
}

/// Odd faces (triangles, pentagons) are paired off along short paths of faces: each edge crossed gets a middle vertex,
/// so every face on the path gains two (or the ends one) and becomes even; then even faces are split into quads.
/// Odd faces left without a partner come out as quads plus one triangle in `leftover`, so the surface stays closed.
pub fn all_quads(pm: &mut Poly, leftover: &mut Vec<[u32; 3]>) -> Vec<[u32; 4]> {
    // Big faces first: cut faces of 6+ sides into even pieces along short diagonals where possible.
    {
        let mut next: Vec<Vec<u32>> = Vec::new();
        for f in std::mem::take(&mut pm.faces) {
            if f.len() <= 5 {
                next.push(f);
                continue;
            }
            let mut todo = vec![f];
            while let Some(g) = todo.pop() {
                if g.len() <= 5 {
                    next.push(g);
                    continue;
                }
                let n = g.len();
                let mut best = 1e30f32;
                let (mut bi, mut bj) = (0usize, 0usize);
                for i in 0..n {
                    for j in i + 3..n {
                        let (a, b) = (j - i + 1, n - (j - i) + 1);
                        if b < 4 || g[i] == g[j] {
                            continue;
                        }
                        let mut s = (pm.p[g[i] as usize] - pm.p[g[j] as usize]).length();
                        if a % 2 == 1 {
                            s *= 1.5;
                        }
                        if b % 2 == 1 {
                            s *= 1.5;
                        }
                        if s < best {
                            best = s;
                            bi = i;
                            bj = j;
                        }
                    }
                }
                if best == 1e30 {
                    next.push(g);
                    continue;
                }
                let fa: Vec<u32> = g[bi..=bj].to_vec();
                let mut fb: Vec<u32> = g[bj..].to_vec();
                fb.extend_from_slice(&g[..=bi]);
                todo.push(fa);
                todo.push(fb);
            }
        }
        pm.faces = next;
    }
    // Edge -> faces.
    let mut ef: HashMap<u64, Vec<u32>> = HashMap::new();
    for (i, f) in pm.faces.iter().enumerate() {
        for k in 0..f.len() {
            ef.entry(edge_key(f[k], f[(k + 1) % f.len()])).or_default().push(i as u32);
        }
    }
    fn insert_mid(pm: &mut Poly, ef: &mut HashMap<u64, Vec<u32>>, a: u32, b: u32) {
        let m = pm.p.len() as u32;
        pm.p.push((pm.p[a as usize] + pm.p[b as usize]) * 0.5);
        let fs = ef.remove(&edge_key(a, b)).unwrap_or_default();
        for fi in fs {
            let f = &mut pm.faces[fi as usize];
            for k in 0..f.len() {
                let (x, y) = (f[k], f[(k + 1) % f.len()]);
                if (x == a && y == b) || (x == b && y == a) {
                    f.insert(k + 1, m);
                    break;
                }
            }
            ef.entry(edge_key(a, m)).or_default().push(fi);
            ef.entry(edge_key(m, b)).or_default().push(fi);
        }
    }
    let mut matched = vec![false; pm.faces.len()];
    for start in 0..pm.faces.len() as u32 {
        if pm.faces[start as usize].len().is_multiple_of(2) || matched[start as usize] {
            continue;
        }
        // Breadth-first search over faces for the nearest other unmatched odd face (or an open edge).
        let mut prev: HashMap<u32, (u32, u64)> = HashMap::new(); // face -> (previous face, edge crossed)
        let mut queue = vec![start];
        prev.insert(start, (NONE, 0));
        let mut goal = NONE;
        let (mut open_edge, mut open_face) = (0u64, NONE);
        let mut qi = 0;
        while qi < queue.len() && goal == NONE {
            let fi = queue[qi];
            qi += 1;
            let f = &pm.faces[fi as usize];
            let mut k = 0;
            while k < f.len() && goal == NONE {
                let key = edge_key(f[k], f[(k + 1) % f.len()]);
                k += 1;
                let nb: &[u32] = ef.get(&key).map_or(&[], |v| v.as_slice());
                if nb.len() == 1 && open_face == NONE {
                    open_face = fi;
                    open_edge = key;
                }
                for &g in nb {
                    if g == fi || prev.contains_key(&g) {
                        continue;
                    }
                    prev.insert(g, (fi, key));
                    if pm.faces[g as usize].len() % 2 == 1 && !matched[g as usize] {
                        goal = g;
                        break;
                    }
                    queue.push(g);
                }
            }
        }
        let mut crossed: Vec<u64> = Vec::new();
        if goal != NONE {
            let mut g = goal;
            while prev[&g].0 != NONE {
                crossed.push(prev[&g].1);
                g = prev[&g].0;
            }
            matched[goal as usize] = true;
        } else if open_face != NONE {
            let mut g = open_face;
            while prev[&g].0 != NONE {
                crossed.push(prev[&g].1);
                g = prev[&g].0;
            }
            crossed.push(open_edge);
        } else {
            continue;
        }
        matched[start as usize] = true;
        for key in crossed {
            insert_mid(pm, &mut ef, (key >> 32) as u32, (key & 0xffff_ffff) as u32);
        }
    }
    let mut quads: Vec<[u32; 4]> = Vec::new();
    for f in &pm.faces {
        if f.len() % 2 == 0 {
            split_even(&pm.p, f, &mut quads);
        } else {
            // an odd face left without a partner (rare): quads and one triangle, so the surface stays closed
            let n = f.len();
            let mut i = 1;
            while i + 3 < n {
                quads.push([f[0], f[i], f[i + 1], f[i + 2]]);
                i += 2;
            }
            leftover.push([f[0], f[i], f[i + 1]]);
        }
    }
    quads
}

/// Doublets (an inside vertex with two quads) merge the two quads; unused vertices go.
pub fn clean_quads(p: &mut Vec<V3>, quads: &mut Vec<[u32; 4]>, tris: &mut [[u32; 3]]) {
    for _pass in 0..4 {
        let nv = p.len();
        let mut vq: Vec<Vec<u32>> = vec![Vec::new(); nv];
        for (f, q) in quads.iter().enumerate() {
            for &v in q {
                vq[v as usize].push(f as u32);
            }
        }
        let mut dead = vec![false; quads.len()];
        let mut touched = vec![false; nv];
        for t in tris.iter() {
            for &v in t {
                touched[v as usize] = true; // a triangle's corner is no doublet
            }
        }
        let rotated = |quads: &[[u32; 4]], f: u32, v: u32| {
            let mut r = quads[f as usize];
            for _ in 0..4 {
                if r[0] == v {
                    break;
                }
                r = [r[1], r[2], r[3], r[0]];
            }
            r
        };
        let mut any = false;
        for v in 0..nv as u32 {
            if vq[v as usize].len() != 2 || touched[v as usize] {
                continue;
            }
            let (fa, fb) = (vq[v as usize][0], vq[v as usize][1]);
            let (a, b) = (rotated(quads, fa, v), rotated(quads, fb, v));
            if a[0] != v || b[0] != v || b[1] != a[3] || b[3] != a[1] || a[2] == b[2] {
                continue;
            }
            if (0..4).any(|k| touched[a[k] as usize] || touched[b[k] as usize]) {
                continue;
            }
            quads[fa as usize] = [a[1], a[2], a[3], b[2]];
            dead[fb as usize] = true;
            for k in 0..4 {
                touched[a[k] as usize] = true;
                touched[b[k] as usize] = true;
            }
            any = true;
        }
        if !any {
            break;
        }
        let mut w = 0;
        for f in 0..quads.len() {
            if !dead[f] {
                quads[w] = quads[f];
                w += 1;
            }
        }
        quads.truncate(w);
    }
    let mut remap = vec![NONE; p.len()];
    let mut q: Vec<V3> = Vec::new();
    for f in quads.iter_mut() {
        for v in f.iter_mut() {
            if remap[*v as usize] == NONE {
                remap[*v as usize] = q.len() as u32;
                q.push(p[*v as usize]);
            }
            *v = remap[*v as usize];
        }
    }
    for f in tris.iter_mut() {
        for v in f.iter_mut() {
            if remap[*v as usize] == NONE {
                remap[*v as usize] = q.len() as u32;
                q.push(p[*v as usize]);
            }
            *v = remap[*v as usize];
        }
    }
    *p = q;
}

/// Quads forming a 2-manifold: every edge in at most two faces, wound opposite ways, and each vertex's quads one fan.
pub fn quads_manifold(nv: usize, quads: &[[u32; 4]], tris: &[[u32; 3]]) -> bool {
    let mut edges: HashMap<u64, (u32, u32)> = HashMap::new();
    let mut count = |a: u32, b: u32| -> bool {
        if a == b {
            return false;
        }
        let e = edges.entry(edge_key(a, b)).or_insert((0, 0));
        if a < b {
            e.0 += 1;
        } else {
            e.1 += 1;
        }
        true
    };
    for q in quads {
        for k in 0..4 {
            if !count(q[k], q[(k + 1) % 4]) {
                return false;
            }
        }
    }
    for t in tris {
        for k in 0..3 {
            if !count(t[k], t[(k + 1) % 3]) {
                return false;
            }
        }
    }
    if edges.values().any(|e| e.0 > 1 || e.1 > 1) {
        return false;
    }
    // Fans: corners (prev, next) around each vertex must chain into one cycle or path.
    let mut fan: Vec<Vec<(u32, u32)>> = vec![Vec::new(); nv];
    for q in quads {
        for k in 0..4 {
            fan[q[k] as usize].push((q[(k + 3) % 4], q[(k + 1) % 4]));
        }
    }
    for c in &fan {
        if c.len() < 2 {
            continue;
        }
        let by_next: HashMap<u32, usize> = c.iter().enumerate().map(|(i, x)| (x.1, i)).collect();
        let mut start = 0;
        for i in 0..c.len() {
            if !c.iter().any(|x| x.0 == c[i].1) {
                start = i;
            }
        }
        let mut seen = vec![false; c.len()];
        let (mut walked, mut cur) = (0, start);
        while !seen[cur] {
            seen[cur] = true;
            walked += 1;
            match by_next.get(&c[cur].0) {
                Some(&n) => cur = n,
                None => break,
            }
        }
        if walked != c.len() {
            return false;
        }
    }
    true
}

/// Makes the quads a 2-manifold where extraction left defects (rare, around folds of the map): faces on an edge used
/// more than twice or twice the same way are taken out and the holes they leave refilled as polygons (paired off into
/// quads like any odd face), and a vertex whose quads form several fans gets one copy per fan.
/// Returns how many defects were repaired.
pub fn repair_manifold(p: &mut Vec<V3>, quads: &mut Vec<[u32; 4]>, tris: &mut Vec<[u32; 3]>) -> usize {
    let mut repaired = 0usize;
    // Edges used badly (triangles left over count as faces too).
    {
        let mut faces: Vec<Vec<u32>> = quads.iter().map(|q| q.to_vec()).collect();
        faces.extend(tris.iter().map(|t| t.to_vec()));
        let mut usage: HashMap<u64, Vec<(u32, bool)>> = HashMap::new(); // edge -> (face, a<b forwards)
        for (f, face) in faces.iter().enumerate() {
            for k in 0..face.len() {
                let (x, y) = (face[k], face[(k + 1) % face.len()]);
                usage.entry(edge_key(x, y)).or_default().push((f as u32, x < y));
            }
        }
        let mut drop = vec![false; faces.len()];
        let mut keys: Vec<&u64> = usage.keys().collect();
        keys.sort_unstable();
        for key in keys {
            let list = &usage[key];
            let bad = list.len() > 2 || (list.len() == 2 && list[0].1 == list[1].1);
            if !bad {
                continue;
            }
            for &(f, _) in list {
                drop[f as usize] = true;
            }
            repaired += 1;
        }
        for (f, face) in faces.iter().enumerate() {
            let mut s2 = face.clone();
            s2.sort_unstable();
            if s2.windows(2).any(|w| w[0] == w[1]) {
                drop[f] = true;
                repaired += 1;
            }
        }
        if repaired > 0 {
            let mut kept_use: HashMap<u64, u32> = HashMap::new();
            let mut kept_dir: HashMap<u64, (u32, u32)> = HashMap::new();
            for (f, face) in faces.iter().enumerate() {
                if drop[f] {
                    continue;
                }
                for k in 0..face.len() {
                    let (x, y) = (face[k], face[(k + 1) % face.len()]);
                    *kept_use.entry(edge_key(x, y)).or_insert(0) += 1;
                    kept_dir.insert(edge_key(x, y), (x, y));
                }
            }
            let mut next: BTreeMap<u32, Vec<u32>> = BTreeMap::new();
            let mut seen: std::collections::HashSet<u64> = std::collections::HashSet::new();
            for (f, face) in faces.iter().enumerate() {
                if !drop[f] {
                    continue;
                }
                for k in 0..face.len() {
                    let key = edge_key(face[k], face[(k + 1) % face.len()]);
                    if !seen.insert(key) {
                        continue;
                    }
                    if kept_use.get(&key) != Some(&1) {
                        continue;
                    }
                    let (x, y) = kept_dir[&key];
                    next.entry(y).or_default().push(x);
                }
            }
            let mut pm = Poly { p: p.clone(), faces: Vec::new() };
            for (f, face) in faces.iter().enumerate() {
                if !drop[f] {
                    pm.faces.push(face.clone());
                }
            }
            let mut used: HashMap<u32, usize> = HashMap::new();
            let starts: Vec<u32> = next.keys().copied().collect();
            for start in starts {
                while *used.get(&start).unwrap_or(&0) < next[&start].len() {
                    let mut lp = vec![start];
                    let mut v = start;
                    let mut closed = false;
                    for _ in 0..200 {
                        let Some(outs) = next.get(&v) else { break };
                        let u = used.entry(v).or_insert(0);
                        if *u >= outs.len() {
                            break;
                        }
                        let w = outs[*u];
                        *u += 1;
                        if w == start {
                            closed = true;
                            break;
                        }
                        if let Some(again) = lp.iter().position(|&x| x == w) {
                            // a figure eight: emit the inner loop, keep walking
                            let inner: Vec<u32> = lp[again..].to_vec();
                            if inner.len() >= 3 {
                                pm.faces.push(inner);
                            }
                            lp.truncate(again + 1);
                            v = w;
                            continue;
                        }
                        lp.push(w);
                        v = w;
                    }
                    if closed && lp.len() >= 3 {
                        pm.faces.push(lp);
                    }
                    if !closed {
                        break;
                    }
                }
            }
            let mut left: Vec<[u32; 3]> = Vec::new();
            *quads = all_quads(&mut pm, &mut left);
            *p = pm.p;
            *tris = left;
        }
    }
    // Pinched vertices: one copy per fan.
    {
        let nv = p.len();
        let mut corners: Vec<Vec<(u32, usize)>> = vec![Vec::new(); nv]; // (quad, corner index)
        for (f, q) in quads.iter().enumerate() {
            for k in 0..4 {
                corners[q[k] as usize].push((f as u32, k));
            }
        }
        for v in 0..nv {
            let c = &corners[v];
            if c.len() < 2 {
                continue;
            }
            // Union corners sharing an edge at v (the next vertex of one is the previous of another).
            let mut uf = UnionFind::new(c.len());
            for i in 0..c.len() {
                for j in 0..c.len() {
                    if i == j {
                        continue;
                    }
                    let (qi, qj) = (quads[c[i].0 as usize], quads[c[j].0 as usize]);
                    if qi[(c[i].1 + 1) % 4] == qj[(c[j].1 + 3) % 4] {
                        let (a, b) = (uf.find(i as u32), uf.find(j as u32));
                        uf.0[a as usize] = b;
                    }
                }
            }
            let mut copy_of: BTreeMap<u32, u32> = BTreeMap::new();
            for i in 0..c.len() {
                let r = uf.find(i as u32);
                copy_of.entry(r).or_insert(0);
            }
            if copy_of.len() < 2 {
                continue;
            }
            repaired += 1;
            let mut first_fan = true;
            for id in copy_of.values_mut() {
                if first_fan {
                    *id = v as u32;
                    first_fan = false;
                } else {
                    *id = p.len() as u32;
                    let pv = p[v];
                    p.push(pv);
                }
            }
            for i in 0..c.len() {
                let r = uf.find(i as u32);
                quads[c[i].0 as usize][c[i].1] = copy_of[&r];
            }
        }
    }
    repaired
}

#[cfg(test)]
mod tests {
    use super::*;

    fn square_grid(n: usize) -> Poly {
        let mut p = Vec::new();
        for j in 0..=n {
            for i in 0..=n {
                p.push(v3(i as f32, j as f32, 0.0));
            }
        }
        let id = |i: usize, j: usize| (j * (n + 1) + i) as u32;
        let mut faces = Vec::new();
        for j in 0..n {
            for i in 0..n {
                faces.push(vec![id(i, j), id(i + 1, j), id(i + 1, j + 1), id(i, j + 1)]);
            }
        }
        Poly { p, faces }
    }

    #[test]
    fn even_polygons_split_into_quads() {
        let hex: Vec<V3> = (0..6).map(|k| v3((k as f32).cos(), (k as f32).sin(), 0.0)).collect();
        let mut quads = Vec::new();
        split_even(&hex, &[0, 1, 2, 3, 4, 5], &mut quads);
        assert_eq!(quads.len(), 2);
        let mut used: Vec<u32> = quads.iter().flatten().copied().collect();
        used.sort_unstable();
        used.dedup();
        assert_eq!(used, vec![0, 1, 2, 3, 4, 5]);
        let mut q8 = Vec::new();
        let oct: Vec<V3> = (0..8).map(|k| v3((k as f32).cos(), (k as f32).sin(), 0.0)).collect();
        split_even(&oct, &(0..8).collect::<Vec<u32>>(), &mut q8);
        assert_eq!(q8.len(), 3);
    }

    #[test]
    fn all_even_quads_pass_through_unchanged() {
        let mut pm = square_grid(4);
        let mut left = Vec::new();
        let quads = all_quads(&mut pm, &mut left);
        assert_eq!(quads.len(), 16);
        assert!(left.is_empty());
    }

    #[test]
    fn two_odd_faces_are_paired_into_quads() {
        // a triangle and a pentagon sharing an edge: after pairing along that edge, both are even
        //   triangle (0, 1, 2), pentagon (1, 3, 4, 5, 2) -> shared edge (1, 2)
        let p = vec![
            v3(0.0, 0.0, 0.0), v3(1.0, 0.0, 0.0), v3(0.5, 1.0, 0.0),
            v3(2.0, 0.0, 0.0), v3(2.2, 1.0, 0.0), v3(1.2, 1.6, 0.0),
        ];
        let mut pm = Poly { p, faces: vec![vec![0, 1, 2], vec![1, 3, 4, 5, 2]] };
        let mut left = Vec::new();
        let quads = all_quads(&mut pm, &mut left);
        assert!(left.is_empty(), "{left:?}");
        // 3 + 5 + 2 (one middle vertex on the shared edge gives each face one more) = 10 sides = 2.5 quads, so the
        // path pairing adds two vertices: faces of 4 and 6 -> 1 + 2 quads
        assert_eq!(quads.len(), 3, "{quads:?}");
    }

    #[test]
    fn doublets_are_merged() {
        // two quads sharing a vertex pair-wise (an inside vertex of valence 2): v = 0 with quads (0,1,2,3) and (0,3,4,1)
        let p: Vec<V3> = (0..5).map(|i| v3(i as f32, (i * i) as f32 * 0.1, 0.0)).collect();
        let mut quads = vec![[0, 1, 2, 3], [0, 3, 4, 1]];
        let mut tris: Vec<[u32; 3]> = Vec::new();
        let mut pp = p;
        clean_quads(&mut pp, &mut quads, &mut tris);
        assert_eq!(quads.len(), 1, "{quads:?}");
        assert_eq!(pp.len(), 4, "the doublet vertex is gone");
    }

    #[test]
    fn manifold_check_accepts_a_cube_and_rejects_defects() {
        let cube: Vec<[u32; 4]> = vec![[0, 3, 2, 1], [4, 5, 6, 7], [0, 1, 5, 4], [2, 3, 7, 6], [1, 2, 6, 5], [3, 0, 4, 7]];
        assert!(quads_manifold(8, &cube, &[]));
        // an edge used twice the same way
        let mut flipped = cube.clone();
        flipped[0] = [0, 1, 2, 3];
        assert!(!quads_manifold(8, &flipped, &[]));
        // a degenerate quad
        let mut degenerate = cube.clone();
        degenerate[1] = [4, 4, 6, 7];
        assert!(!quads_manifold(8, &degenerate, &[]));
        // an extra face on an edge makes three uses
        let mut extra = cube.clone();
        extra.push([0, 1, 5, 4]);
        assert!(!quads_manifold(8, &extra, &[]));
    }

    #[test]
    fn pinched_vertices_get_a_copy_per_fan() {
        // two quads touching only at vertex 2 (a bow tie): one vertex, two fans
        let mut p: Vec<V3> = (0..7).map(|i| v3(i as f32, 0.0, 0.0)).collect();
        let mut quads = vec![[0, 1, 2, 3], [2, 4, 5, 6]];
        let mut tris: Vec<[u32; 3]> = Vec::new();
        let n = repair_manifold(&mut p, &mut quads, &mut tris);
        assert!(n >= 1);
        assert_eq!(p.len(), 8, "vertex 2 is copied");
        let touching: usize = quads.iter().filter(|q| q.contains(&2)).count();
        assert_eq!(touching, 1, "{quads:?}");
    }
}
