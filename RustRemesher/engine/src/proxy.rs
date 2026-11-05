// SPDX-License-Identifier: GPL-3.0-or-later
//! The proxy surface: the source brought to about three triangle edges per quad edge, with even triangles, so the
//! cross field and the integer-grid map have a well-shaped mesh to live on whatever the source's density.
//!
//!  1. Decimation: rounds of shortest-edge-first collapses (each round touches a vertex's neighbourhood once, so the
//!     density stays even); collapsed vertices settle at the middle of the edge and back onto the source.
//!  2. Isotropic remeshing (Botsch & Kobbelt, "A remeshing approach to multiresolution modeling", 2004): split long
//!     edges, collapse short ones, flip edges towards valence 6, smooth tangentially and project onto the source,
//!     towards a per-vertex target length.
//!
//! Feature lines (open edges, group borders, hard edges) are kept: their vertices only collapse along the line, slide
//! along it when smoothed and project onto the source's line; where lines meet or turn sharply the vertex is pinned.
//! Port of SculptForge's `QRemesh2Proxy.cpp` (GPL-3.0-or-later). Deterministic: candidates are sorted with total
//! tie-breaks and the parallel parts only gather in fixed block order.

use crate::bvh::{SegmentBvh, TriangleBvh};
use crate::mesh::{Attributes, Mesh};
use crate::vec3::{v3, V3};
use crate::workmesh::{edge_key, key_ends, WorkMesh};
use rayon::prelude::*;
use std::collections::{HashMap, HashSet};

pub const NONE: u32 = u32::MAX;

/// Proxy triangles per quad edge (a quad edge is about this many triangle edges long).
pub const DENSITY: f32 = 3.0;
/// Upper bound on the proxy size, in triangles.
pub const MAX_PROXY_TRIANGLES: f64 = 400_000.0;

/// The triangle mesh the field and the map are solved on.
#[derive(Clone, Debug, Default)]
pub struct Proxy {
    pub p: Vec<V3>,
    pub t: Vec<[u32; 3]>,
    /// Per triangle.
    pub group: Vec<u32>,
    /// Per corner 3t+k (the edge t[k] -> t[k+1]): the twin corner, NONE when open.
    pub opp: Vec<u32>,
    /// Per corner: the edge is a feature line (open edges included).
    pub feature: Vec<u8>,
    /// Per corner: ... because it is a hard edge.
    pub sharp: Vec<u8>,
    /// Per vertex: wanted quad edge length.
    pub size: Vec<f32>,
    /// Per vertex: the (relative) size the cross field turns with (vertex paint density); empty = none.
    pub field_size: Vec<f32>,
    /// Per triangle: direction the field is fixed to (a guide's tangent), zero = from its edges; empty = none.
    pub fixed_dir: Vec<V3>,
}

impl Proxy {
    pub fn triangles(&self) -> usize {
        self.t.len()
    }

    /// `opp` from `t` (edges with other than two consistently wound triangles stay open).
    pub fn build_opposites(&mut self) {
        let nt = self.t.len();
        self.opp = vec![NONE; 3 * nt];
        let mut seen: HashMap<u64, (u32, u32)> = HashMap::with_capacity(3 * nt / 2 + 16); // key -> (count, first corner)
        for (ti, tr) in self.t.iter().enumerate() {
            for k in 0..3 {
                let key = edge_key(tr[k], tr[(k + 1) % 3]);
                let e = seen.entry(key).or_insert((0, (3 * ti + k) as u32));
                e.0 += 1;
            }
        }
        for ti in 0..nt {
            for k in 0..3 {
                let (a, b) = (self.t[ti][k], self.t[ti][(k + 1) % 3]);
                let (count, c) = seen[&edge_key(a, b)];
                let me = (3 * ti + k) as u32;
                if count != 2 || c == me {
                    continue;
                }
                let (ot, ok) = ((c / 3) as usize, (c % 3) as usize);
                // Consistently wound neighbours run the edge the other way.
                if self.t[ot][ok] != b || self.t[ot][(ok + 1) % 3] != a {
                    continue;
                }
                self.opp[me as usize] = c;
                self.opp[c as usize] = me;
            }
        }
    }

    /// Triangle shape statistics (angles in degrees, edge length relative to `edge`).
    pub fn quality(&self, edge: f32) -> ProxyQuality {
        let mut val = vec![0u32; self.p.len()];
        let mut sum = 0f64;
        let mut count = 0usize;
        let mut min_angle = 180f32;
        let mut thin = 0usize;
        for tri in &self.t {
            let q = [self.p[tri[0] as usize], self.p[tri[1] as usize], self.p[tri[2] as usize]];
            let mut tri_min = 180f32;
            for k in 0..3 {
                let (a, b) = (q[(k + 1) % 3] - q[k], q[(k + 2) % 3] - q[k]);
                tri_min = tri_min.min(a.cross(b).length().atan2(a.dot(b)).to_degrees());
                sum += a.length() as f64;
                count += 1;
                val[tri[k] as usize] += 1;
            }
            min_angle = min_angle.min(tri_min);
            thin += (tri_min < 20.0) as usize;
        }
        // every vertex of a closed mesh sees as many triangles as edges: valence = triangle count around it
        let regular = val.iter().filter(|&&v| v == 6).count();
        ProxyQuality {
            mean_edge: (sum / count.max(1) as f64) as f32 / edge,
            regular_vertices: regular as f32 / self.p.len().max(1) as f32,
            min_angle,
            thin_triangles: thin as f32 / self.t.len().max(1) as f32,
        }
    }

    pub fn to_mesh(&self) -> Mesh {
        Mesh {
            positions: self.p.iter().map(|v| v.to_array()).collect(),
            face_sizes: vec![3; self.t.len()],
            indices: self.t.iter().flatten().copied().collect(),
            attrs: Attributes::default(),
        }
    }
}

#[derive(Clone, Copy, Debug)]
pub struct ProxyQuality {
    /// Mean triangle edge over the wanted edge.
    pub mean_edge: f32,
    /// Share of vertices with six triangles around them (interior; borders count as irregular).
    pub regular_vertices: f32,
    /// Smallest triangle angle, degrees.
    pub min_angle: f32,
    /// Share of triangles whose smallest angle is under 20 degrees.
    pub thin_triangles: f32,
}

fn has(t: [u32; 3], v: u32) -> bool {
    t[0] == v || t[1] == v || t[2] == v
}

/// a -> b runs forwards in t.
fn forwards(t: [u32; 3], a: u32, b: u32) -> bool {
    (t[0] == a && t[1] == b) || (t[1] == a && t[2] == b) || (t[2] == a && t[0] == b)
}

#[derive(Clone, Copy)]
struct Cand {
    len2: f32,
    a: u32,
    b: u32,
}

fn asc(x: &Cand, y: &Cand) -> std::cmp::Ordering {
    x.len2.total_cmp(&y.len2).then(x.a.cmp(&y.a)).then(x.b.cmp(&y.b))
}

fn desc(x: &Cand, y: &Cand) -> std::cmp::Ordering {
    y.len2.total_cmp(&x.len2).then(x.a.cmp(&y.a)).then(x.b.cmp(&y.b))
}

