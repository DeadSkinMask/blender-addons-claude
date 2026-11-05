// SPDX-License-Identifier: GPL-3.0-or-later
//! Curvature and the cross field on the proxy surface.
//!
//! Curvature: the per-edge dihedral angle tensor of Cohen-Steiner & Morvan ("Restricted Delaunay triangulations and
//! normal cycle", 2003), summed per triangle and smoothed over about one quad, so a dense sculpt's pores and
//! scratches don't steer the edge flow but its brow, lips and limbs do.
//!
//! Cross field: a 4-RoSy field per triangle as u = e^{4 i theta} (Ray, Vallet, Li & Levy, "N-symmetry direction
//! field design", 2008; Knoppel, Crane, Pinkall & Schroder, "Globally optimal direction fields", 2013): the
//! connection Laplacian (smoothness) plus a pull towards the principal curvature directions weighted by how
//! anisotropic the bending is, with feature edges (open edges, borders, hard edges, guides) as fixed directions.
//! One sparse Hermitian system, solved by multigrid-preconditioned conjugate gradients with deterministic sums.
//! Port of SculptForge's `QRemesh2Field.cpp` (GPL-3.0-or-later).

use crate::complex::{c64, C64};
use crate::mesh::{Attributes, Mesh};
use crate::proxy::{Proxy, NONE};
use crate::solver::{pcg, Multigrid, SparseMatrix};
use crate::vec3::{v3, V3};
use rayon::prelude::*;

const PI: f64 = std::f64::consts::PI;

/// Per triangle: an orthonormal frame (e1 along the first edge, e2 = n x e1) the field angles are measured in.
#[derive(Clone, Debug, Default)]
pub struct Frames {
    pub e1: Vec<V3>,
    pub e2: Vec<V3>,
    pub n: Vec<V3>,
    pub area: Vec<f32>,
}

impl Frames {
    pub fn build(p: &Proxy) -> Frames {
        let nt = p.t.len();
        let items: Vec<(V3, V3, V3, f32)> = (0..nt)
            .into_par_iter()
            .map(|t| {
                let tri = p.t[t];
                let a = p.p[tri[1] as usize] - p.p[tri[0] as usize];
                let b = p.p[tri[2] as usize] - p.p[tri[0] as usize];
                let c = a.cross(b);
                let n = c.normalized();
                let e1 = (a - n * n.dot(a)).safe_normalize(v3(1.0, 0.0, 0.0));
                let e2 = n.cross(e1);
                (e1, e2, n, 0.5 * c.length())
            })
            .collect();
        Frames {
            e1: items.iter().map(|i| i.0).collect(),
            e2: items.iter().map(|i| i.1).collect(),
            n: items.iter().map(|i| i.2).collect(),
            area: items.iter().map(|i| i.3).collect(),
        }
    }

    /// Angle of `v` in triangle `t`'s frame.
    pub fn angle_in(&self, t: usize, v: V3) -> f64 {
        (v.dot(self.e2[t]) as f64).atan2(v.dot(self.e1[t]) as f64)
    }

    /// The world direction at `angle` in triangle `t`'s frame.
    pub fn direction(&self, t: usize, angle: f32) -> V3 {
        self.e1[t] * angle.cos() + self.e2[t] * angle.sin()
    }
}

/// Per triangle curvature tensor, smoothed over `radius`: the direction of strongest bending (angle in the frame),
/// the anisotropy |k1 - k2| (1 / length) and the magnitude.
#[derive(Clone, Debug, Default)]
pub struct Curvature {
    pub angle: Vec<f32>,
    pub anisotropy: Vec<f32>,
    pub magnitude: Vec<f32>,
}

