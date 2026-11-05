// SPDX-License-Identifier: GPL-3.0-or-later
//! The integer-grid map. A seamless parametrization whose gradients follow the cross field at the wanted quad size,
//! with transitions across the cut that are grid symmetries (quarter turns plus integer shifts), singularities on
//! lattice points and feature lines on lattice lines, so the integer lattice drawn on the surface is a quad mesh with
//! poles only at the field's singularities. After Mixed-Integer Quadrangulation (Bommes, Zimmer & Kobbelt, SIGGRAPH
//! 2009) and integer-grid maps (Bommes, Campen, Ebke, Alliez & Kobbelt, SIGGRAPH 2013); the constraint elimination and
//! greedy rounding are a small solver of our own (no CoMISo). Port of SculptForge's `QRemesh2Param.cpp`
//! (GPL-3.0-or-later).
//!
//!  1. Comb the field along a spanning tree of the triangles; the edges off the tree, pruned to the branches that
//!     reach singularities, open edges or handles, are the cut.
//!  2. Unknowns (u, v) per vertex wedge (corners not separated by the cut); linear constraints: across each cut edge
//!     the two sides differ by the quarter turn and an integer shift, feature edges keep one coordinate on an
//!     integer, singularities sit on integers. Eliminated one by one (integer unknowns stay integer combinations).
//!  3. Least squares for the gradients, solved by conjugate gradients; integer unknowns rounded nearest-first in
//!     batches, each followed by a warm-started re-solve.

use crate::eliminator::{term, Eliminator, Term};
use crate::field::{CrossField, Frames};
use crate::proxy::{Proxy, NONE};
use crate::solver::{block_sum, Multigrid, SparseMatrix};
use crate::vec3::V3;
use rayon::prelude::*;

const PI: f64 = std::f64::consts::PI;

#[derive(Clone, Debug, Default)]
pub struct GridMap {
    /// Per corner 3t+k: the map at that corner, in the triangle's chart.
    pub uv: Vec<[f32; 2]>,
    /// Triangles whose image is turned over.
    pub flipped: usize,
    pub cut_edges: usize,
    pub integer_vars: usize,
    /// In: scale the map to this many unit squares before rounding (0: as solved).
    pub want_quads: f64,
}

struct Csr {
    off: Vec<u32>,
    col: Vec<u32>,
    val: Vec<f64>,
    diag: Vec<f64>,
}

impl Csr {
    fn mul(&self, x: &[f64], z: &mut [f64]) {
        z.par_iter_mut().enumerate().for_each(|(i, zi)| {
            let mut s = 0.0;
            for k in self.off[i] as usize..self.off[i + 1] as usize {
                s += self.val[k] * x[self.col[k] as usize];
            }
            *zi = s;
        });
    }
}

/// The reduced normal equations C^T K C y = C^T (b - K d) (x = C y + d), applied without forming them: the cut tree
/// makes C fill in (a transition is the composition of everything below it), so C^T K C would not fit.
struct Reduced {
    k: Csr,
    ncopy: usize,
    c_off: Vec<u32>,
    c_col: Vec<u32>,
    c_val: Vec<f64>,
    t_off: Vec<u32>,
    t_row: Vec<u32>,
    t_val: Vec<f64>,
    reg: f64,
}

impl Reduced {
    fn apply(&self, y: &[f64], out: &mut [f64]) {
        let mut xs = vec![0.0; self.ncopy];
        xs.par_iter_mut().enumerate().for_each(|(v, x)| {
            let mut s = 0.0;
            for k in self.c_off[v] as usize..self.c_off[v + 1] as usize {
                s += self.c_val[k] * y[self.c_col[k] as usize];
            }
            *x = s;
        });
        let mut zs = vec![0.0; self.ncopy];
        self.k.mul(&xs, &mut zs);
        out.par_iter_mut().enumerate().for_each(|(j, o)| {
            let mut s = self.reg * y[j];
            for k in self.t_off[j] as usize..self.t_off[j + 1] as usize {
                s += self.t_val[k] * zs[self.t_row[k] as usize];
            }
            *o = s;
        });
    }
}

type Op<'a> = &'a (dyn Fn(&[f64], &mut [f64]) + Sync);