pub struct ProxyBuilder<'a> {
    surface: &'a TriangleBvh,
    features: Option<&'a SegmentBvh>,
    sharp: HashSet<u64>,
    groups: bool,
    p: Vec<V3>,
    t: Vec<[u32; 3]>,
    g: Vec<u32>,
    dead_t: Vec<bool>,
    dead_v: Vec<bool>,
    vt: Vec<Vec<u32>>,
    /// Per vertex target length (remesh).
    lt: Vec<f32>,
    /// Pre-decimation blocks: vertices shared with other blocks never go.
    locked: Vec<bool>,
    reach: f32,
}

impl<'a> ProxyBuilder<'a> {
    pub fn new(
        src: &WorkMesh,
        surface: &'a TriangleBvh,
        features: Option<&'a SegmentBvh>,
        sharp: HashSet<u64>,
        keep_groups: bool,
    ) -> Self {
        let p = src.p.clone();
        let t = src.t.clone();
        let mut g = src.group.clone();
        if g.len() != t.len() {
            g = vec![0; t.len()];
        }
        let mut dead_t = vec![false; t.len()];
        let mut dead_v = vec![false; p.len()];
        let mut vt: Vec<Vec<u32>> = vec![Vec::new(); p.len()];
        for (ti, tri) in t.iter().enumerate() {
            if tri[0] == tri[1] || tri[1] == tri[2] || tri[0] == tri[2] {
                dead_t[ti] = true;
                continue;
            }
            for &v in tri {
                vt[v as usize].push(ti as u32);
            }
        }
        for v in 0..p.len() {
            if vt[v].is_empty() {
                dead_v[v] = true;
            }
        }
        let lt = vec![1.0; p.len()];
        ProxyBuilder { surface, features, sharp, groups: keep_groups, p, t, g, dead_t, dead_v, vt, lt, locked: Vec::new(), reach: 1.0 }
    }

    pub fn vertex_count(&self) -> usize {
        self.p.len()
    }

    /// Hard edges still alive (vertex numbers of this builder).
    pub fn sharp_edges(&self) -> &HashSet<u64> {
        &self.sharp
    }

    fn edge_tris(&self, a: u32, b: u32, out: &mut [u32; 4]) -> usize {
        let mut n = 0;
        for &t in &self.vt[a as usize] {
            if has(self.t[t as usize], b) {
                if n < 4 {
                    out[n] = t;
                }
                n += 1;
            }
        }
        n
    }

    fn feature_edge(&self, a: u32, b: u32) -> bool {
        let mut t = [0u32; 4];
        if self.edge_tris(a, b, &mut t) != 2 {
            return true;
        }
        let (t0, t1) = (self.t[t[0] as usize], self.t[t[1] as usize]);
        if forwards(t0, a, b) == forwards(t1, a, b) {
            return true; // wound inconsistently
        }
        if self.groups && self.g[t[0] as usize] != self.g[t[1] as usize] {
            return true;
        }
        !self.sharp.is_empty() && self.sharp.contains(&edge_key(a, b))
    }

    fn ring(&self, v: u32, out: &mut Vec<u32>) {
        out.clear();
        for &t in &self.vt[v as usize] {
            for &x in &self.t[t as usize] {
                if x != v {
                    out.push(x);
                }
            }
        }
        out.sort_unstable();
        out.dedup();
    }

    /// Feature edges at v: how many, and the first two other ends (ascending). One pass over the triangles around v
    /// (O(d log d), not O(d^2): the poles of a UV sphere have valence in the thousands).
    fn feature_info(&self, v: u32, ends: &mut [u32; 2], _scratch: &mut Vec<u32>) -> usize {
        let around = &self.vt[v as usize];
        let mut pairs: Vec<u64> = Vec::with_capacity(2 * around.len());
        for &t in around {
            for &x in &self.t[t as usize] {
                if x != v {
                    pairs.push(((x as u64) << 32) | t as u64);
                }
            }
        }
        pairs.sort_unstable();
        let (mut n, mut i) = (0usize, 0usize);
        while i < pairs.len() {
            let u = (pairs[i] >> 32) as u32;
            let mut j = i;
            while j < pairs.len() && (pairs[j] >> 32) as u32 == u {
                j += 1;
            }
            let feature = if j - i != 2 {
                true // open or non-manifold edge
            } else {
                let (t0, t1) = ((pairs[i] & 0xffff_ffff) as usize, (pairs[i + 1] & 0xffff_ffff) as usize);
                forwards(self.t[t0], v, u) == forwards(self.t[t1], v, u) // wound inconsistently
                    || (self.groups && self.g[t0] != self.g[t1])
                    || (!self.sharp.is_empty() && self.sharp.contains(&edge_key(v, u)))
            };
            if feature {
                if n < 2 {
                    ends[n] = u;
                }
                n += 1;
            }
            i = j;
        }
        n
    }

    /// 0 free, 1 on a feature line, 2 pinned (lines meet, end, or turn by more than 45 degrees).
    fn kind(&self, v: u32, ends: &mut [u32; 2], scratch: &mut Vec<u32>) -> u8 {
        if !self.locked.is_empty() && self.locked[v as usize] {
            return 2;
        }
        let n = self.feature_info(v, ends, scratch);
        if n == 0 {
            return 0;
        }
        if n != 2 {
            return 2;
        }
        let pv = self.p[v as usize];
        let a = (self.p[ends[0] as usize] - pv).normalized();
        let b = (self.p[ends[1] as usize] - pv).normalized();
        if a.dot(b) > -0.7 {
            2
        } else {
            1
        }
    }

    fn tri_normal(&self, t: [u32; 3]) -> V3 {
        (self.p[t[1] as usize] - self.p[t[0] as usize]).cross(self.p[t[2] as usize] - self.p[t[0] as usize])
    }

    fn project_surface(&self, p: V3) -> V3 {
        self.surface.closest_point(p, self.reach).map_or(p, |h| h.position)
    }

    fn project_feature(&self, p: V3) -> V3 {
        if let Some(f) = self.features {
            if let Some(h) = f.closest_point(p, self.reach) {
                return h.position;
            }
        }
        self.project_surface(p)
    }