pub fn compute_curvature(p: &Proxy, f: &Frames, radius: f32) -> Curvature {
    let nt = p.t.len();
    // Per triangle 3x3 symmetric tensor (xx, xy, xz, yy, yz, zz), area-weighted sums.
    let mut tensor = vec![[0f32; 6]; nt];
    let (mut edge_sum, mut edge_count) = (0f64, 0usize);
    for t in 0..nt {
        for k in 0..3 {
            let c = 3 * t + k;
            let o = p.opp[c];
            let e = p.p[p.t[t][(k + 1) % 3] as usize] - p.p[p.t[t][k] as usize];
            let len = e.length();
            edge_sum += len as f64;
            edge_count += 1;
            if o == NONE || (o as usize) < c || p.feature[c] != 0 {
                continue; // a feature edge is a crease, not curvature
            }
            let g = (o / 3) as usize;
            let ed = e / len.max(1e-30);
            // Signed dihedral angle: positive where the surface bends away from its normal (convex).
            let beta = f.n[t].cross(f.n[g]).dot(ed).atan2(f.n[t].dot(f.n[g]));
            let w = 0.5 * beta * len;
            let m = [w * ed.x * ed.x, w * ed.x * ed.y, w * ed.x * ed.z, w * ed.y * ed.y, w * ed.y * ed.z, w * ed.z * ed.z];
            for i in 0..6 {
                tensor[t][i] += m[i];
                tensor[g][i] += m[i];
            }
        }
    }
    let mean_edge = if edge_count > 0 { (edge_sum / edge_count as f64) as f32 } else { 1.0 };
    // Smoothing: area-weighted averages over edge neighbours; diffusion reaches about mean_edge * sqrt(rounds).
    let ratio = (radius / mean_edge) as f64;
    let rounds = ((ratio * ratio).round() as i64).clamp(1, 60) as usize;
    let mut area = f.area.clone();
    for _ in 0..rounds {
        let next: Vec<([f32; 6], f32)> = (0..nt)
            .into_par_iter()
            .map(|t| {
                let mut s = tensor[t];
                let mut a = area[t];
                for k in 0..3 {
                    let o = p.opp[3 * t + k];
                    if o == NONE || p.feature[3 * t + k] != 0 {
                        continue; // don't blur across feature lines
                    }
                    let g = (o / 3) as usize;
                    for i in 0..6 {
                        s[i] += tensor[g][i];
                    }
                    a += area[g];
                }
                let w = 1.0 / 4.0;
                for v in s.iter_mut() {
                    *v *= w;
                }
                (s, a * w)
            })
            .collect();
        for (t, (s, a)) in next.into_iter().enumerate() {
            tensor[t] = s;
            area[t] = a;
        }
    }
    let per: Vec<(f32, f32, f32)> = (0..nt)
        .into_par_iter()
        .map(|t| {
            let m = &tensor[t];
            let inv = 1.0 / area[t].max(1e-30);
            let apply = |v: V3| {
                v3(m[0] * v.x + m[1] * v.y + m[2] * v.z, m[1] * v.x + m[3] * v.y + m[4] * v.z, m[2] * v.x + m[4] * v.y + m[5] * v.z) * inv
            };
            let (a1, a2) = (apply(f.e1[t]), apply(f.e2[t]));
            let xx = f.e1[t].dot(a1);
            let xy = f.e2[t].dot(a1);
            let yy = f.e2[t].dot(a2);
            let angle = 0.5 * (2.0 * xy).atan2(xx - yy);
            let aniso = ((xx - yy) * (xx - yy) + 4.0 * xy * xy).sqrt();
            (angle, aniso, 0.5 * (xx + yy).abs() + 0.5 * aniso)
        })
        .collect();
    Curvature {
        angle: per.iter().map(|x| x.0).collect(),
        anisotropy: per.iter().map(|x| x.1).collect(),
        magnitude: per.iter().map(|x| x.2).collect(),
    }
}

#[derive(Clone, Debug)]
pub struct FieldSettings {
    /// How strongly the field follows the curvature directions.
    pub align_weight: f32,
    /// Per triangle: a guide's direction to follow softly (NaN: none).
    pub guide_angle: Vec<f32>,
    pub guide_weight: Vec<f32>,
}

impl Default for FieldSettings {
    fn default() -> Self {
        // SculptForge's default weight (QRemesh2.cpp): the curvature only nudges the smoothest field.
        FieldSettings { align_weight: 0.01, guide_angle: Vec::new(), guide_weight: Vec::new() }
    }
}

#[derive(Clone, Debug, Default)]
pub struct CrossField {
    /// Per triangle: the u direction's angle in the triangle's frame.
    pub theta: Vec<f32>,
    /// Per corner: quarter turns from this triangle's cross to the twin's.
    pub matching: Vec<i8>,
    /// Per vertex: singularity index in quarter turns (0 regular, +1 = valence 3, -1 = valence 5).
    pub vertex_index: Vec<i8>,
    pub singularities: usize,
}

