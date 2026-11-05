// SPDX-License-Identifier: GPL-3.0-or-later
//! Sparse solver for the cross field and the integer-grid map: conjugate gradients preconditioned by an aggregation
//! multigrid V-cycle, for symmetric (real) or Hermitian (complex) positive definite systems.
//! Plain Jacobi-CG needs thousands of iterations on a 100k-triangle surface (smooth fields are the slowest modes);
//! with the V-cycle a few dozen do. Aggregates are a vertex and its strongly coupled neighbours; the prolongation
//! carries the phase of the connection (so it also works for the cross field's connection Laplacian); coarse
//! matrices are Galerkin products; the smoother is symmetric Gauss-Seidel. Port of SculptForge's
//! `QRemesh2Solver.h` (GPL-3.0-or-later). Deterministic: sums are taken in fixed blocks and every ordering that
//! feeds floating-point additions is sorted, so the result does not depend on the thread count.

use crate::complex::Scalar;
use rayon::prelude::*;

const NO_AGG: u32 = u32::MAX;

#[derive(Clone, Debug, Default)]
pub struct SparseMatrix<S: Scalar> {
    pub n: usize,
    /// CSR, columns sorted, diagonal included.
    pub off: Vec<u32>,
    pub col: Vec<u32>,
    pub val: Vec<S>,
    /// Real diagonal.
    pub diag: Vec<f64>,
}

impl<S: Scalar> SparseMatrix<S> {
    /// From `(row << 32 | col, value)` triplets (duplicates are summed, in input order).
    pub fn build(size: usize, trips: &mut [(u64, S)]) -> SparseMatrix<S> {
        trips.sort_by_key(|t| t.0); // stable
        let mut m = SparseMatrix { n: size, off: vec![0; size + 1], col: Vec::new(), val: Vec::new(), diag: vec![0.0; size] };
        let mut i = 0;
        while i < trips.len() {
            let mut j = i;
            let mut v = S::default();
            while j < trips.len() && trips[j].0 == trips[i].0 {
                v += trips[j].1;
                j += 1;
            }
            let (r, c) = ((trips[i].0 >> 32) as usize, (trips[i].0 & 0xffff_ffff) as u32);
            m.col.push(c);
            m.val.push(v);
            m.off[r + 1] += 1;
            i = j;
        }
        for i in 0..size {
            m.off[i + 1] += m.off[i];
        }
        for i in 0..size {
            for k in m.off[i] as usize..m.off[i + 1] as usize {
                if m.col[k] as usize == i {
                    m.diag[i] = m.val[k].re();
                }
            }
        }
        for d in m.diag.iter_mut() {
            if d.is_nan() || *d <= 0.0 {
                *d = 1.0;
            }
        }
        m
    }

    pub fn mul(&self, x: &[S], y: &mut [S]) {
        y.par_iter_mut().enumerate().for_each(|(i, yi)| {
            let mut s = S::default();
            for k in self.off[i] as usize..self.off[i + 1] as usize {
                s += self.val[k] * x[self.col[k] as usize];
            }
            *yi = s;
        });
    }
}

#[derive(Default)]
struct Level<S: Scalar> {
    a: SparseMatrix<S>,
    /// Fine unknown -> coarse unknown (this level -> next).
    agg: Vec<u32>,
    /// Tentative prolongation coefficient of each fine unknown.
    phase: Vec<S>,
    /// Smoothed prolongation P = (I - w D^-1 A) P0, rows per fine unknown.
    p_off: Vec<u32>,
    p_col: Vec<u32>,
    p_val: Vec<S>,
}

#[derive(Default)]
pub struct Multigrid<S: Scalar> {
    levels: Vec<Level<S>>,
    dense: Vec<S>,
}