    /// Would collapsing a and b into `to` leave the triangles around them healthy? Per triangle: no degenerate or
    /// flipped result. Over the whole neighbourhood: the worst triangle may not become a needle (height under 1/50 of
    /// the longest side) *and* worse than the worst one was, and no edge may get longer than `max_len` unless it only
    /// replaces one about as long. Judging the neighbourhood (not each triangle) matters where the source already has
    /// needles, such as the fans of a UV sphere's poles: every collapse there makes some triangle a bit worse, and
    /// refusing them all would leave those regions undecimated.
    fn collapse_ok(&self, a: u32, b: u32, to: V3, max_len: f32) -> bool {
        let max2 = max_len * max_len;
        let (mut worst_old, mut worst_new) = (f32::MAX, f32::MAX);
        let (mut max_old2, mut max_new2) = (0f32, 0f32);
        let moved = to != self.p[a as usize];
        for v in [b, a] {
            if v == a && !moved {
                continue; // a stays where it is: its other triangles do not change
            }
            for &t in &self.vt[v as usize] {
                let tri = self.t[t as usize];
                if has(tri, a) && has(tri, b) {
                    continue;
                }
                let mut q = [V3::ZERO; 3];
                for k in 0..3 {
                    q[k] = if tri[k] == a || tri[k] == b { to } else { self.p[tri[k] as usize] };
                }
                let n_new = (q[1] - q[0]).cross(q[2] - q[0]);
                let n_old = self.tri_normal(tri);
                let (ln, lo) = (n_new.length(), n_old.length());
                if ln <= 1e-12 * (lo + 1e-30) || ln <= 0.0 {
                    return false;
                }
                if n_new.dot(n_old) < 0.2 * ln * lo {
                    return false;
                }
                let (mut longest, mut longest_old) = (0f32, 0f32);
                for k in 0..3 {
                    longest = longest.max((q[(k + 1) % 3] - q[k]).length());
                    longest_old = longest_old.max((self.p[tri[(k + 1) % 3] as usize] - self.p[tri[k] as usize]).length());
                }
                worst_new = worst_new.min(ln / (longest * longest).max(1e-30));
                worst_old = worst_old.min(lo / (longest_old * longest_old).max(1e-30));
                if max_len > 0.0 {
                    for k in 0..3 {
                        if tri[k] == a || tri[k] == b {
                            for j in 1..3 {
                                max_new2 = max_new2.max((q[(k + j) % 3] - to).length2());
                                max_old2 = max_old2.max((self.p[tri[(k + j) % 3] as usize] - self.p[tri[k] as usize]).length2());
                            }
                        }
                    }
                }
            }
        }
        if worst_new < 0.02 && worst_new < worst_old {
            return false;
        }
        !(max_len > 0.0 && max_new2 > max2 && max_new2 > 1.21 * max_old2)
    }

    /// Collapses b into a, a moving to `to`. `max_len`: the longest edge the collapse may make.
    #[allow(clippy::too_many_arguments)]
    fn collapse(&mut self, b: u32, a: u32, to: V3, max_len: f32, ra: &mut Vec<u32>, rb: &mut Vec<u32>, scratch: &mut Vec<u32>) -> bool {
        let mut ends = [0u32; 2];
        let kb = self.kind(b, &mut ends, scratch);
        if kb == 2 {
            return false;
        }
        if kb == 1 && !self.feature_edge(a, b) {
            return false;
        }
        let mut et = [0u32; 4];
        let ne = self.edge_tris(a, b, &mut et);
        if !(1..=2).contains(&ne) {
            return false;
        }
        self.ring(a, ra);
        self.ring(b, rb);
        if ra.len() <= 3 && rb.len() <= 3 {
            return false; // a tetrahedron
        }
        let common = rb.iter().filter(|x| ra.binary_search(x).is_ok()).count();
        if common != ne {
            return false; // link condition: the collapse would pinch the surface
        }
        if !self.collapse_ok(a, b, to, max_len) {
            return false;
        }
        // Do it.
        let vtb = std::mem::take(&mut self.vt[b as usize]);
        for &t in &vtb {
            let tri = self.t[t as usize];
            if has(tri, a) {
                self.dead_t[t as usize] = true;
                for &x in &tri {
                    if x != a && x != b {
                        self.vt[x as usize].retain(|&y| y != t);
                    }
                }
                continue;
            }
            for k in 0..3 {
                if self.t[t as usize][k] == b {
                    self.t[t as usize][k] = a;
                }
            }
            self.vt[a as usize].push(t);
        }
        {
            let dead = &self.dead_t;
            self.vt[a as usize].retain(|&t| !dead[t as usize]);
        }
        self.dead_v[b as usize] = true;
        self.p[a as usize] = to;
        self.lt[a as usize] = self.lt[a as usize].min(self.lt[b as usize]);
        if !self.sharp.is_empty() {
            for &x in rb.iter() {
                if self.sharp.remove(&edge_key(b, x)) && x != a {
                    self.sharp.insert(edge_key(a, x));
                }
            }
        }
        true
    }

    /// Tries to collapse the edge (a, b) whichever way the feature rules allow; the survivor, or NONE.
    fn collapse_edge(&mut self, a: u32, b: u32, max_len: f32, ra: &mut Vec<u32>, rb: &mut Vec<u32>, scratch: &mut Vec<u32>) -> u32 {
        let mut ends = [0u32; 2];
        let ka = self.kind(a, &mut ends, scratch);
        let kbb = self.kind(b, &mut ends, scratch);
        if ka == 0 && kbb == 0 {
            let mid = (self.p[a as usize] + self.p[b as usize]) * 0.5;
            return if self.collapse(b, a, mid, max_len, ra, rb, scratch) { a } else { NONE };
        }
        if ka >= kbb {
            let pa = self.p[a as usize];
            if self.collapse(b, a, pa, max_len, ra, rb, scratch) {
                return a;
            }
            let pb = self.p[b as usize];
            if ka != 2 && self.collapse(a, b, pb, max_len, ra, rb, scratch) {
                return b;
            }
        } else {
            let pb = self.p[b as usize];
            if self.collapse(a, b, pb, max_len, ra, rb, scratch) {
                return b;
            }
        }
        NONE
    }

    /// Edges (a < b) with length in [lo, hi) (times the per-vertex target when `scaled`), found in parallel blocks.
    fn edges(&self, lo: f32, hi: f32, scaled: bool) -> Vec<Cand> {
        let nv = self.p.len();
        const BLOCK: usize = 4096;
        let parts: Vec<Vec<Cand>> = (0..nv.div_ceil(BLOCK))
            .into_par_iter()
            .map(|blk| {
                let mut out = Vec::new();
                let mut r = Vec::new();
                for v in blk * BLOCK..nv.min((blk + 1) * BLOCK) {
                    if self.dead_v[v] {
                        continue;
                    }
                    self.ring(v as u32, &mut r);
                    for &u in &r {
                        if u as usize <= v {
                            continue;
                        }
                        let l2 = (self.p[u as usize] - self.p[v]).length2();
                        let s = if scaled { 0.5 * (self.lt[v] + self.lt[u as usize]) } else { 1.0 };
                        if l2 >= lo * lo * s * s && l2 < hi * hi * s * s {
                            out.push(Cand { len2: l2 / (s * s), a: v as u32, b: u });
                        }
                    }
                }
                out
            })
            .collect();
        parts.into_iter().flatten().collect()
    }

    fn project_moved(&mut self, moved: &mut Vec<u32>) {
        moved.sort_unstable();
        moved.dedup();
        let new: Vec<V3> = moved
            .par_iter()
            .map(|&v| if self.dead_v[v as usize] { self.p[v as usize] } else { self.project_surface(self.p[v as usize]) })
            .collect();
        for (&v, q) in moved.iter().zip(new) {
            self.p[v as usize] = q;
        }
        moved.clear();
    }