/// Matching across each edge and the singularity index of each vertex for the current angles.
pub fn compute_matching(p: &Proxy, f: &Frames, field: &mut CrossField) {
    let (nt, nv) = (p.t.len(), p.p.len());
    field.matching = vec![0; 3 * nt];
    let mut eps = vec![0f32; 3 * nt]; // field rotation across the edge, beyond the matching
    for t in 0..nt {
        for k in 0..3 {
            let c = 3 * t + k;
            let o = p.opp[c];
            if o == NONE {
                continue;
            }
            let g = (o / 3) as usize;
            let e = p.p[p.t[t][(k + 1) % 3] as usize] - p.p[p.t[t][k] as usize];
            let moved = field.theta[t] as f64 - f.angle_in(t, e) + f.angle_in(g, e);
            let d = field.theta[g] as f64 - moved;
            let q = (d / (PI / 2.0)).round() as i64;
            field.matching[c] = (((q % 4) + 4) % 4) as i8;
            eps[c] = (d - q as f64 * PI / 2.0) as f32;
        }
    }
    // Singularity index: (sum of eps going round + angle defect) / (pi/2), interior vertices only.
    field.vertex_index = vec![0; nv];
    let mut angle_sum = vec![0f64; nv];
    let mut eps_sum = vec![0f64; nv];
    let mut open = vec![false; nv];
    for t in 0..nt {
        for k in 0..3 {
            let v = p.t[t][k] as usize;
            let a = p.p[p.t[t][(k + 1) % 3] as usize] - p.p[v];
            let b = p.p[p.t[t][(k + 2) % 3] as usize] - p.p[v];
            angle_sum[v] += (a.cross(b).length() as f64).atan2(a.dot(b) as f64);
            // Going round v counter-clockwise, triangle t hands over to the twin across the edge (T[k+2] -> v).
            let c = 3 * t + (k + 2) % 3;
            if p.opp[c] == NONE {
                open[v] = true;
            } else {
                eps_sum[v] += eps[c] as f64;
            }
            if p.opp[3 * t + k] == NONE {
                open[v] = true;
            }
        }
    }
    field.singularities = 0;
    for v in 0..nv {
        if open[v] {
            continue;
        }
        let defect = 2.0 * PI - angle_sum[v];
        let q = ((eps_sum[v] + defect) / (PI / 2.0)).round() as i64;
        field.vertex_index[v] = q.clamp(-4, 4) as i8;
        if q != 0 {
            field.singularities += 1;
        }
    }
}

fn arg4(v: C64) -> f32 {
    if v.abs() > 1e-30 {
        (v.arg() / 4.0) as f32
    } else {
        0.0
    }
}