/// Sorts `(col, value)` pairs by column (stable) and sums runs of equal columns.
fn merge_by_col<S: Scalar>(row: &mut Vec<(u32, S)>) {
    row.sort_by_key(|e| e.0);
    let mut w = 0;
    for r in 0..row.len() {
        if w > 0 && row[w - 1].0 == row[r].0 {
            let v = row[r].1;
            row[w - 1].1 += v;
        } else {
            row[w] = row[r];
            w += 1;
        }
    }
    row.truncate(w);
}

impl<S: Scalar> Multigrid<S> {
    pub fn build(a: &SparseMatrix<S>) -> Multigrid<S> {
        let mut mg = Multigrid { levels: vec![Level { a: a.clone(), ..Default::default() }], dense: Vec::new() };
        while mg.levels.last().unwrap().a.n > 400 && mg.levels.len() < 30 {
            let fine = mg.levels.last_mut().unwrap();
            let Some(coarse) = Self::coarsen(fine) else { break };
            if coarse.n * 10 > fine.a.n * 9 {
                break;
            }
            mg.levels.push(Level { a: coarse, ..Default::default() });
        }
        if mg.levels.last().unwrap().a.n <= 2500 {
            mg.factor_coarsest();
        }
        mg
    }

    pub fn levels(&self) -> usize {
        self.levels.len()
    }

    /// z = M^{-1} r (one V-cycle from zero).
    pub fn apply(&self, r: &[S], z: &mut [S]) {
        z.copy_from_slice(&self.vcycle(0, r));
    }

    /// Aggregates: an unaggregated unknown takes its unaggregated neighbours; leftovers join the neighbour they are
    /// most strongly coupled to. Returns the coarse matrix.
    fn coarsen(f: &mut Level<S>) -> Option<SparseMatrix<S>> {
        let a = &f.a;
        let n = a.n;
        f.agg = vec![NO_AGG; n];
        f.phase = vec![S::real(1.0); n];
        let mut nc = 0u32;
        let strong = |i: usize, k: usize| a.val[k].abs2() >= 0.0625 * a.diag[i] * a.diag[a.col[k] as usize] / 9.0;
        for i in 0..n {
            if f.agg[i] != NO_AGG {
                continue;
            }
            let mut free = true;
            let (lo, hi) = (a.off[i] as usize, a.off[i + 1] as usize);
            for k in lo..hi {
                if a.col[k] as usize != i && strong(i, k) && f.agg[a.col[k] as usize] != NO_AGG {
                    free = false;
                    break;
                }
            }
            if !free {
                continue;
            }
            f.agg[i] = nc;
            f.phase[i] = S::real(1.0);
            for k in lo..hi {
                let j = a.col[k] as usize;
                if j == i || !strong(i, k) {
                    continue;
                }
                f.agg[j] = nc;
                // Value at j for a value at i: -A_ji / |A_ji| (A_ji = conj(A_ij)).
                let aji = a.val[k].conj();
                let m = aji.abs2().sqrt();
                f.phase[j] = if m > 0.0 { (-aji).scale(1.0 / m) } else { S::real(1.0) };
            }
            nc += 1;
        }
        for _pass in 0..4 {
            for i in 0..n {
                if f.agg[i] != NO_AGG {
                    continue;
                }
                let mut best = -1.0f64;
                let mut bk = usize::MAX;
                for k in a.off[i] as usize..a.off[i + 1] as usize {
                    let j = a.col[k] as usize;
                    if j == i || f.agg[j] == NO_AGG {
                        continue;
                    }
                    if a.val[k].abs2() > best {
                        best = a.val[k].abs2();
                        bk = k;
                    }
                }
                if bk == usize::MAX {
                    continue;
                }
                let j = a.col[bk] as usize;
                f.agg[i] = f.agg[j];
                let aij = a.val[bk];
                let m = aij.abs2().sqrt();
                f.phase[i] = if m > 0.0 { (-aij).scale(1.0 / m) * f.phase[j] } else { f.phase[j] };
            }
        }
        for i in 0..n {
            if f.agg[i] == NO_AGG {
                f.agg[i] = nc;
                nc += 1;
                f.phase[i] = S::real(1.0);
            }
        }
        if nc == 0 || nc as usize >= n {
            return None;
        }
        // Smoothed aggregation: P = (I - w D^-1 A) P0 (w = 2/3), so coarse corrections are smooth and the V-cycle
        // converges in a few dozen iterations instead of hundreds.
        let w = 2.0 / 3.0;
        f.p_off = vec![0; n + 1];
        f.p_col.clear();
        f.p_val.clear();
        let mut row: Vec<(u32, S)> = Vec::new();
        for i in 0..n {
            row.clear();
            row.push((f.agg[i], f.phase[i]));
            for k in a.off[i] as usize..a.off[i + 1] as usize {
                let j = a.col[k] as usize;
                row.push((f.agg[j], (a.val[k] * f.phase[j]).scale(-(w / a.diag[i]))));
            }
            merge_by_col(&mut row);
            for &(c, v) in &row {
                f.p_col.push(c);
                f.p_val.push(v);
            }
            f.p_off[i + 1] = f.p_col.len() as u32;
        }
        // Galerkin: Ac = P^H A P, via AP (rows fine, columns coarse).
        let mut trips: Vec<(u64, S)> = Vec::new();
        let mut acc: Vec<(u32, S)> = Vec::new();
        for i in 0..n {
            acc.clear();
            for k in a.off[i] as usize..a.off[i + 1] as usize {
                let j = a.col[k] as usize;
                for m in f.p_off[j] as usize..f.p_off[j + 1] as usize {
                    acc.push((f.p_col[m], a.val[k] * f.p_val[m]));
                }
            }
            merge_by_col(&mut acc);
            for m in f.p_off[i] as usize..f.p_off[i + 1] as usize {
                let ci = f.p_col[m] as u64;
                let pc = f.p_val[m].conj();
                for &(cj, v) in &acc {
                    trips.push(((ci << 32) | cj as u64, pc * v));
                }
            }
        }
        Some(SparseMatrix::build(nc as usize, &mut trips))
    }