    /// Edge collapses down to edges of about `edge` (the dense source to the proxy's density).
    pub fn decimate(&mut self, edge: f32) {
        self.reach = 3.0 * edge;
        let (mut ra, mut rb, mut scratch, mut moved) = (Vec::new(), Vec::new(), Vec::new(), Vec::new());
        let mut touched = vec![false; self.p.len()];
        let timing = std::env::var_os("RR_TIMING").is_some();
        for pass in 0..60 {
            let t_pass = std::time::Instant::now();
            let mut c = self.edges(0.0, 0.8 * edge, false);
            let t_edges = t_pass.elapsed().as_secs_f64();
            if c.is_empty() {
                break;
            }
            c.sort_by(asc);
            touched.iter_mut().for_each(|x| *x = false);
            let mut done = 0usize;
            for e in &c {
                if self.dead_v[e.a as usize] || self.dead_v[e.b as usize] || touched[e.a as usize] || touched[e.b as usize] {
                    continue;
                }
                let s = self.collapse_edge(e.a, e.b, 1.25 * edge, &mut ra, &mut rb, &mut scratch);
                if s == NONE {
                    continue;
                }
                done += 1;
                moved.push(s);
                touched[s as usize] = true;
                self.ring(s, &mut ra);
                for &u in &ra {
                    touched[u as usize] = true;
                }
            }
            let t_loop = t_pass.elapsed().as_secs_f64();
            self.project_moved(&mut moved);
            if timing {
                let alive = self.dead_v.iter().filter(|d| !**d).count();
                eprintln!(
                    "    decimate pass {pass:2}: {:7} candidates, {:7} collapsed, {alive} vertices; edges {:.2}s loop {:.2}s project {:.2}s",
                    c.len(), done, t_edges, t_loop - t_edges, t_pass.elapsed().as_secs_f64() - t_loop
                );
            }
            if done == 0 || done < c.len() / 400 {
                break;
            }
        }
    }

    /// Splits the edge (a, b) at its middle; the new vertex, or NONE.
    fn split(&mut self, a: u32, b: u32) -> u32 {
        let mut et = [0u32; 4];
        let ne = self.edge_tris(a, b, &mut et);
        if !(1..=2).contains(&ne) {
            return NONE;
        }
        let feature = self.feature_edge(a, b);
        let m = self.p.len() as u32;
        let mid = (self.p[a as usize] + self.p[b as usize]) * 0.5;
        let q = if feature { self.project_feature(mid) } else { self.project_surface(mid) };
        self.p.push(q);
        self.dead_v.push(false);
        self.vt.push(Vec::new());
        self.lt.push(0.5 * (self.lt[a as usize] + self.lt[b as usize]));
        if !self.locked.is_empty() {
            self.locked.push(false);
        }
        for &t in et.iter().take(ne) {
            let mut tri = self.t[t as usize];
            // Rotate so the edge is (tri[0], tri[1]).
            for _ in 0..3 {
                if (tri[0] == a && tri[1] == b) || (tri[0] == b && tri[1] == a) {
                    break;
                }
                tri = [tri[1], tri[2], tri[0]];
            }
            let (x, y, z) = (tri[0], tri[1], tri[2]);
            self.t[t as usize] = [x, m, z];
            let n = self.t.len() as u32;
            self.t.push([m, y, z]);
            let gt = self.g[t as usize];
            self.g.push(gt);
            self.dead_t.push(false);
            for e in self.vt[y as usize].iter_mut() {
                if *e == t {
                    *e = n;
                }
            }
            self.vt[m as usize].push(t);
            self.vt[m as usize].push(n);
            self.vt[z as usize].push(n);
        }
        if !self.sharp.is_empty() && self.sharp.remove(&edge_key(a, b)) {
            self.sharp.insert(edge_key(a, m));
            self.sharp.insert(edge_key(m, b));
        }
        m
    }

    fn split_long(&mut self) {
        let mut c = self.edges(4.0 / 3.0, 1e30, true);
        c.sort_by(desc);
        for e in &c {
            let mut et = [0u32; 4];
            if self.edge_tris(e.a, e.b, &mut et) == 0 {
                continue; // gone (split already from the other side)
            }
            self.split(e.a, e.b);
        }
    }

    fn collapse_short(&mut self) {
        let mut c = self.edges(0.0, 0.8, true);
        c.sort_by(asc);
        let (mut ra, mut rb, mut scratch, mut moved) = (Vec::new(), Vec::new(), Vec::new(), Vec::new());
        for e in &c {
            if self.dead_v[e.a as usize] || self.dead_v[e.b as usize] {
                continue;
            }
            let s = 0.5 * (self.lt[e.a as usize] + self.lt[e.b as usize]);
            let l2 = (self.p[e.a as usize] - self.p[e.b as usize]).length2();
            if l2 >= 0.64 * s * s {
                continue;
            }
            let mut et = [0u32; 4];
            if self.edge_tris(e.a, e.b, &mut et) == 0 {
                continue;
            }
            let sv = self.collapse_edge(e.a, e.b, 4.0 / 3.0 * s, &mut ra, &mut rb, &mut scratch);
            if sv != NONE {
                moved.push(sv);
            }
        }
        self.project_moved(&mut moved);
    }

    fn flip_edges(&mut self) {
        let nv = self.p.len();
        let mut val = vec![0i32; nv];
        let mut target = vec![6i32; nv];
        {
            let (mut r, mut scratch) = (Vec::new(), Vec::new());
            let mut ends = [0u32; 2];
            for v in 0..nv as u32 {
                if self.dead_v[v as usize] {
                    continue;
                }
                self.ring(v, &mut r);
                val[v as usize] = r.len() as i32;
                if self.kind(v, &mut ends, &mut scratch) != 0 {
                    target[v as usize] = 4;
                }
            }
        }
        let c = self.edges(0.0, 1e30, false);
        for e in &c {
            let (a, b) = (e.a, e.b);
            let mut et = [0u32; 4];
            if self.edge_tris(a, b, &mut et) != 2 || self.feature_edge(a, b) {
                continue;
            }
            let (mut t1, mut t2) = (self.t[et[0] as usize], self.t[et[1] as usize]);
            if !forwards(t1, a, b) {
                std::mem::swap(&mut t1, &mut t2);
                et.swap(0, 1);
            }
            if !forwards(t1, a, b) || !forwards(t2, b, a) {
                continue;
            }
            let (mut c1, mut d1) = (NONE, NONE);
            for k in 0..3 {
                if t1[k] != a && t1[k] != b {
                    c1 = t1[k];
                }
                if t2[k] != a && t2[k] != b {
                    d1 = t2[k];
                }
            }
            if c1 == NONE || d1 == NONE || c1 == d1 {
                continue;
            }
            if val[a as usize] <= 3 || val[b as usize] <= 3 {
                continue;
            }
            let dev = |v: u32, dv: i32| (val[v as usize] + dv - target[v as usize]).abs();
            let before = dev(a, 0) + dev(b, 0) + dev(c1, 0) + dev(d1, 0);
            let after = dev(a, -1) + dev(b, -1) + dev(c1, 1) + dev(d1, 1);
            if after >= before {
                continue;
            }
            let mut tmp = [0u32; 4];
            if self.edge_tris(c1, d1, &mut tmp) > 0 {
                continue; // c-d must not be an edge already
            }
            let (n1, n2) = (self.tri_normal(t1), self.tri_normal(t2));
            let n_old = n1.normalized() + n2.normalized();
            if n1.normalized().dot(n2.normalized()) < 0.85 {
                continue; // not across a fold
            }
            let (u1, u2) = ([a, d1, c1], [d1, b, c1]);
            let (m1, m2) = (self.tri_normal(u1), self.tri_normal(u2));
            if m1.dot(n_old) <= 0.0 || m2.dot(n_old) <= 0.0 {
                continue;
            }
            if m1.normalized().dot(m2.normalized()) < 0.85 {
                continue;
            }
            self.t[et[0] as usize] = u1;
            self.t[et[1] as usize] = u2;
            // vt: a loses et[1], b loses et[0], c gains et[1], d gains et[0].
            self.vt[a as usize].retain(|&x| x != et[1]);
            self.vt[b as usize].retain(|&x| x != et[0]);
            self.vt[c1 as usize].push(et[1]);
            self.vt[d1 as usize].push(et[0]);
            val[a as usize] -= 1;
            val[b as usize] -= 1;
            val[c1 as usize] += 1;
            val[d1 as usize] += 1;
        }
    }