/// Smooth 4-RoSy field: feature edges fixed, curvature directions followed with `align_weight`. Solved as sparse
/// complex linear systems (u = e^{4 i theta}), then normalised; deterministic. `None` when `cancelled` said stop.
pub fn solve_cross_field(
    p: &Proxy,
    f: &Frames,
    curv: &Curvature,
    s: &FieldSettings,
    cancelled: &(dyn Fn() -> bool + Sync),
) -> Option<CrossField> {
    let nt = p.t.len();
    // Fixed triangles: those with a feature edge, along the longest one.
    let mut fixed = vec![false; nt];
    let mut z = vec![C64::ZERO; nt];
    let mut target = vec![C64::ZERO; nt];
    let mut weight = vec![0f64; nt];
    for t in 0..nt {
        let mut best = -1.0f32;
        for k in 0..3 {
            if p.feature[3 * t + k] == 0 {
                continue;
            }
            let e = p.p[p.t[t][(k + 1) % 3] as usize] - p.p[p.t[t][k] as usize];
            let l = e.length();
            if l > best {
                best = l;
                z[t] = C64::polar(1.0, 4.0 * f.angle_in(t, e));
            }
        }
        if best > 0.0 && t < p.fixed_dir.len() && p.fixed_dir[t].length2() > 0.0 {
            z[t] = C64::polar(1.0, 4.0 * f.angle_in(t, p.fixed_dir[t])); // a guide: its smooth tangent
        }
        if best > 0.0 {
            fixed[t] = true;
            continue;
        }
        // Curvature: anisotropy times the quad size is the bend across one quad (radians); full weight from ~17 degrees.
        let tri = p.t[t];
        let h = (p.size[tri[0] as usize] as f64 + p.size[tri[1] as usize] as f64 + p.size[tri[2] as usize] as f64) / 3.0;
        let bend = (curv.anisotropy[t] as f64 * h / 0.3).min(1.0);
        weight[t] = s.align_weight as f64 * bend * bend;
        target[t] = C64::polar(1.0, 4.0 * curv.angle[t] as f64);
        if t < s.guide_angle.len() && !s.guide_angle[t].is_nan() && s.guide_weight[t] > 0.0 {
            // A soft guide replaces the curvature pull near the curve.
            let gw = s.guide_weight[t] as f64;
            target[t] = target[t] * ((1.0 - gw.min(1.0)) * weight[t]) + C64::polar(gw * 10.0, 4.0 * s.guide_angle[t] as f64);
            weight[t] = target[t].abs();
            target[t] = if weight[t] > 0.0 { target[t] * (1.0 / weight[t]) } else { C64::ONE };
        }
    }
    // Transport factors per corner: from the twin triangle's frame into this one's.
    let mut transport = vec![C64::ZERO; 3 * nt];
    for t in 0..nt {
        for k in 0..3 {
            let c = 3 * t + k;
            let o = p.opp[c];
            if o == NONE {
                continue;
            }
            let g = (o / 3) as usize;
            let e = p.p[p.t[t][(k + 1) % 3] as usize] - p.p[p.t[t][k] as usize];
            let at = f.angle_in(t, e);
            let ag = f.angle_in(g, e);
            // Wanted quad size h: the field lives in the metric scaled by 1/h, whose connection turns by
            // (log h_b - log h_a) times the dual / primal edge length ratio (cotangent weight) crossing into t.
            // Without it, (1/h) X can't be a gradient and the map smears the density out.
            let mut size = 0.0;
            if !p.field_size.is_empty() {
                let (a, b) = (p.t[t][k], p.t[t][(k + 1) % 3]);
                let cot_at = |tri: usize, opposite: u32| {
                    let q = p.t[tri];
                    let j = (0..3).find(|&j| q[j] == opposite).unwrap_or(3).min(2);
                    let u = p.p[q[(j + 1) % 3] as usize] - p.p[q[j] as usize];
                    let v = p.p[q[(j + 2) % 3] as usize] - p.p[q[j] as usize];
                    u.dot(v) as f64 / (u.cross(v).length() as f64).max(1e-20)
                };
                let ct = p.t[t][(k + 2) % 3];
                let mut cg = p.t[g][0];
                for j in 0..3 {
                    if p.t[g][j] != a && p.t[g][j] != b {
                        cg = p.t[g][j];
                    }
                }
                let w = (0.5 * (cot_at(t, ct) + cot_at(g, cg))).clamp(0.0, 5.0);
                const GAIN: f64 = 2.0;
                size = GAIN * w * ((p.field_size[b as usize] as f64).ln() - (p.field_size[a as usize] as f64).ln());
            }
            transport[c] = C64::polar(1.0, 4.0 * (at - ag + size));
        }
    }
    // System: for free t, (deg + w) z_t - sum r z_g = w target (+ fixed neighbours).
    let mut diag = vec![1.0f64; nt];
    let mut rhs = vec![C64::ZERO; nt];
    for t in 0..nt {
        if fixed[t] {
            continue;
        }
        let mut d = weight[t];
        let mut b = target[t] * weight[t];
        for k in 0..3 {
            let c = 3 * t + k;
            let o = p.opp[c];
            if o == NONE {
                continue;
            }
            d += 1.0;
            if fixed[(o / 3) as usize] {
                b += transport[c] * z[(o / 3) as usize];
            }
        }
        diag[t] = d.max(1e-12);
        rhs[t] = b;
    }
    // The system over the free triangles, preconditioned by multigrid.
    let mut fid = vec![NONE; nt];
    let mut free_tris: Vec<usize> = Vec::new();
    for t in 0..nt {
        if !fixed[t] {
            fid[t] = free_tris.len() as u32;
            free_tris.push(t);
        }
    }
    let nfree = free_tris.len();
    let mut x = vec![C64::ZERO; nfree];
    if nfree > 0 {
        let mut trips: Vec<(u64, C64)> = Vec::with_capacity(nfree * 4);
        let mut b = vec![C64::ZERO; nfree];
        for (i, &t) in free_tris.iter().enumerate() {
            trips.push((((i as u64) << 32) | i as u64, c64(diag[t], 0.0)));
            for k in 0..3 {
                let c = 3 * t + k;
                let o = p.opp[c];
                if o == NONE || fixed[(o / 3) as usize] {
                    continue;
                }
                trips.push((((i as u64) << 32) | fid[(o / 3) as usize] as u64, -transport[c]));
            }
            b[i] = rhs[t];
            x[i] = if weight[t] > 0.0 { target[t] } else { C64::ZERO };
        }
        // A tiny shift keeps a closed surface's system definite.
        let mean_diag = free_tris.iter().map(|&t| diag[t]).sum::<f64>() / nfree as f64;
        for i in 0..nfree {
            trips.push((((i as u64) << 32) | i as u64, c64(1e-4 * mean_diag, 0.0)));
        }
        let a = SparseMatrix::build(nfree, &mut trips);
        let mg = Multigrid::build(&a);
        let mul = |inp: &[C64], out: &mut [C64]| a.mul(inp, out);

        // Inverse power iteration (the smoothest field, Knoppel et al. 2013) with the alignment and the fixed
        // triangles as a steady pull: x <- A^-1 (sigma x / |x| + b). Where nothing pulls, the field is the smoothest
        // one rather than noise.
        let bn = (b.iter().map(|v| v.norm()).sum::<f64>() / nfree as f64).sqrt();
        // Start: the pull, or a constant phase where there is none.
        for v in x.iter_mut() {
            if v.abs() == 0.0 {
                *v = C64::ONE;
            }
        }
        // With fixed directions (feature lines) the pull towards them must dominate: only a light smoothest-field
        // term. Without any, the smoothest field is the answer and the scale only sets the step.
        const TUNE_SIGMA: f64 = 2e-3;
        const PROJECTED: usize = 30;
        let sigma = if nfree < nt { TUNE_SIGMA * mean_diag } else { bn.max(1e-3 * mean_diag) };
        let mut rhsk = vec![C64::ZERO; nfree];
        let mut local: Vec<f64> = Vec::new(); // per free triangle: the projected rounds' weight (empty: not run)
        let mut last_round = 6 + PROJECTED;
        let mut round = 0;
        while round < last_round {
            if round == 6 {
                // Zeros of order two or more (four poles merged by symmetry, as inside a closed guide loop) are
                // split by projected rounds around them, strongly there and lightly elsewhere; none: done.
                let mut probe = CrossField { theta: vec![0.0; nt], ..Default::default() };
                for t in 0..nt {
                    probe.theta[t] = arg4(if fixed[t] { z[t] } else { x[fid[t] as usize] });
                }
                compute_matching(p, f, &mut probe);
                let mut ring = vec![-1i32; nt];
                let mut queue: Vec<usize> = Vec::new();
                for (t, r) in ring.iter_mut().enumerate() {
                    for k in 0..3 {
                        if probe.vertex_index[p.t[t][k] as usize].abs() >= 2 && *r < 0 {
                            *r = 0;
                            queue.push(t);
                        }
                    }
                }
                if queue.is_empty() {
                    // none: a few light projected rounds everywhere (they smooth the field)
                    local = vec![sigma * 4.0; nfree];
                    last_round = 6 + 8;
                    round += 1;
                    continue;
                }
                let mut qi = 0;
                while qi < queue.len() {
                    let t = queue[qi];
                    qi += 1;
                    if ring[t] >= 30 {
                        continue;
                    }
                    for k in 0..3 {
                        let o = p.opp[3 * t + k];
                        if o == NONE || ring[(o / 3) as usize] >= 0 {
                            continue;
                        }
                        ring[(o / 3) as usize] = ring[t] + 1;
                        queue.push((o / 3) as usize);
                    }
                }
                let strong = bn.max(1e-3 * mean_diag) * 4.0;
                local = vec![sigma; nfree];
                for i in 0..nfree {
                    if ring[free_tris[i]] >= 0 {
                        local[i] = strong;
                    }
                }
            }
            let xn = (x.iter().map(|v| v.norm()).sum::<f64>() / nfree as f64).sqrt() + 1e-300;
            // Later rounds normalise per triangle (a projected heat flow, like Ginzburg-Landau): a zero of order
            // two or more (a closed guide's centre, by symmetry) splits into simple ones that move apart.
            if round < 6 {
                for i in 0..nfree {
                    rhsk[i] = x[i] * (sigma / xn) + b[i];
                }
            } else {
                for i in 0..nfree {
                    let m = x[i].abs();
                    rhsk[i] = (if m > 1e-12 { x[i] * (1.0 / m) } else { C64::ZERO }) * local[i] + b[i];
                }
            }
            let mut nx = x.clone();
            let r = pcg(&mul, Some(&mg), &a.diag, &rhsk, &mut nx, if round < 3 { 1e-3 } else { 1e-5 }, 200, cancelled);
            if r.stopped {
                return None;
            }
            x = nx;
            round += 1;
        }
    }
    let mut out = CrossField { theta: vec![0.0; nt], ..Default::default() };
    for t in 0..nt {
        out.theta[t] = arg4(if fixed[t] { z[t] } else { x[fid[t] as usize] });
    }
    // A few rounds of local smoothing of the normalised field (the linear solve shrinks |u| near singularities and
    // under-smooths there).
    for _ in 0..4 {
        let next: Vec<f32> = (0..nt)
            .into_par_iter()
            .map(|t| {
                if fixed[t] {
                    return out.theta[t];
                }
                let mut sum = C64::polar(1.0, 4.0 * out.theta[t] as f64) * (1.0 + weight[t]);
                sum += target[t] * weight[t];
                for k in 0..3 {
                    let c = 3 * t + k;
                    let o = p.opp[c];
                    if o == NONE {
                        continue;
                    }
                    sum += transport[c] * C64::polar(1.0, 4.0 * out.theta[(o / 3) as usize] as f64);
                }
                if sum.abs() > 1e-30 {
                    (sum.arg() / 4.0) as f32
                } else {
                    out.theta[t]
                }
            })
            .collect();
        out.theta = next;
    }
    compute_matching(p, f, &mut out);
    Some(out)
}