    /// Smoothing on A x = b: damped Jacobi in parallel on big levels (two sweeps), Gauss-Seidel (forwards or
    /// backwards, so the V-cycle stays symmetric) on small ones.
    fn sweep(a: &SparseMatrix<S>, b: &[S], x: &mut [S], forwards: bool) {
        let n = a.n;
        if n > 20000 {
            let mut r = vec![S::default(); n];
            for _ in 0..2 {
                a.mul(x, &mut r);
                x.par_iter_mut().enumerate().for_each(|(i, xi)| {
                    *xi += (b[i] - r[i]).scale(0.7 / a.diag[i]);
                });
            }
            return;
        }
        for s in 0..n {
            let i = if forwards { s } else { n - 1 - s };
            let mut r = b[i];
            for k in a.off[i] as usize..a.off[i + 1] as usize {
                if a.col[k] as usize != i {
                    r -= a.val[k] * x[a.col[k] as usize];
                }
            }
            x[i] = r.scale(1.0 / a.diag[i]);
        }
    }

    /// Dense Cholesky (A = L L^H) of the coarsest level.
    fn factor_coarsest(&mut self) {
        let a = &self.levels.last().unwrap().a;
        let n = a.n;
        let mut d = vec![S::default(); n * n];
        for i in 0..n {
            for k in a.off[i] as usize..a.off[i + 1] as usize {
                d[i * n + a.col[k] as usize] = a.val[k];
            }
        }
        for j in 0..n {
            let mut dj = d[j * n + j].re();
            for k in 0..j {
                dj -= d[j * n + k].abs2();
            }
            let dj = dj.max(1e-300).sqrt();
            d[j * n + j] = S::real(dj);
            for i in j + 1..n {
                let mut s = d[i * n + j];
                for k in 0..j {
                    s -= d[i * n + k] * d[j * n + k].conj();
                }
                d[i * n + j] = s.scale(1.0 / dj);
            }
        }
        self.dense = d;
    }