    fn smooth(&mut self, rounds: usize) {
        let nv = self.p.len();
        let kinds: Vec<(u8, [u32; 2])> = (0..nv)
            .into_par_iter()
            .map_init(Vec::new, |scratch, v| {
                if self.dead_v[v] {
                    return (0, [NONE; 2]);
                }
                let mut ends = [NONE; 2];
                let k = self.kind(v as u32, &mut ends, scratch);
                (k, if k == 1 { ends } else { [NONE; 2] })
            })
            .collect();
        for _ in 0..rounds {
            let normals: Vec<V3> = (0..nv)
                .into_par_iter()
                .map(|v| {
                    let mut n = V3::ZERO;
                    for &t in &self.vt[v] {
                        n += self.tri_normal(self.t[t as usize]);
                    }
                    n.normalized()
                })
                .collect();
            let next: Vec<V3> = (0..nv)
                .into_par_iter()
                .map_init(Vec::new, |rg, v| {
                    let pv = self.p[v];
                    let (k, ends) = kinds[v];
                    if self.dead_v[v] || k == 2 {
                        return pv;
                    }
                    if k == 1 {
                        let c = (self.p[ends[0] as usize] + self.p[ends[1] as usize]) * 0.5;
                        return self.project_feature(pv + (c - pv) * 0.5);
                    }
                    self.ring(v as u32, rg);
                    if rg.is_empty() {
                        return pv;
                    }
                    // Weighted towards the target sizes: a neighbour wanting small edges pulls less.
                    let mut c = V3::ZERO;
                    let mut w = 0f32;
                    for &u in rg.iter() {
                        let wu = self.lt[u as usize];
                        c += self.p[u as usize] * wu;
                        w += wu;
                    }
                    c /= w;
                    let mut d = c - pv;
                    d -= normals[v] * normals[v].dot(d);
                    self.project_surface(pv + d * 0.5)
                })
                .collect();
            // Keep a move only if no triangle around turns over.
            let kept: Vec<V3> = (0..nv)
                .into_par_iter()
                .map(|v| {
                    if self.dead_v[v] || next[v] == self.p[v] {
                        return self.p[v];
                    }
                    for &t in &self.vt[v] {
                        let tri = self.t[t as usize];
                        let mut q = [V3::ZERO; 3];
                        for k in 0..3 {
                            q[k] = if tri[k] as usize == v { next[v] } else { self.p[tri[k] as usize] };
                        }
                        let nn = (q[1] - q[0]).cross(q[2] - q[0]);
                        if nn.dot(self.tri_normal(tri)) <= 0.0 {
                            return self.p[v];
                        }
                    }
                    next[v]
                })
                .collect();
            self.p = kept;
        }
    }

    /// Isotropic remeshing passes (split, collapse, flip, smooth onto the source) towards `edge` times each vertex's
    /// relative size (`rel`: per current vertex, empty/short = 1).
    pub fn remesh(&mut self, edge: f32, rel: &[f32], iterations: usize) {
        self.reach = 3.0 * edge;
        for v in 0..self.p.len() {
            self.lt[v] = edge * rel.get(v).copied().unwrap_or(1.0);
        }
        for _ in 0..iterations {
            self.split_long();
            self.collapse_short();
            self.flip_edges();
            self.smooth(2);
        }
    }

    /// The current mesh, compacted, and for each proxy vertex the builder vertex it came from.
    pub fn result(&self) -> (Proxy, Vec<u32>) {
        let mut p = Proxy::default();
        let mut vmap = Vec::new();
        let mut remap = vec![NONE; self.p.len()];
        for t in 0..self.t.len() {
            if self.dead_t[t] {
                continue;
            }
            let tri = self.t[t];
            for &v in &tri {
                if remap[v as usize] == NONE {
                    remap[v as usize] = p.p.len() as u32;
                    p.p.push(self.p[v as usize]);
                    vmap.push(v);
                }
            }
            p.t.push([remap[tri[0] as usize], remap[tri[1] as usize], remap[tri[2] as usize]]);
            p.group.push(self.g[t]);
            for k in 0..3 {
                let (a, b) = (tri[k], tri[(k + 1) % 3]);
                p.feature.push(self.feature_edge(a, b) as u8);
                p.sharp.push((!self.sharp.is_empty() && self.sharp.contains(&edge_key(a, b))) as u8);
            }
        }
        p.build_opposites();
        for c in 0..p.opp.len() {
            if p.opp[c] == NONE {
                p.feature[c] = 1;
            }
        }
        p.size = vec![1.0; p.p.len()];
        (p, vmap)
    }
}