/// Conjugate gradients on the unknowns not fixed (Jacobi preconditioner unless `pre` is given); fixed entries of x
/// stay. Returns (iterations, stopped by `cancelled`).
#[allow(clippy::too_many_arguments)]
fn conjugate_gradients(
    apply: Op,
    diag: &[f64],
    b: &[f64],
    fixed: &[bool],
    x: &mut [f64],
    tol: f64,
    max_it: usize,
    cancelled: &(dyn Fn() -> bool + Sync),
    pre: Option<Op>,
) -> (usize, bool) {
    let n = b.len();
    let (mut r, mut z, mut q) = (vec![0.0; n], vec![0.0; n], vec![0.0; n]);
    let precond = |r: &[f64], z: &mut [f64]| {
        if let Some(p) = pre {
            p(r, z);
            for i in 0..n {
                if fixed[i] {
                    z[i] = 0.0;
                }
            }
        } else {
            for i in 0..n {
                z[i] = if fixed[i] { 0.0 } else { r[i] / diag[i] };
            }
        }
    };
    apply(x, &mut q);
    for i in 0..n {
        r[i] = if fixed[i] { 0.0 } else { b[i] - q[i] };
    }
    precond(&r, &mut z);
    let mut d = z.clone();
    let mut rz = block_sum(n, |i| r[i] * z[i]);
    let bn = block_sum(n, |i| if fixed[i] { 0.0 } else { b[i] * b[i] }).sqrt() + 1e-30;
    let mut it = 0;
    while it < max_it {
        let rn = block_sum(n, |i| r[i] * r[i]).sqrt();
        if rn < tol * bn {
            break;
        }
        if (it & 31) == 0 && cancelled() {
            return (it, true);
        }
        apply(&d, &mut q);
        let dq = block_sum(n, |i| if fixed[i] { 0.0 } else { d[i] * q[i] });
        if dq.is_nan() || dq <= 1e-300 {
            break;
        }
        let alpha = rz / dq;
        for i in 0..n {
            if !fixed[i] {
                x[i] += alpha * d[i];
                r[i] -= alpha * q[i];
            }
        }
        precond(&r, &mut z);
        let rz_new = block_sum(n, |i| r[i] * z[i]);
        let beta = rz_new / rz;
        rz = rz_new;
        for i in 0..n {
            d[i] = if fixed[i] { 0.0 } else { z[i] + beta * d[i] };
        }
        it += 1;
    }
    (it, false)
}

/// Exact dense block over the integer unknowns (singularity positions, seam shifts, feature lines), which couple
/// across the whole cut tree and which the multigrid's sparse stand-in leaves out: A_ii = S^T K S (S: their columns
/// of C). Used by a symmetric block Gauss-Seidel preconditioner over (integers, reals, integers).
struct IntBlock {
    mi: usize,
    aii: Vec<f64>,
    /// Free index of each integer unknown.
    int_vars: Vec<u32>,
    /// Integers (indices into `int_vars`) not fixed yet, and the Cholesky factor of A_ii over them.
    live: Vec<u32>,
    lii: Vec<f64>,
}

impl IntBlock {
    fn factor(&mut self, fixed: &[bool]) {
        self.live = (0..self.mi as u32).filter(|&a| !fixed[self.int_vars[a as usize] as usize]).collect();
        let m = self.live.len();
        let mut l = vec![0.0; m * m];
        for a in 0..m {
            for b in 0..m {
                l[a * m + b] = self.aii[self.live[a] as usize * self.mi + self.live[b] as usize];
            }
        }
        for j in 0..m {
            let mut d = l[j * m + j];
            for k in 0..j {
                d -= l[j * m + k] * l[j * m + k];
            }
            let d = d.max(1e-300).sqrt();
            l[j * m + j] = d;
            for i in j + 1..m {
                let mut s = l[i * m + j];
                for k in 0..j {
                    s -= l[i * m + k] * l[j * m + k];
                }
                l[i * m + j] = s / d;
            }
        }
        self.lii = l;
    }

    fn solve(&self, rhs: &mut [f64]) {
        let m = self.live.len();
        let l = &self.lii;
        for i in 0..m {
            let mut s = rhs[i];
            for k in 0..i {
                s -= l[i * m + k] * rhs[k];
            }
            rhs[i] = s / l[i * m + i];
        }
        for i in (0..m).rev() {
            let mut s = rhs[i];
            for k in i + 1..m {
                s -= l[k * m + i] * rhs[k];
            }
            rhs[i] = s / l[i * m + i];
        }
    }
}