    fn solve_coarsest(&self, b: &[S]) -> Vec<S> {
        let n = b.len();
        let d = &self.dense;
        let mut x = b.to_vec();
        for i in 0..n {
            let mut s = x[i];
            for k in 0..i {
                s -= d[i * n + k] * x[k];
            }
            x[i] = s.scale(1.0 / d[i * n + i].re());
        }
        for i in (0..n).rev() {
            let mut s = x[i];
            for k in i + 1..n {
                s -= d[k * n + i].conj() * x[k];
            }
            x[i] = s.scale(1.0 / d[i * n + i].re());
        }
        x
    }

    /// Symmetric V-cycle: forward sweep, coarse correction, backward sweep.
    fn vcycle(&self, li: usize, b: &[S]) -> Vec<S> {
        let l = &self.levels[li];
        let n = l.a.n;
        let mut x = vec![S::default(); n];
        if li + 1 == self.levels.len() {
            if !self.dense.is_empty() {
                return self.solve_coarsest(b);
            }
            for _ in 0..20 {
                Self::sweep(&l.a, b, &mut x, true);
                Self::sweep(&l.a, b, &mut x, false);
            }
            return x;
        }
        Self::sweep(&l.a, b, &mut x, true);
        let mut r = vec![S::default(); n];
        l.a.mul(&x, &mut r);
        for (ri, &bi) in r.iter_mut().zip(b) {
            *ri = bi - *ri;
        }
        let nc = self.levels[li + 1].a.n;
        let mut rc = vec![S::default(); nc];
        for (i, &ri) in r.iter().enumerate() {
            for m in l.p_off[i] as usize..l.p_off[i + 1] as usize {
                rc[l.p_col[m] as usize] += l.p_val[m].conj() * ri;
            }
        }
        let xc = self.vcycle(li + 1, &rc);
        for (i, xi) in x.iter_mut().enumerate() {
            for m in l.p_off[i] as usize..l.p_off[i + 1] as usize {
                *xi += l.p_val[m] * xc[l.p_col[m] as usize];
            }
        }
        Self::sweep(&l.a, b, &mut x, false);
        x
    }
}

/// Sum of `term(i)` for i < n in fixed blocks of 8192 (same result for any thread count).
pub fn block_sum(n: usize, term: impl Fn(usize) -> f64 + Sync) -> f64 {
    const BLOCK: usize = 8192;
    let parts: Vec<f64> = (0..n.div_ceil(BLOCK))
        .into_par_iter()
        .map(|b| (b * BLOCK..n.min((b + 1) * BLOCK)).map(&term).sum::<f64>())
        .collect();
    parts.iter().sum()
}

pub struct PcgResult {
    pub iterations: usize,
    /// `cancelled` asked to stop.
    pub stopped: bool,
}