/// For inspection in Blender: two thin quads per triangle along the field's two directions (lifted slightly along the
/// normal), `length` and `width` in model units.
pub fn field_debug_mesh(p: &Proxy, f: &Frames, field: &CrossField, length: f32, width: f32) -> Mesh {
    let mut m = Mesh { attrs: Attributes::default(), ..Default::default() };
    for t in 0..p.t.len() {
        let tri = p.t[t];
        let c = (p.p[tri[0] as usize] + p.p[tri[1] as usize] + p.p[tri[2] as usize]) / 3.0 + f.n[t] * (0.1 * width);
        let u = f.direction(t, field.theta[t]);
        let v = f.n[t].cross(u);
        for (d, side) in [(u, v), (v, u)] {
            let base = m.positions.len() as u32;
            for (a, b) in [(-1.0, -1.0), (1.0, -1.0), (1.0, 1.0), (-1.0, 1.0)] {
                m.positions.push((c + d * (a * length) + side * (b * width)).to_array());
            }
            m.face_sizes.push(4);
            m.indices.extend_from_slice(&[base, base + 1, base + 2, base + 3]);
        }
    }
    m
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::bvh::{SegmentBvh, TriangleBvh};
    use crate::proxy::build_proxy;
    use crate::testmesh::{grid_cube, grid_plane, icosphere, torus, tube};
    use crate::workmesh::{detect_features, WorkMesh};

    /// The proxy of `src` at proxy edge `edge` (hard edges and borders as features), sizes set to 3 edges.
    fn proxy_of(src: &WorkMesh, edge: f32) -> Proxy {
        let surface = TriangleBvh::build(src.p.clone(), src.t.clone());
        let features = detect_features(src, false, 60f32.to_radians().cos(), &[], 3.0 * edge);
        let segs = (!features.seg_a.is_empty()).then(|| SegmentBvh::build(features.seg_a.clone(), features.seg_b.clone()));
        let (_b, mut proxy, _) = build_proxy(src, &surface, segs.as_ref(), features.sharp, false, edge, src.area());
        proxy.size = vec![3.0 * edge; proxy.p.len()];
        proxy
    }

    fn no_cancel() -> bool {
        false
    }

    fn solve(p: &Proxy, align: f32) -> (Frames, Curvature, CrossField) {
        let f = Frames::build(p);
        let c = compute_curvature(p, &f, 3.0 * p.size[0] / 3.0);
        let s = FieldSettings { align_weight: align, ..Default::default() };
        let field = solve_cross_field(p, &f, &c, &s, &no_cancel).expect("not cancelled");
        (f, c, field)
    }

    fn fraction(p: &Proxy, keep: impl Fn(usize) -> bool, ok: impl Fn(usize) -> bool) -> f32 {
        let all: Vec<usize> = (0..p.t.len()).filter(|&t| keep(t)).collect();
        all.iter().filter(|&&t| ok(t)).count() as f32 / all.len().max(1) as f32
    }

    fn centroid(p: &Proxy, t: usize) -> V3 {
        (p.p[p.t[t][0] as usize] + p.p[p.t[t][1] as usize] + p.p[p.t[t][2] as usize]) / 3.0
    }

    #[test]
    fn frames_are_orthonormal() {
        let p = proxy_of(&icosphere(4), 0.2);
        let f = Frames::build(&p);
        let mut area = 0.0;
        for t in 0..p.t.len() {
            assert!((f.e1[t].length() - 1.0).abs() < 1e-5 && (f.e2[t].length() - 1.0).abs() < 1e-5);
            assert!(f.e1[t].dot(f.e2[t]).abs() < 1e-5 && f.e1[t].dot(f.n[t]).abs() < 1e-5 && f.e2[t].dot(f.n[t]).abs() < 1e-5);
            assert!(f.n[t].cross(f.e1[t]).dot(f.e2[t]) > 0.99, "e2 must be n x e1");
            area += f.area[t] as f64;
            // the angle of a direction round-trips
            let d = f.direction(t, 0.7);
            assert!((f.angle_in(t, d) - 0.7).abs() < 1e-4);
        }
        // the frames' areas are the mesh's own (a polyhedron inscribed in the sphere: ~1% below 4 pi)
        let direct: f64 = p
            .t
            .iter()
            .map(|t| 0.5 * (p.p[t[1] as usize] - p.p[t[0] as usize]).cross(p.p[t[2] as usize] - p.p[t[0] as usize]).length() as f64)
            .sum();
        assert!((area - direct).abs() < 1e-3 * direct, "{area} vs {direct}");
        assert!((area / (4.0 * std::f64::consts::PI) - 1.0).abs() < 0.02, "area {area}");
    }

    #[test]
    fn sphere_curvature_is_isotropic_and_uniform() {
        let p = proxy_of(&icosphere(5), 0.12);
        let f = Frames::build(&p);
        let c = compute_curvature(&p, &f, 0.36);
        let mut ratio: Vec<f32> = (0..p.t.len()).map(|t| c.anisotropy[t] / c.magnitude[t].max(1e-9)).collect();
        ratio.sort_by(|a, b| a.total_cmp(b));
        let mean = c.magnitude.iter().sum::<f32>() / c.magnitude.len() as f32;
        let var = c.magnitude.iter().map(|m| (m - mean) * (m - mean)).sum::<f32>() / c.magnitude.len() as f32;
        println!("sphere curvature: magnitude {mean:.3} +- {:.3}, median anisotropy/magnitude {:.3}", var.sqrt(), ratio[ratio.len() / 2]);
        assert!(mean > 0.1, "magnitude {mean}");
        assert!(var.sqrt() / mean < 0.3, "magnitude varies by {:.0}%", 100.0 * var.sqrt() / mean);
        assert!(ratio[ratio.len() / 2] < 0.35, "median anisotropy/magnitude {}", ratio[ratio.len() / 2]);
    }

    #[test]
    fn cylinder_curvature_points_along_axis_or_around() {
        let p = proxy_of(&tube(80, 40), 0.15);
        let f = Frames::build(&p);
        let c = compute_curvature(&p, &f, 0.45);
        let inner = |t: usize| centroid(&p, t).z.abs() < 0.6;
        let aligned = fraction(&p, inner, |t| {
            let d = f.direction(t, c.angle[t]);
            d.z.abs() > 0.95 || d.z.abs() < 0.05
        });
        let aniso = fraction(&p, inner, |t| c.anisotropy[t] > 0.7 * c.magnitude[t]);
        println!("cylinder curvature: {:.0}% aligned, {:.0}% anisotropic", aligned * 100.0, aniso * 100.0);
        assert!(aligned > 0.9, "{aligned}");
        assert!(aniso > 0.9, "{aniso}");
    }

    #[test]
    fn plane_field_follows_the_borders_without_singularities() {
        let p = proxy_of(&grid_plane(30), 0.1);
        let (f, _, field) = solve(&p, 0.01);
        let aligned = fraction(&p, |_| true, |t| {
            let d = f.direction(t, field.theta[t]);
            d.x.abs().min(d.y.abs()) < 0.05
        });
        println!("plane: {:.1}% aligned, {} singularities", aligned * 100.0, field.singularities);
        assert!(aligned > 0.97, "{aligned}");
        assert_eq!(field.singularities, 0);
        assert!(field.vertex_index.iter().all(|&q| q == 0));
    }

    /// Poincare-Hopf for a 4-RoSy field: the indices (in quarter turns) add up to 4 x Euler characteristic.
    #[test]
    fn sphere_field_indices_add_up_to_eight() {
        let p = proxy_of(&icosphere(5), 0.12);
        let (_, _, field) = solve(&p, 0.01);
        let sum: i32 = field.vertex_index.iter().map(|&q| q as i32).sum();
        let plus = field.vertex_index.iter().filter(|&&q| q > 0).count();
        let minus = field.vertex_index.iter().filter(|&&q| q < 0).count();
        println!("sphere: {} singularities ({plus} positive, {minus} negative), index sum {sum}", field.singularities);
        assert_eq!(sum, 8);
        assert!(field.singularities >= 8 && field.singularities <= 40, "{} singularities", field.singularities);
    }

    /// The same sphere as a UV sphere with needle fans at the poles: the proxy must be as good as for the icosphere, so
    /// the field has no more than the eight unavoidable singularities (it had over a hundred before the proxy fix).
    #[test]
    fn uv_sphere_field_has_the_minimum_of_eight_singularities() {
        let src = crate::testmesh::uv_sphere(150, 600);
        let p = proxy_of(&src, 0.1);
        let (_, _, field) = solve(&p, 0.01);
        let sum: i32 = field.vertex_index.iter().map(|&q| q as i32).sum();
        println!("uv sphere: {} singularities, index sum {sum}", field.singularities);
        assert_eq!(sum, 8);
        assert!(field.singularities <= 12, "{} singularities", field.singularities);
    }

    #[test]
    fn torus_field_indices_add_up_to_zero() {
        let p = proxy_of(&torus(1.0, 0.4, 80, 40), 0.1);
        let (_, _, field) = solve(&p, 0.01);
        let sum: i32 = field.vertex_index.iter().map(|&q| q as i32).sum();
        println!("torus: {} singularities, index sum {sum}", field.singularities);
        assert_eq!(sum, 0);
    }

    #[test]
    fn cube_field_aligns_with_the_hard_edges() {
        let p = proxy_of(&grid_cube(10), 0.25);
        let (f, _, field) = solve(&p, 0.01);
        let aligned = fraction(&p, |_| true, |t| {
            let d = f.direction(t, field.theta[t]);
            d.x.abs().max(d.y.abs()).max(d.z.abs()) > 0.97
        });
        let sum: i32 = field.vertex_index.iter().map(|&q| q as i32).sum();
        println!("cube: {:.1}% axis-aligned, {} singularities, index sum {sum}", aligned * 100.0, field.singularities);
        assert!(aligned > 0.95, "{aligned}");
        assert_eq!(sum, 8);
    }

    #[test]
    fn cylinder_field_runs_along_axis_or_around() {
        let p = proxy_of(&tube(80, 40), 0.15);
        let (f, _, field) = solve(&p, 0.01);
        let aligned = fraction(&p, |_| true, |t| {
            let d = f.direction(t, field.theta[t]);
            d.z.abs() > 0.95 || d.z.abs() < 0.05
        });
        println!("cylinder: {:.1}% aligned, {} singularities", aligned * 100.0, field.singularities);
        assert!(aligned > 0.95, "{aligned}");
    }

    #[test]
    fn matchings_of_twin_corners_cancel() {
        let p = proxy_of(&icosphere(4), 0.2);
        let (_, _, field) = solve(&p, 0.01);
        for c in 0..p.opp.len() {
            let o = p.opp[c];
            if o != NONE {
                assert_eq!((field.matching[c] + field.matching[o as usize]) % 4, 0, "corner {c}");
            }
        }
    }

    #[test]
    fn solve_is_deterministic_and_thread_independent() {
        let p = proxy_of(&grid_cube(8), 0.3);
        let run = |threads: usize| {
            let pool = rayon::ThreadPoolBuilder::new().num_threads(threads).build().unwrap();
            pool.install(|| solve(&p, 0.01).2.theta)
        };
        assert_eq!(run(1), run(4));
    }

    #[test]
    fn cancellation_returns_none() {
        let p = proxy_of(&grid_cube(8), 0.3);
        let f = Frames::build(&p);
        let c = compute_curvature(&p, &f, 0.3);
        assert!(solve_cross_field(&p, &f, &c, &FieldSettings::default(), &|| true).is_none());
    }

    #[test]
    fn debug_mesh_has_two_quads_per_triangle() {
        let p = proxy_of(&icosphere(3), 0.3);
        let (f, _, field) = solve(&p, 0.01);
        let m = field_debug_mesh(&p, &f, &field, 0.1, 0.01);
        assert_eq!(m.face_count(), 2 * p.t.len());
        assert!(m.validate().is_ok());
    }
}