/// Solves the map. `field` is combed in place (its theta and matching change: the matching becomes 0 off the cut).
/// False when `cancelled` said stop.
pub fn solve_grid_map(
    p: &Proxy,
    f: &Frames,
    field: &mut CrossField,
    out: &mut GridMap,
    cancelled: &(dyn Fn() -> bool + Sync),
) -> bool {
    let (nt, nv) = (p.t.len(), p.p.len());
    let nc = 3 * nt;
    if nt == 0 {
        return false;
    }

    // 1. Comb along a breadth-first spanning tree of the triangles.
    let mut tree = vec![false; nc];
    let mut seen = vec![false; nt];
    let mut queue: Vec<usize> = Vec::with_capacity(nt);
    for root in 0..nt {
        if seen[root] {
            continue;
        }
        seen[root] = true;
        queue.clear();
        queue.push(root);
        let mut qi = 0;
        while qi < queue.len() {
            let t = queue[qi];
            qi += 1;
            for k in 0..3 {
                let c = 3 * t + k;
                let o = p.opp[c];
                if o == NONE || seen[(o / 3) as usize] {
                    continue;
                }
                let g = (o / 3) as usize;
                seen[g] = true;
                // Turn g's cross so it matches t's across this edge.
                let m = field.matching[c] as i32;
                field.theta[g] -= m as f32 * (PI / 2.0) as f32;
                tree[c] = true;
                tree[o as usize] = true;
                // Update the matchings of g's edges for the turn (+m on g's corners, -m on their twins).
                for j in 0..3 {
                    let cg = 3 * g + j;
                    let og = p.opp[cg];
                    field.matching[cg] = ((field.matching[cg] as i32 + m) % 4) as i8;
                    if og != NONE {
                        field.matching[og as usize] = ((field.matching[og as usize] as i32 + 4 - m) % 4) as i8;
                    }
                }
                queue.push(g);
            }
        }
    }

    // 2. Cut graph: interior edges off the tree, pruned to the branches that reach something.
    let mut cut = vec![false; nc];
    let mut open = vec![false; nv];
    for c in 0..nc {
        if p.opp[c] == NONE {
            open[p.t[c / 3][c % 3] as usize] = true;
            open[p.t[c / 3][(c % 3 + 1) % 3] as usize] = true;
            continue;
        }
        if !tree[c] {
            cut[c] = true;
        }
    }
    {
        let mut vedges: Vec<Vec<u32>> = vec![Vec::new(); nv]; // per vertex: cut edges as their lower corner
        for c in 0..nc {
            if !cut[c] || p.opp[c] < c as u32 {
                continue;
            }
            vedges[p.t[c / 3][c % 3] as usize].push(c as u32);
            vedges[p.t[c / 3][(c % 3 + 1) % 3] as usize].push(c as u32);
        }
        let mut deg: Vec<i32> = vedges.iter().map(|e| e.len() as i32).collect();
        let prunable = |v: usize, deg: &[i32]| deg[v] == 1 && !open[v] && field.vertex_index[v] == 0;
        let mut stack: Vec<usize> = (0..nv).filter(|&v| prunable(v, &deg)).collect();
        while let Some(v) = stack.pop() {
            if deg[v] != 1 {
                continue;
            }
            let Some(e) = vedges[v].iter().copied().rfind(|&c| cut[c as usize]) else { continue };
            let e = e as usize;
            cut[e] = false;
            cut[p.opp[e] as usize] = false;
            let (a, b) = (p.t[e / 3][e % 3] as usize, p.t[e / 3][(e % 3 + 1) % 3] as usize);
            deg[a] -= 1;
            deg[b] -= 1;
            let other = if a == v { b } else { a };
            if prunable(other, &deg) {
                stack.push(other);
            }
        }
    }
    let cut_edges = (0..nc).filter(|&c| cut[c] && p.opp[c] != NONE && p.opp[c] > c as u32).count();

    // 3. Wedges: corners of a vertex joined across edges that are neither open nor cut.
    let mut parent: Vec<u32> = (0..nc as u32).collect();
    fn find(parent: &mut [u32], mut x: u32) -> u32 {
        while parent[x as usize] != x {
            parent[x as usize] = parent[parent[x as usize] as usize];
            x = parent[x as usize];
        }
        x
    }
    let corner_of = |t: usize, v: u32| -> usize {
        (0..3).find(|&k| p.t[t][k] == v).map_or(usize::MAX, |k| 3 * t + k)
    };
    for c in 0..nc {
        let o = p.opp[c];
        if o == NONE || cut[c] || o < c as u32 {
            continue;
        }
        let (t, g) = (c / 3, (o / 3) as usize);
        let (a, b) = (p.t[t][c % 3], p.t[t][(c % 3 + 1) % 3]);
        for v in [a, b] {
            let x = find(&mut parent, corner_of(t, v) as u32);
            let y = find(&mut parent, corner_of(g, v) as u32);
            if x != y {
                parent[x.max(y) as usize] = x.min(y);
            }
        }
    }
    let mut copy_of = vec![0u32; nc];
    let mut copy_id = vec![NONE; nc];
    let mut copies = 0u32;
    for c in 0..nc {
        let r = find(&mut parent, c as u32) as usize;
        if copy_id[r] == NONE {
            copy_id[r] = copies;
            copies += 1;
        }
        copy_of[c] = copy_id[r];
    }

    let mut el = Eliminator::default();
    for _ in 0..2 * copies {
        el.add(0);
    }
    let u_of = |corner: usize| 2 * copy_of[corner];

    // Axis of each triangle's field: X = (cos theta, sin theta) in its frame.
    let axis_of_edge = |t: usize, e: V3| -> usize {
        // 0: the edge runs along u (so v is constant), 1: along v
        let th = field.theta[t] as f64;
        let (x, y) = (e.dot(f.e1[t]) as f64, e.dot(f.e2[t]) as f64);
        let du = (x * th.cos() + y * th.sin()).abs();
        let dv = (-x * th.sin() + y * th.cos()).abs();
        if du >= dv {
            0
        } else {
            1
        }
    };

    // Singularities on lattice points.
    {
        let mut done = vec![false; nv];
        for c in 0..nc {
            let v = p.t[c / 3][c % 3] as usize;
            if field.vertex_index[v] == 0 || open[v] || done[v] {
                continue;
            }
            done[v] = true;
            let (iu, iv) = (el.add(3), el.add(3));
            el.constrain(&[term(u_of(c), 1.0), term(iu, -1.0)], 0.0);
            el.constrain(&[term(u_of(c) + 1, 1.0), term(iv, -1.0)], 0.0);
        }
    }
    // Feature lines on lattice lines.
    for c in 0..nc {
        if p.feature[c] == 0 {
            continue;
        }
        let (t, k) = (c / 3, c % 3);
        let e = p.p[p.t[t][(k + 1) % 3] as usize] - p.p[p.t[t][k] as usize];
        let along = axis_of_edge(t, e);
        let coord = if along == 0 { 1 } else { 0 }; // the coordinate that stays constant
        let s = el.add(2);
        el.constrain(&[term(u_of(c) + coord, 1.0), term(s, -1.0)], 0.0);
        el.constrain(&[term(u_of(3 * t + (k + 1) % 3) + coord, 1.0), term(s, -1.0)], 0.0);
    }
    // Seams: across each cut edge, (u, v) on the far side = R^m (u, v) + integer shift.
    for c in 0..nc {
        if !cut[c] || p.opp[c] < c as u32 {
            continue;
        }
        let o = p.opp[c] as usize;
        let (t, g, k) = (c / 3, o / 3, c % 3);
        let m = field.matching[c];
        let (tu, tv) = (el.add(1), el.add(1));
        for v in [p.t[t][k], p.t[t][(k + 1) % 3]] {
            let a = u_of(corner_of(t, v));
            let b = u_of(corner_of(g, v));
            // R^m: m = 1: (u, v) -> (v, -u).
            let r: [[f64; 2]; 2] = match m {
                0 => [[1.0, 0.0], [0.0, 1.0]],
                1 => [[0.0, 1.0], [-1.0, 0.0]],
                2 => [[-1.0, 0.0], [0.0, -1.0]],
                _ => [[0.0, -1.0], [1.0, 0.0]],
            };
            el.constrain(&[term(b, 1.0), term(a, -r[0][0]), term(a + 1, -r[0][1]), term(tu, -1.0)], 0.0);
            el.constrain(&[term(b + 1, 1.0), term(a, -r[1][0]), term(a + 1, -r[1][1]), term(tv, -1.0)], 0.0);
        }
    }

    // 4. Free unknowns.
    let nvar = el.size();
    let ncopy = 2 * copies as usize;
    let mut free_id = vec![NONE; nvar];
    let mut free_vars: Vec<u32> = Vec::new();
    for v in 0..nvar as u32 {
        if !el.eliminated(v) {
            free_id[v as usize] = free_vars.len() as u32;
            free_vars.push(v);
        }
    }
    let nf = free_vars.len();
    // v as terms over the free unknowns, plus its constant
    let expand = |v: u32, terms: &mut Vec<Term>| -> f64 {
        terms.clear();
        if !el.eliminated(v) {
            terms.push(term(free_id[v as usize], 1.0));
            return 0.0;
        }
        for t in el.expr(v) {
            terms.push(term(free_id[t.var as usize], t.a));
        }
        el.constant(v)
    };

    // K and b on the wedge unknowns: per triangle, the hat-function gradients against the target gradients.
    let mut b_full = vec![0.0f64; ncopy];
    let mut trips: Vec<(u32, u32, f64)> = Vec::with_capacity(nt * 18);
    for t in 0..nt {
        let tri = p.t[t];
        let o = p.p[tri[0] as usize];
        let mut q = [[0f32; 2]; 3];
        for k in 0..3 {
            let d = p.p[tri[k] as usize] - o;
            q[k] = [d.dot(f.e1[t]), d.dot(f.e2[t])];
        }
        let a2 = (q[1][0] - q[0][0]) as f64 * (q[2][1] - q[0][1]) as f64 - (q[2][0] - q[0][0]) as f64 * (q[1][1] - q[0][1]) as f64;
        if a2.is_nan() || a2 <= 1e-30 {
            continue;
        }
        let area = 0.5 * a2;
        let (mut gx, mut gy) = ([0f64; 3], [0f64; 3]);
        for k in 0..3 {
            let (e0, e1) = ((q[(k + 2) % 3][0] - q[(k + 1) % 3][0]) as f64, (q[(k + 2) % 3][1] - q[(k + 1) % 3][1]) as f64);
            gx[k] = -e1 / a2;
            gy[k] = e0 / a2;
        }
        let h = (p.size[tri[0] as usize] as f64 + p.size[tri[1] as usize] as f64 + p.size[tri[2] as usize] as f64) / 3.0;
        let th = field.theta[t] as f64;
        let gu = [th.cos() / h, th.sin() / h];
        let gv = [-th.sin() / h, th.cos() / h];
        for coord in 0..2 {
            let g = if coord == 0 { gu } else { gv };
            for i in 0..3 {
                let vi = u_of(3 * t + i) + coord as u32;
                b_full[vi as usize] += area * (gx[i] * g[0] + gy[i] * g[1]);
                for j in 0..3 {
                    let vj = u_of(3 * t + j) + coord as u32;
                    trips.push((vi, vj, area * (gx[i] * gx[j] + gy[i] * gy[j])));
                }
            }
        }
    }
    trips.sort_by_key(|t| (t.0, t.1)); // stable: duplicates are summed in insertion order
    let mut k_mat = Csr { off: vec![0; ncopy + 1], col: Vec::new(), val: Vec::new(), diag: vec![0.0; ncopy] };
    {
        let mut i = 0;
        while i < trips.len() {
            let mut j = i;
            let mut v = 0.0;
            while j < trips.len() && trips[j].0 == trips[i].0 && trips[j].1 == trips[i].1 {
                v += trips[j].2;
                j += 1;
            }
            k_mat.col.push(trips[i].1);
            k_mat.val.push(v);
            k_mat.off[trips[i].0 as usize + 1] += 1;
            i = j;
        }
        for i in 0..ncopy {
            k_mat.off[i + 1] += k_mat.off[i];
        }
        for i in 0..ncopy {
            for k in k_mat.off[i] as usize..k_mat.off[i + 1] as usize {
                if k_mat.col[k] as usize == i {
                    k_mat.diag[i] = k_mat.val[k];
                }
            }
        }
    }
    drop(trips);
    // C as rows (per wedge unknown) and columns (per free unknown).
    let mut c_off = vec![0u32; ncopy + 1];
    let (mut c_col, mut c_val) = (Vec::<u32>::new(), Vec::<f64>::new());
    let mut d_const = vec![0.0f64; ncopy];
    {
        let mut terms = Vec::new();
        for v in 0..ncopy {
            d_const[v] = expand(v as u32, &mut terms);
            for t in &terms {
                c_col.push(t.var);
                c_val.push(t.a);
            }
            c_off[v + 1] = c_col.len() as u32;
        }
    }
    let mut t_off = vec![0u32; nf + 1];
    let mut t_row = vec![0u32; c_col.len()];
    let mut t_val = vec![0.0f64; c_col.len()];
    for &c in &c_col {
        t_off[c as usize + 1] += 1;
    }
    for i in 0..nf {
        t_off[i + 1] += t_off[i];
    }
    {
        let mut fill: Vec<u32> = t_off[..nf].to_vec();
        for v in 0..ncopy {
            for k in c_off[v] as usize..c_off[v + 1] as usize {
                let at = fill[c_col[k] as usize] as usize;
                fill[c_col[k] as usize] += 1;
                t_row[at] = v as u32;
                t_val[at] = c_val[k];
            }
        }
    }
    let mut rhs = vec![0.0f64; nf];
    let mut diag = vec![0.0f64; nf];
    {
        let mut kd = vec![0.0; ncopy];
        k_mat.mul(&d_const, &mut kd);
        for j in 0..nf {
            let (mut s, mut dg) = (0.0, 0.0);
            for k in t_off[j] as usize..t_off[j + 1] as usize {
                let r = t_row[k] as usize;
                s += t_val[k] * (b_full[r] - kd[r]);
                dg += t_val[k] * t_val[k] * k_mat.diag[r];
            }
            rhs[j] = s;
            diag[j] = dg;
        }
    }
    let mean_diag = if nf > 0 { diag.iter().sum::<f64>() / nf as f64 } else { 1.0 };
    let reg = 1e-7 * mean_diag;
    for d in diag.iter_mut() {
        *d = if *d > 0.0 { *d + reg } else { 1.0 };
    }
    let reduced = Reduced { k: k_mat, ncopy, c_off, c_col, c_val, t_off, t_row, t_val, reg };
    let op = |y: &[f64], o: &mut [f64]| reduced.apply(y, o);

    // Multigrid preconditioner on a sparse stand-in for C^T K C: each wedge unknown mapped to its main free real
    // unknown only (the integer fill-in dropped), integer unknowns on their own diagonal.
    let kind_of_free = |j: usize| el.kind(free_vars[j]);
    let mg = {
        let mut main_of = vec![-1i64; ncopy];
        let mut main_coef = vec![0.0f64; ncopy];
        for v in 0..ncopy {
            let mut best = 0.0;
            for k in reduced.c_off[v] as usize..reduced.c_off[v + 1] as usize {
                let (col, val) = (reduced.c_col[k] as usize, reduced.c_val[k]);
                if kind_of_free(col) == 0 && val.abs() > best {
                    best = val.abs();
                    main_of[v] = col as i64;
                    main_coef[v] = val;
                }
            }
        }
        let mut st: Vec<(u64, f64)> = Vec::with_capacity(reduced.k.val.len() + nf);
        for i in 0..ncopy {
            if main_of[i] < 0 {
                continue;
            }
            for k in reduced.k.off[i] as usize..reduced.k.off[i + 1] as usize {
                let j = reduced.k.col[k] as usize;
                if main_of[j] < 0 {
                    continue;
                }
                st.push((((main_of[i] as u64) << 32) | main_of[j] as u64, main_coef[i] * reduced.k.val[k] * main_coef[j]));
            }
        }
        for j in 0..nf {
            st.push((((j as u64) << 32) | j as u64, if kind_of_free(j) != 0 { diag[j] } else { reg }));
        }
        Multigrid::build(&SparseMatrix::build(nf, &mut st))
    };

    // The integer block.
    let mut int_idx = vec![NONE; nf];
    let mut int_vars: Vec<u32> = Vec::new();
    for j in 0..nf {
        if kind_of_free(j) != 0 {
            int_idx[j] = int_vars.len() as u32;
            int_vars.push(j as u32);
        }
    }
    let mi = int_vars.len();
    let use_int_block = mi > 0 && mi <= 4000;
    let mut blk = IntBlock { mi, aii: Vec::new(), int_vars: int_vars.clone(), live: Vec::new(), lii: Vec::new() };
    if use_int_block {
        let mut aii = vec![0.0f64; mi * mi];
        // S rows (per wedge unknown: its integer terms); K is symmetric, so (K S)_v = sum_u K_vu S_u.
        let s_rows: Vec<Vec<(u32, f64)>> = (0..ncopy)
            .map(|v| {
                (reduced.c_off[v] as usize..reduced.c_off[v + 1] as usize)
                    .filter(|&k| int_idx[reduced.c_col[k] as usize] != NONE)
                    .map(|k| (int_idx[reduced.c_col[k] as usize], reduced.c_val[k]))
                    .collect()
            })
            .collect();
        let mut acc = vec![0.0f64; mi];
        let mut touched: Vec<u32> = Vec::new();
        for v in 0..ncopy {
            if s_rows[v].is_empty() {
                continue;
            }
            touched.clear();
            for k in reduced.k.off[v] as usize..reduced.k.off[v + 1] as usize {
                for &(j, c) in &s_rows[reduced.k.col[k] as usize] {
                    if acc[j as usize] == 0.0 {
                        touched.push(j);
                    }
                    acc[j as usize] += reduced.k.val[k] * c;
                    if acc[j as usize] == 0.0 {
                        acc[j as usize] = 1e-300;
                    }
                }
            }
            for &(i, c) in &s_rows[v] {
                for &j in &touched {
                    aii[i as usize * mi + j as usize] += c * acc[j as usize];
                }
            }
            for &j in &touched {
                acc[j as usize] = 0.0;
            }
        }
        for i in 0..mi {
            aii[i * mi + i] += reg;
        }
        blk.aii = aii;
    }

    // One CG solve with the current state of the integer block as part of the preconditioner.
    let solve = |blk: &IntBlock, rhs: &[f64], fixed: &[bool], y: &mut [f64], tol: f64, max_it: usize| -> (usize, bool) {
        let pre = |r: &[f64], z: &mut [f64]| {
            if !use_int_block || blk.live.is_empty() {
                mg.apply(r, z);
                return;
            }
            let m = blk.live.len();
            // z_i1 = A_ii^-1 r_i
            let mut ri: Vec<f64> = (0..m).map(|a| r[blk.int_vars[blk.live[a] as usize] as usize]).collect();
            blk.solve(&mut ri);
            let mut pz = vec![0.0; nf];
            for a in 0..m {
                pz[blk.int_vars[blk.live[a] as usize] as usize] = ri[a];
            }
            // z_r = MG(r_r - A_ri z_i1)
            let mut pt = vec![0.0; nf];
            reduced.apply(&pz, &mut pt);
            let pr: Vec<f64> = (0..nf).map(|j| if int_idx[j] == NONE { r[j] - pt[j] } else { 0.0 }).collect();
            mg.apply(&pr, z);
            for j in 0..nf {
                if int_idx[j] != NONE {
                    z[j] = 0.0;
                }
            }
            // z_i = A_ii^-1 (r_i - A_ir z_r)
            reduced.apply(z, &mut pt);
            let mut ri: Vec<f64> = (0..m)
                .map(|a| {
                    let j = blk.int_vars[blk.live[a] as usize] as usize;
                    r[j] - pt[j]
                })
                .collect();
            blk.solve(&mut ri);
            for a in 0..m {
                z[blk.int_vars[blk.live[a] as usize] as usize] = ri[a];
            }
        };
        conjugate_gradients(&op, &diag, rhs, fixed, y, tol, max_it, cancelled, Some(&pre))
    };

    // 5. Solve, round the integers nearest-first, re-solve.
    let mut y = vec![0.0f64; nf];
    let mut fixed = vec![false; nf];
    if use_int_block {
        blk.factor(&fixed);
    }
    let (mut iters, stopped) = solve(&blk, &rhs, &fixed, &mut y, 1e-4, 2000);
    if stopped {
        return false;
    }
    // Before rounding: scale the map so its area (the number of unit squares, i.e. quads) is the wanted count. The
    // constraints are homogeneous, so a scaled solution is still a solution; distortion near singularities and
    // features otherwise leaves the count 10-20% off.
    if out.want_quads > 0.0 {
        let mut terms = Vec::new();
        let mut at = |corner: usize, coord: usize, y: &[f64]| {
            let k = expand(u_of(corner) + coord as u32, &mut terms);
            k + terms.iter().map(|t| t.a * y[t.var as usize]).sum::<f64>()
        };
        let mut area = 0.0;
        for t in 0..nt {
            let (ax, ay) = (at(3 * t, 0, &y), at(3 * t, 1, &y));
            let (bx, by) = (at(3 * t + 1, 0, &y), at(3 * t + 1, 1, &y));
            let (cx, cy) = (at(3 * t + 2, 0, &y), at(3 * t + 2, 1, &y));
            area += 0.5 * ((bx - ax) * (cy - ay) - (cx - ax) * (by - ay)).abs();
        }
        if area > 0.0 {
            let s = (out.want_quads / area).sqrt();
            y.iter_mut().for_each(|v| *v *= s);
            rhs.iter_mut().for_each(|v| *v *= s);
        }
    }
    {
        let mut left: Vec<u32> = int_vars.clone();
        while !left.is_empty() {
            let mut dist: Vec<(f64, u32)> = left.iter().map(|&i| ((y[i as usize] - y[i as usize].round()).abs(), i)).collect();
            dist.sort_by(|a, b| a.0.total_cmp(&b.0).then(a.1.cmp(&b.1)));
            const FRAC: f64 = 0.7;
            let mut take = ((left.len() as f64 * FRAC + 0.999) as usize).max(1);
            while take < dist.len() && dist[take].0 < 0.08 {
                take += 1;
            }
            if left.len() <= 8 {
                take = left.len();
            }
            for &(_, i) in &dist[..take] {
                y[i as usize] = y[i as usize].round();
                fixed[i as usize] = true;
            }
            left = dist[take..].iter().map(|d| d.1).collect();
            if use_int_block {
                blk.factor(&fixed);
            }
            let last = left.is_empty();
            let (it, stopped) = solve(&blk, &rhs, &fixed, &mut y, if last { 1e-4 } else { 1e-2 }, if last { 2000 } else { 500 });
            if stopped {
                return false;
            }
            iters += it;
        }
    }
    let _ = iters;

    // 6. The map at each corner.
    out.uv = vec![[0.0; 2]; nc];
    let mut terms = Vec::new();
    for c in 0..nc {
        let mut val = [0.0f64; 2];
        for (coord, slot) in val.iter_mut().enumerate() {
            let k = expand(u_of(c) + coord as u32, &mut terms);
            *slot = k + terms.iter().map(|t| t.a * y[t.var as usize]).sum::<f64>();
        }
        out.uv[c] = [val[0] as f32, val[1] as f32];
    }
    out.flipped = (0..nt)
        .filter(|&t| {
            let (a, b, c) = (out.uv[3 * t], out.uv[3 * t + 1], out.uv[3 * t + 2]);
            let area = (b[0] - a[0]) * (c[1] - a[1]) - (c[0] - a[0]) * (b[1] - a[1]);
            area.is_nan() || area <= 0.0
        })
        .count();
    out.cut_edges = cut_edges;
    out.integer_vars = int_vars.len();
    true
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::field::{compute_curvature, solve_cross_field, FieldSettings};
    use crate::testmesh::{grid_cube, icosphere, proxy_of, torus};

    fn no_cancel() -> bool {
        false
    }

    /// Proxy, frames, solved (uncombed) field.
    fn setup(src: &crate::workmesh::WorkMesh, edge: f32) -> (Proxy, Frames, CrossField) {
        let p = proxy_of(src, edge);
        let f = Frames::build(&p);
        let c = compute_curvature(&p, &f, edge * 3.0);
        let field = solve_cross_field(&p, &f, &c, &FieldSettings::default(), &no_cancel).unwrap();
        (p, f, field)
    }

    fn rot(m: i8, uv: [f32; 2]) -> [f32; 2] {
        match m {
            0 => uv,
            1 => [uv[1], -uv[0]],
            2 => [-uv[0], -uv[1]],
            _ => [-uv[1], uv[0]],
        }
    }

    fn frac_dist(x: f32) -> f32 {
        (x - x.round()).abs()
    }

    fn map_area(p: &Proxy, m: &GridMap) -> f64 {
        (0..p.t.len())
            .map(|t| {
                let (a, b, c) = (m.uv[3 * t], m.uv[3 * t + 1], m.uv[3 * t + 2]);
                0.5 * ((b[0] - a[0]) * (c[1] - a[1]) - (c[0] - a[0]) * (b[1] - a[1])) as f64
            })
            .sum()
    }

    /// Solves the map and checks what makes it an integer-grid map; returns it with the combed field.
    fn solve_and_check(src: &crate::workmesh::WorkMesh, edge: f32, want: f64) -> (Proxy, CrossField, GridMap) {
        let (p, f, mut field) = setup(src, edge);
        let mut map = GridMap { want_quads: want, ..Default::default() };
        assert!(solve_grid_map(&p, &f, &mut field, &mut map, &no_cancel));
        // Seamless: across every interior edge the two sides agree up to the grid symmetry (rotation by the
        // matching plus an integer shift) at both ends of the edge.
        let mut worst = 0f32;
        for c in 0..p.opp.len() {
            let o = p.opp[c];
            if o == NONE {
                continue;
            }
            let (t, g) = (c / 3, (o / 3) as usize);
            for v in [p.t[t][c % 3], p.t[t][(c % 3 + 1) % 3]] {
                let ct = 3 * t + (0..3).find(|&k| p.t[t][k] == v).unwrap();
                let cg = 3 * g + (0..3).find(|&k| p.t[g][k] == v).unwrap();
                let r = rot(field.matching[c], map.uv[ct]);
                worst = worst.max(frac_dist(map.uv[cg][0] - r[0])).max(frac_dist(map.uv[cg][1] - r[1]));
            }
        }
        assert!(worst < 2e-3, "seam transitions are off the grid symmetries by {worst}");
        // Area = wanted number of unit squares, up to what rounding the integers (singularity positions, feature
        // lines) costs; the pipeline corrects the count once more when it is over 12% off.
        let area = map_area(&p, &map);
        assert!((area / want - 1.0).abs() < 0.10, "map area {area}, wanted {want}");
        (p, field, map)
    }

    #[test]
    fn sphere_map_is_seamless_with_singularities_on_lattice_points() {
        let (p, field, map) = solve_and_check(&icosphere(5), 0.1, 400.0);
        assert!(map.flipped * 100 <= p.t.len(), "{} of {} triangles flipped", map.flipped, p.t.len());
        // every singular vertex sits on an integer point in each of its corners' charts
        let mut checked = 0;
        for (t, tri) in p.t.iter().enumerate() {
            for k in 0..3 {
                if field.vertex_index[tri[k] as usize] != 0 {
                    let uv = map.uv[3 * t + k];
                    assert!(frac_dist(uv[0]) < 1e-3 && frac_dist(uv[1]) < 1e-3, "singularity at {uv:?}");
                    checked += 1;
                }
            }
        }
        assert!(checked >= 8 * 3, "{checked} corners checked");
        println!("sphere map: {} cut edges, {} integer unknowns, {} flipped", map.cut_edges, map.integer_vars, map.flipped);
    }

    #[test]
    fn torus_map_is_seamless_across_handles() {
        let (p, _, map) = solve_and_check(&torus(1.0, 0.4, 80, 40), 0.1, 600.0);
        assert!(map.flipped * 100 <= p.t.len(), "{} of {} triangles flipped", map.flipped, p.t.len());
        assert!(map.cut_edges > 0, "a torus needs a cut along its two handles");
    }

    #[test]
    fn cube_feature_lines_lie_on_lattice_lines() {
        let (p, _, map) = solve_and_check(&grid_cube(10), 0.25, 600.0);
        assert!(map.flipped * 50 <= p.t.len(), "{} of {} triangles flipped", map.flipped, p.t.len());
        let mut checked = 0;
        for c in 0..p.feature.len() {
            if p.feature[c] == 0 {
                continue;
            }
            let (t, k) = (c / 3, c % 3);
            let (a, b) = (map.uv[3 * t + k], map.uv[3 * t + (k + 1) % 3]);
            // one coordinate is the same integer at both ends of the edge
            let ok = (0..2).any(|i| frac_dist(a[i]) < 1e-3 && frac_dist(b[i]) < 1e-3 && (a[i] - b[i]).abs() < 1e-3);
            assert!(ok, "feature edge {a:?} -> {b:?} is off the lattice");
            checked += 1;
        }
        assert!(checked >= 12 * 4, "{checked} feature edges checked");
    }

    #[test]
    fn solving_is_deterministic_and_does_not_depend_on_the_thread_count() {
        let src = icosphere(4);
        let run = |threads: usize| {
            let pool = rayon::ThreadPoolBuilder::new().num_threads(threads).build().unwrap();
            pool.install(|| {
                let (p, f, mut field) = setup(&src, 0.15);
                let mut map = GridMap { want_quads: 150.0, ..Default::default() };
                assert!(solve_grid_map(&p, &f, &mut field, &mut map, &no_cancel));
                map.uv
            })
        };
        assert_eq!(run(1), run(4));
    }

    #[test]
    fn cancellation_stops_the_solver() {
        let (p, f, mut field) = setup(&icosphere(4), 0.15);
        let mut map = GridMap { want_quads: 150.0, ..Default::default() };
        assert!(!solve_grid_map(&p, &f, &mut field, &mut map, &|| true));
    }
}