/// Dense sources: the piece is cut into spatial blocks, each decimated towards `edge` on its own thread (vertices
/// shared between blocks stay), then merged. The builder's own pass afterwards finishes the strips along the block
/// borders. The hard edges come back in the result's vertex numbers.
pub fn pre_decimate(
    src: &WorkMesh,
    surface: &TriangleBvh,
    features: Option<&SegmentBvh>,
    sharp: &HashSet<u64>,
    keep_groups: bool,
    edge: f32,
) -> (WorkMesh, HashSet<u64>) {
    let (nt, nv) = (src.t.len(), src.p.len());
    // Spatial blocks: recursive median splits of the triangle centres along the longest axis.
    const LEVELS: usize = 6; // 64 blocks
    let centre: Vec<V3> = src
        .t
        .par_iter()
        .map(|t| (src.p[t[0] as usize] + src.p[t[1] as usize] + src.p[t[2] as usize]) / 3.0)
        .collect();
    let mut order: Vec<u32> = (0..nt as u32).collect();
    let mut ranges: Vec<(usize, usize)> = vec![(0, nt)];
    for _ in 0..LEVELS {
        let mut next = Vec::with_capacity(ranges.len() * 2);
        for &(b, e) in &ranges {
            if e - b < 2 {
                next.push((b, e));
                continue;
            }
            let (mut lo, mut hi) = (V3::splat(1e30), V3::splat(-1e30));
            for &i in &order[b..e] {
                lo = lo.min(centre[i as usize]);
                hi = hi.max(centre[i as usize]);
            }
            let ext = hi - lo;
            let axis = if ext.x >= ext.y && ext.x >= ext.z {
                0
            } else if ext.y >= ext.z {
                1
            } else {
                2
            };
            let mid = (b + e) / 2;
            order[b..e].select_nth_unstable_by(mid - b, |&x, &y| centre[x as usize].get(axis).total_cmp(&centre[y as usize].get(axis)));
            next.push((b, mid));
            next.push((mid, e));
        }
        ranges = next;
    }
    let mut block_of = vec![0u32; nt];
    for (k, &(b, e)) in ranges.iter().enumerate() {
        for &i in &order[b..e] {
            block_of[i as usize] = k as u32;
        }
    }
    // Vertices used by more than one block stay.
    let mut vblock = vec![NONE; nv];
    let mut shared = vec![false; nv];
    for (t, tri) in src.t.iter().enumerate() {
        for &v in tri {
            let b = &mut vblock[v as usize];
            if *b == NONE {
                *b = block_of[t];
            } else if *b != block_of[t] {
                shared[v as usize] = true;
            }
        }
    }

    struct Part {
        g: Vec<u32>, // local -> global vertex
        p: Vec<V3>,
        t: Vec<[u32; 3]>,
        grp: Vec<u32>,
        sharp: Vec<u64>,
    }
    let parts: Vec<Part> = ranges
        .par_iter()
        .map(|&(b, e)| {
            let mut local = WorkMesh::default();
            let mut l: HashMap<u32, u32> = HashMap::new();
            let mut g: Vec<u32> = Vec::new();
            let mut id = |v: u32, local: &mut WorkMesh| -> u32 {
                *l.entry(v).or_insert_with(|| {
                    g.push(v);
                    local.p.push(src.p[v as usize]);
                    g.len() as u32 - 1
                })
            };
            for &t in &order[b..e] {
                let tri = src.t[t as usize];
                let nt3 = [id(tri[0], &mut local), id(tri[1], &mut local), id(tri[2], &mut local)];
                local.t.push(nt3);
                local.group.push(src.group.get(t as usize).copied().unwrap_or(0));
            }
            let mut ls: HashSet<u64> = HashSet::new();
            for &key in sharp {
                let (ka, kb) = key_ends(key);
                if let (Some(&x), Some(&y)) = (l.get(&ka), l.get(&kb)) {
                    ls.insert(edge_key(x, y));
                }
            }
            let mut builder = ProxyBuilder::new(&local, surface, features, ls, keep_groups);
            builder.locked = g.iter().map(|&v| shared[v as usize]).collect();
            builder.decimate(edge);
            let mut part = Part { g: g.clone(), p: builder.p.clone(), t: Vec::new(), grp: Vec::new(), sharp: Vec::new() };
            for t in 0..builder.t.len() {
                if !builder.dead_t[t] {
                    let tr = builder.t[t];
                    part.t.push([g[tr[0] as usize], g[tr[1] as usize], g[tr[2] as usize]]);
                    part.grp.push(builder.g[t]);
                }
            }
            for &key in &builder.sharp {
                let (x, y) = key_ends(key);
                part.sharp.push(edge_key(g[x as usize], g[y as usize]));
            }
            part
        })
        .collect();

    // Merge (surviving vertices keep their global ids, moved by midpoint collapses), then compact.
    let mut pos = src.p.clone();
    for part in &parts {
        for (v, &gv) in part.g.iter().enumerate() {
            pos[gv as usize] = part.p[v];
        }
    }
    let mut merged = WorkMesh::default();
    let mut remap = vec![NONE; nv];
    for part in &parts {
        for (i, tri) in part.t.iter().enumerate() {
            let mut out = [0u32; 3];
            for k in 0..3 {
                let r = &mut remap[tri[k] as usize];
                if *r == NONE {
                    *r = merged.p.len() as u32;
                    merged.p.push(pos[tri[k] as usize]);
                }
                out[k] = *r;
            }
            merged.t.push(out);
            merged.group.push(part.grp[i]);
        }
    }
    let mut sharp_out = HashSet::new();
    for part in &parts {
        for &key in &part.sharp {
            let (x, y) = key_ends(key);
            let (a, b) = (remap[x as usize], remap[y as usize]);
            if a != NONE && b != NONE {
                sharp_out.insert(edge_key(a, b));
            }
        }
    }
    if !src.color.is_empty() {
        merged.color = vec![v3(1.0, 1.0, 1.0); merged.p.len()];
        for (v, &r) in remap.iter().enumerate() {
            if r != NONE {
                merged.color[r as usize] = src.color[v];
            }
        }
    }
    (merged, sharp_out)
}

/// Edge length of the proxy: about `DENSITY` proxy edges per quad edge (`h0` = sqrt(area / wanted quads)), but never
/// finer than the `MAX_PROXY_TRIANGLES` cap allows.
pub fn proxy_edge(area: f64, h0: f32) -> f32 {
    let min_edge = (area / (0.433 * MAX_PROXY_TRIANGLES)).sqrt() as f32;
    (h0 / DENSITY).max(min_edge)
}