/// Preconditioned CG on A x = b with A applied by `mul` and the multigrid of A (Jacobi on `diag` when `mg` is None).
#[allow(clippy::too_many_arguments)]
pub fn pcg<S: Scalar>(
    mul: &(dyn Fn(&[S], &mut [S]) + Sync),
    mg: Option<&Multigrid<S>>,
    diag: &[f64],
    b: &[S],
    x: &mut [S],
    tol: f64,
    max_it: usize,
    cancelled: &(dyn Fn() -> bool + Sync),
) -> PcgResult {
    let n = b.len();
    let mut r = vec![S::default(); n];
    let mut z = vec![S::default(); n];
    let mut q = vec![S::default(); n];
    mul(x, &mut q);
    for i in 0..n {
        r[i] = b[i] - q[i];
    }
    let precond = |input: &[S], out: &mut [S]| match mg {
        Some(m) => m.apply(input, out),
        None => {
            for i in 0..n {
                out[i] = input[i].scale(1.0 / diag[i]);
            }
        }
    };
    precond(&r, &mut z);
    let mut d = z.clone();
    let mut rz = block_sum(n, |i| (r[i].conj() * z[i]).re());
    let bn = block_sum(n, |i| b[i].abs2()).sqrt() + 1e-300;
    let mut it = 0;
    while it < max_it {
        let rn = block_sum(n, |i| r[i].abs2()).sqrt();
        if rn < tol * bn {
            break;
        }
        if (it & 7) == 0 && cancelled() {
            return PcgResult { iterations: it, stopped: true };
        }
        mul(&d, &mut q);
        let dq = block_sum(n, |i| (d[i].conj() * q[i]).re());
        if dq.is_nan() || dq <= 1e-300 {
            break;
        }
        let alpha = rz / dq;
        for i in 0..n {
            x[i] += d[i].scale(alpha);
            r[i] -= q[i].scale(alpha);
        }
        precond(&r, &mut z);
        let rz_new = block_sum(n, |i| (r[i].conj() * z[i]).re());
        let beta = rz_new / rz;
        rz = rz_new;
        for i in 0..n {
            d[i] = z[i] + d[i].scale(beta);
        }
        it += 1;
    }
    PcgResult { iterations: it, stopped: false }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::complex::{c64, C64};

    /// 2D grid Laplacian with a small shift (SPD). `phase(i, j)` gives the unit-modulus link from j to i
    /// (Hermitian: phase(j, i) = conj(phase(i, j))); real case uses 1.
    fn grid<S: Scalar>(n: usize, shift: f64, link: impl Fn(usize, usize) -> S) -> SparseMatrix<S> {
        let id = |x: usize, y: usize| y * n + x;
        let mut trips: Vec<(u64, S)> = Vec::new();
        for y in 0..n {
            for x in 0..n {
                let i = id(x, y);
                let mut deg = 0.0;
                for (dx, dy) in [(1i32, 0i32), (-1, 0), (0, 1), (0, -1)] {
                    let (nx, ny) = (x as i32 + dx, y as i32 + dy);
                    if nx < 0 || ny < 0 || nx >= n as i32 || ny >= n as i32 {
                        continue;
                    }
                    let j = id(nx as usize, ny as usize);
                    deg += 1.0;
                    trips.push((((i as u64) << 32) | j as u64, -link(i, j)));
                }
                trips.push((((i as u64) << 32) | i as u64, S::real(deg + shift)));
            }
        }
        SparseMatrix::build(n * n, &mut trips)
    }

    fn no_cancel() -> bool {
        false
    }

    fn residual<S: Scalar>(a: &SparseMatrix<S>, x: &[S], b: &[S]) -> f64 {
        let mut q = vec![S::default(); a.n];
        a.mul(x, &mut q);
        let r: f64 = (0..a.n).map(|i| (b[i] - q[i]).abs2()).sum::<f64>().sqrt();
        let bn: f64 = b.iter().map(|v| v.abs2()).sum::<f64>().sqrt();
        r / bn
    }

    #[test]
    fn matrix_vector_product_and_duplicates() {
        let mut trips = vec![(0u64, 2.0), (1, -1.0), (1u64 << 32, -1.0), ((1u64 << 32) | 1, 2.0), (0, 1.0)];
        let a = SparseMatrix::<f64>::build(2, &mut trips);
        assert_eq!(a.diag, vec![3.0, 2.0]); // the two (0,0) entries are summed
        let mut y = vec![0.0; 2];
        a.mul(&[1.0, 1.0], &mut y);
        assert_eq!(y, vec![2.0, 1.0]);
    }

    #[test]
    fn multigrid_cg_solves_a_real_laplacian_in_few_iterations() {
        let n = 150; // 22500 unknowns: more than the 20000 where the parallel Jacobi smoother kicks in
        let a = grid::<f64>(n, 1e-3, |_, _| 1.0);
        let b: Vec<f64> = (0..n * n).map(|i| ((i * 7919) % 101) as f64 / 101.0 - 0.5).collect();
        let mg = Multigrid::build(&a);
        assert!(mg.levels() >= 3, "{} levels", mg.levels());
        let mul = |x: &[f64], y: &mut [f64]| a.mul(x, y);
        let mut x = vec![0.0; n * n];
        let r = pcg(&mul, Some(&mg), &a.diag, &b, &mut x, 1e-8, 200, &no_cancel);
        assert!(!r.stopped);
        assert!(residual(&a, &x, &b) < 1e-7, "residual {}", residual(&a, &x, &b));
        // plain Jacobi-CG needs far more
        let mut x2 = vec![0.0; n * n];
        let j = pcg(&mul, None, &a.diag, &b, &mut x2, 1e-8, 5000, &no_cancel);
        println!("multigrid-CG {} iterations, Jacobi-CG {} iterations", r.iterations, j.iterations);
        assert!(r.iterations * 4 < j.iterations, "{} vs {}", r.iterations, j.iterations);
        assert!(r.iterations < 80);
    }

    #[test]
    fn hermitian_system_with_a_connection() {
        // Links with phase e^{i phi}: non-trivial holonomy around each cell, like the cross field's connection.
        let n = 60;
        let phi = |i: usize, j: usize| {
            let (lo, hi) = (i.min(j), i.max(j));
            let angle = 0.9 * (((lo * 31 + hi * 17) % 13) as f64 / 13.0 - 0.5);
            let l = C64::polar(1.0, angle);
            if i < j { l } else { l.conj() }
        };
        let a = grid::<C64>(n, 0.05, phi);
        // Hermitian: A_ij = conj(A_ji)
        for i in 0..a.n {
            for k in a.off[i] as usize..a.off[i + 1] as usize {
                let j = a.col[k] as usize;
                let kk = (a.off[j] as usize..a.off[j + 1] as usize).find(|&m| a.col[m] as usize == i).unwrap();
                assert_eq!(a.val[k], a.val[kk].conj());
            }
        }
        let b: Vec<C64> = (0..n * n).map(|i| c64(((i * 13) % 17) as f64 - 8.0, ((i * 5) % 11) as f64 - 5.0)).collect();
        let mg = Multigrid::build(&a);
        let mul = |x: &[C64], y: &mut [C64]| a.mul(x, y);
        let mut x = vec![C64::ZERO; n * n];
        let r = pcg(&mul, Some(&mg), &a.diag, &b, &mut x, 1e-9, 300, &no_cancel);
        assert!(residual(&a, &x, &b) < 1e-8, "residual {} after {} iterations", residual(&a, &x, &b), r.iterations);
        println!("complex multigrid-CG: {} levels, {} iterations", mg.levels(), r.iterations);
    }

    #[test]
    fn cancellation_stops_the_solver() {
        let a = grid::<f64>(40, 1e-3, |_, _| 1.0);
        let b = vec![1.0; a.n];
        let mul = |x: &[f64], y: &mut [f64]| a.mul(x, y);
        let mut x = vec![0.0; a.n];
        let r = pcg(&mul, None, &a.diag, &b, &mut x, 1e-12, 1000, &|| true);
        assert!(r.stopped);
    }

    #[test]
    fn solution_does_not_depend_on_the_thread_count() {
        let a = grid::<f64>(120, 1e-3, |_, _| 1.0);
        let b: Vec<f64> = (0..a.n).map(|i| ((i * 31) % 97) as f64).collect();
        let solve = |threads: usize| {
            let pool = rayon::ThreadPoolBuilder::new().num_threads(threads).build().unwrap();
            pool.install(|| {
                let mg = Multigrid::build(&a);
                let mul = |x: &[f64], y: &mut [f64]| a.mul(x, y);
                let mut x = vec![0.0; a.n];
                pcg(&mul, Some(&mg), &a.diag, &b, &mut x, 1e-9, 200, &no_cancel);
                x
            })
        };
        assert_eq!(solve(1), solve(4));
    }
}