/// Builds the proxy: pre-decimation for dense sources, decimation to `edge`, then two isotropic remeshing passes.
/// Returns the builder too (so the adaptive pass can remesh again with per-vertex sizes), the proxy and the builder
/// vertex of each proxy vertex.
pub fn build_proxy<'a>(
    src: &WorkMesh,
    surface: &'a TriangleBvh,
    features: Option<&'a SegmentBvh>,
    sharp: HashSet<u64>,
    keep_groups: bool,
    edge: f32,
    area: f64,
) -> (ProxyBuilder<'a>, Proxy, Vec<u32>) {
    let proxy_tris = area / (0.433 * edge as f64 * edge as f64);
    let t0 = std::time::Instant::now();
    let timing = std::env::var_os("RR_TIMING").is_some();
    let mark = |what: &str| {
        if timing {
            eprintln!("  proxy {:<14} {:7.3} s", what, t0.elapsed().as_secs_f64());
        }
    };
    let mut builder = if src.t.len() as f64 > 8.0 * proxy_tris && src.t.len() > 300_000 {
        let (pre, pre_sharp) = pre_decimate(src, surface, features, &sharp, keep_groups, edge);
        ProxyBuilder::new(&pre, surface, features, pre_sharp, keep_groups)
    } else {
        ProxyBuilder::new(src, surface, features, sharp, keep_groups)
    };
    mark("pre-decimate");
    builder.decimate(edge);
    mark("decimate");
    builder.remesh(edge, &[], 2);
    mark("remesh");
    let (proxy, vmap) = builder.result();
    mark("result");
    (builder, proxy, vmap)
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::testmesh::{grid_cube, grid_plane, icosphere, uv_sphere, volume};
    use crate::topology::analyze;
    use crate::workmesh::{detect_features, mesh_edges};

    fn surface_of(m: &WorkMesh) -> TriangleBvh {
        TriangleBvh::build(m.p.clone(), m.t.clone())
    }

    fn as_work(p: &Proxy) -> WorkMesh {
        WorkMesh { p: p.p.clone(), t: p.t.clone(), group: p.group.clone(), color: Vec::new() }
    }

    /// (mean edge length, share of vertices with six neighbours)
    fn quality(p: &Proxy) -> (f32, f32) {
        let edges = mesh_edges(&p.t);
        let mean = edges.iter().map(|e| (p.p[e.a as usize] - p.p[e.b as usize]).length()).sum::<f32>() / edges.len() as f32;
        let mut val = vec![0u32; p.p.len()];
        for e in &edges {
            val[e.a as usize] += 1;
            val[e.b as usize] += 1;
        }
        let six = val.iter().filter(|&&v| v == 6).count() as f32 / p.p.len() as f32;
        (mean, six)
    }

    #[test]
    fn sphere_decimates_and_stays_a_clean_sphere() {
        let src = icosphere(5); // 20480 triangles
        let surface = surface_of(&src);
        let edge = 0.12;
        let mut b = ProxyBuilder::new(&src, &surface, None, HashSet::new(), false);
        b.decimate(edge);
        b.remesh(edge, &[], 3);
        let (proxy, vmap) = b.result();
        let work = as_work(&proxy);
        let st = analyze(&proxy.to_mesh());
        assert!(st.is_closed_manifold() && st.euler == 2 && st.components == 1, "{st:?}");
        // about area / (0.433 edge^2) triangles
        let want = src.area() / (0.433 * (edge as f64).powi(2));
        assert!(proxy.t.len() as f64 > 0.6 * want && (proxy.t.len() as f64) < 1.6 * want, "{} triangles, wanted about {want:.0}", proxy.t.len());
        assert_eq!(vmap.len(), proxy.p.len());
        assert!(volume(&work) > 0.0, "turned inside out");
        let worst = proxy.p.iter().map(|p| (p.length() - 1.0).abs()).fold(0f32, f32::max);
        assert!(worst < 5e-3, "vertex {worst} off the sphere");
        let (mean, six) = quality(&proxy);
        println!("sphere proxy: {} tris, mean edge {:.4} (target {edge}), valence-6 {:.0}%", proxy.t.len(), mean, six * 100.0);
        assert!(mean > 0.8 * edge && mean < 1.35 * edge, "mean edge {mean}");
        assert!(six > 0.6, "only {:.0}% regular vertices", six * 100.0);
        // no open edges, none marked as features
        assert!(proxy.opp.iter().all(|&o| o != NONE));
        assert!(proxy.feature.iter().all(|&f| f == 0));
    }

    #[test]
    fn result_is_deterministic() {
        let src = icosphere(4);
        let surface = surface_of(&src);
        let run = || {
            let mut b = ProxyBuilder::new(&src, &surface, None, HashSet::new(), false);
            b.decimate(0.15);
            b.remesh(0.15, &[], 2);
            b.result().0
        };
        let (a, b) = (run(), run());
        assert_eq!(a.t, b.t);
        assert_eq!(a.p, b.p);
    }

    #[test]
    fn hard_edges_and_corners_of_a_cube_survive() {
        let src = grid_cube(10);
        let surface = surface_of(&src);
        let f = detect_features(&src, false, (60f32).to_radians().cos(), &[], 0.2);
        assert_eq!(f.sharp.len(), 12 * 10);
        let segs = SegmentBvh::build(f.seg_a.clone(), f.seg_b.clone());
        let edge = 0.4;
        let (_b, proxy, _) = build_proxy(&src, &surface, Some(&segs), f.sharp.clone(), false, edge, src.area());
        let st = analyze(&proxy.to_mesh());
        assert!(st.is_closed_manifold() && st.euler == 2, "{st:?}");
        assert!(proxy.t.len() < src.t.len() / 2, "{} triangles", proxy.t.len());
        // the cube's shape is intact: volume and area
        let work = as_work(&proxy);
        assert!((volume(&work) - 8.0).abs() < 0.05, "volume {}", volume(&work));
        assert!((work.area() - 24.0).abs() < 0.2, "area {}", work.area());
        // every corner is still a vertex
        for sx in [-1.0f32, 1.0] {
            for sy in [-1.0f32, 1.0] {
                for sz in [-1.0f32, 1.0] {
                    let c = v3(sx, sy, sz);
                    assert!(proxy.p.iter().any(|p| (*p - c).length() < 1e-4), "corner {c:?} lost");
                }
            }
        }
        // hard-edge corners lie on a cube edge (two coordinates at +-1), and there are some
        let mut on_edge = 0;
        for (c, &s) in proxy.sharp.iter().enumerate() {
            if s == 0 {
                continue;
            }
            on_edge += 1;
            let (a, b) = (proxy.t[c / 3][c % 3], proxy.t[c / 3][(c % 3 + 1) % 3]);
            for q in [proxy.p[a as usize], proxy.p[b as usize]] {
                let fixed = [q.x, q.y, q.z].iter().filter(|x| (x.abs() - 1.0).abs() < 1e-3).count();
                assert!(fixed >= 2, "hard-edge vertex {q:?} left the cube edge");
            }
            assert_eq!(proxy.feature[c], 1);
        }
        assert!(on_edge >= 12 * 2, "only {on_edge} hard-edge corners");
    }

    #[test]
    fn open_borders_are_kept_on_the_border() {
        let src = grid_plane(30);
        let surface = surface_of(&src);
        let f = detect_features(&src, false, 2.0, &[], 0.1);
        let segs = SegmentBvh::build(f.seg_a.clone(), f.seg_b.clone());
        let edge = 0.12;
        let (_b, proxy, _) = build_proxy(&src, &surface, Some(&segs), f.sharp, false, edge, src.area());
        assert!(proxy.t.len() < src.t.len() / 4);
        for p in &proxy.p {
            assert!(p.z.abs() < 1e-5 && p.x > -1e-5 && p.x < 1.0 + 1e-5 && p.y > -1e-5 && p.y < 1.0 + 1e-5, "{p:?} left the square");
        }
        assert!((as_work(&proxy).area() - 1.0).abs() < 1e-3, "area {}", as_work(&proxy).area());
        // corners of the square survive; every open corner is flagged as a feature
        for c in [v3(0., 0., 0.), v3(1., 0., 0.), v3(0., 1., 0.), v3(1., 1., 0.)] {
            assert!(proxy.p.iter().any(|p| (*p - c).length() < 1e-4), "corner {c:?} lost");
        }
        let mut open = 0;
        for c in 0..proxy.opp.len() {
            if proxy.opp[c] == NONE {
                open += 1;
                assert_eq!(proxy.feature[c], 1);
                let (a, b) = (proxy.t[c / 3][c % 3], proxy.t[c / 3][(c % 3 + 1) % 3]);
                for q in [proxy.p[a as usize], proxy.p[b as usize]] {
                    let on = q.x.abs() < 1e-4 || (q.x - 1.0).abs() < 1e-4 || q.y.abs() < 1e-4 || (q.y - 1.0).abs() < 1e-4;
                    assert!(on, "open-edge vertex {q:?} is not on the border");
                }
            }
        }
        assert!(open >= 4 * 4, "{open} open edges");
        assert_eq!(analyze(&proxy.to_mesh()).inconsistent_edges, 0);
    }

    #[test]
    fn sizes_scale_the_remesh_per_vertex() {
        let src = icosphere(4);
        let surface = surface_of(&src);
        let mut b = ProxyBuilder::new(&src, &surface, None, HashSet::new(), false);
        b.decimate(0.2);
        b.remesh(0.2, &[], 2);
        // vertices on the +x half want edges half as long
        let (p0, vmap) = b.result();
        let mut rel = vec![1.0f32; b.vertex_count()];
        for (v, &bv) in vmap.iter().enumerate() {
            if p0.p[v].x > 0.0 {
                rel[bv as usize] = 0.5;
            }
        }
        b.remesh(0.2, &rel, 4);
        let (p1, _) = b.result();
        let count = |p: &Proxy, right: bool| p.t.iter().filter(|t| ((p.p[t[0] as usize].x + p.p[t[1] as usize].x + p.p[t[2] as usize].x) > 0.0) == right).count();
        let (r, l) = (count(&p1, true), count(&p1, false));
        println!("triangles right {r}, left {l}");
        assert!(r as f32 > 2.0 * l as f32, "right {r}, left {l}");
        let st = analyze(&p1.to_mesh());
        assert!(st.is_closed_manifold() && st.euler == 2, "{st:?}");
    }

    #[test]
    fn pre_decimation_in_blocks_keeps_the_sphere_closed() {
        let src = icosphere(6); // 81920 triangles
        let surface = surface_of(&src);
        let edge = 0.1;
        let (pre, _) = pre_decimate(&src, &surface, None, &HashSet::new(), false, edge);
        assert!(pre.t.len() < src.t.len() / 6, "{} triangles", pre.t.len());
        let mut b = ProxyBuilder::new(&pre, &surface, None, HashSet::new(), false);
        b.decimate(edge);
        b.remesh(edge, &[], 2);
        let (proxy, _) = b.result();
        let st = analyze(&proxy.to_mesh());
        assert!(st.is_closed_manifold() && st.euler == 2 && st.components == 1, "{st:?}");
        let worst = proxy.p.iter().map(|p| (p.length() - 1.0).abs()).fold(0f32, f32::max);
        assert!(worst < 5e-3, "vertex {worst} off the sphere");
        assert!(volume(&as_work(&proxy)) > 0.0);
    }

    #[test]
    fn pre_decimation_carries_hard_edges() {
        let src = grid_cube(40); // 19200 triangles
        let surface = surface_of(&src);
        let f = detect_features(&src, false, (60f32).to_radians().cos(), &[], 0.1);
        let segs = SegmentBvh::build(f.seg_a.clone(), f.seg_b.clone());
        let (pre, sharp) = pre_decimate(&src, &surface, Some(&segs), &f.sharp, false, 0.15);
        assert!(pre.t.len() < src.t.len() / 3);
        assert!(!sharp.is_empty());
        for &key in &sharp {
            let (a, b) = key_ends(key);
            for q in [pre.p[a as usize], pre.p[b as usize]] {
                let fixed = [q.x, q.y, q.z].iter().filter(|x| (x.abs() - 1.0).abs() < 1e-3).count();
                assert!(fixed >= 2, "{q:?}");
            }
        }
    }

    #[test]
    fn poles_of_very_high_valence_are_handled() {
        // 300 triangles meet at each pole: feature queries there used to be quadratic in the valence
        let src = uv_sphere(60, 300);
        assert!(analyze(&src.to_mesh()).is_closed_manifold());
        assert!(volume(&src) > 0.0, "the generator must face outward");
        let surface = surface_of(&src);
        let (_b, proxy, _) = build_proxy(&src, &surface, None, HashSet::new(), false, 0.12, src.area());
        let st = analyze(&proxy.to_mesh());
        assert!(st.is_closed_manifold() && st.euler == 2 && st.components == 1, "{st:?}");
        assert!(volume(&as_work(&proxy)) > 0.0);
        let worst = proxy.p.iter().map(|p| (p.length() - 1.0).abs()).fold(0f32, f32::max);
        assert!(worst < 5e-3, "vertex {worst} off the sphere");
    }

    /// Regression: on a UV sphere the fans of triangles around the poles are needles, and the collapse checks used to
    /// judge each triangle alone, so nothing there could be decimated: 42% of the proxy's triangles were thinner than
    /// 20 degrees and it had twice the triangles it should (70% and 3.5x with 1000 segments).
    #[test]
    fn needle_fans_at_the_poles_are_decimated_into_even_triangles() {
        let src = uv_sphere(150, 600); // 178800 triangles, poles of valence 600
        let surface = surface_of(&src);
        let area = src.area();
        let edge = proxy_edge(area, (area / 300.0).sqrt() as f32);
        let (_b, proxy, _) = build_proxy(&src, &surface, None, HashSet::new(), false, edge, area);
        let q = proxy.quality(edge);
        let want = area / (0.433 * edge as f64 * edge as f64);
        println!("uv sphere proxy: {} triangles (about {want:.0} wanted), {:?}", proxy.t.len(), q);
        assert!(proxy.t.len() as f64 > 0.7 * want && (proxy.t.len() as f64) < 1.4 * want, "{} triangles, wanted about {want:.0}", proxy.t.len());
        assert!(q.thin_triangles < 0.01, "{:.1}% thin triangles", 100.0 * q.thin_triangles);
        assert!(q.mean_edge > 0.85 && q.mean_edge < 1.2, "mean edge {}", q.mean_edge);
        let st = analyze(&proxy.to_mesh());
        assert!(st.is_closed_manifold() && st.euler == 2, "{st:?}");
    }

    #[test]
    fn proxy_edge_follows_the_quad_size_with_a_cap() {
        assert!((proxy_edge(4.0, 0.3) - 0.1).abs() < 1e-6);
        let capped = proxy_edge(1000.0, 0.01);
        assert!(capped > 0.01 / DENSITY);
    }

    #[test]
    fn build_proxy_end_to_end_on_a_sphere() {
        let src = icosphere(5);
        let surface = surface_of(&src);
        let area = src.area();
        let h0 = (area / 500.0).sqrt() as f32;
        let edge = proxy_edge(area, h0);
        let (_b, proxy, _) = build_proxy(&src, &surface, None, HashSet::new(), false, edge, area);
        let st = analyze(&proxy.to_mesh());
        assert!(st.is_closed_manifold() && st.euler == 2, "{st:?}");
        // about three proxy edges per quad edge: ~ area / (0.433 edge^2) triangles
        let want = area / (0.433 * edge as f64 * edge as f64);
        let got = proxy.t.len() as f64;
        println!("proxy {got} triangles (about {want:.0} wanted)");
        assert!(got > 0.6 * want && got < 1.6 * want, "{got} vs {want}");
    }
}
