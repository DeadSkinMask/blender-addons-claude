// SPDX-License-Identifier: GPL-3.0-or-later
//! Quad remeshing of a piece (a connected component of the input) by an integer-grid map. Port of the outline of
//! SculptForge's `QRemesh2.cpp` (GPL-3.0-or-later):
//!
//!  1. Proxy surface (`proxy.rs`): the piece decimated and remeshed to about three triangle edges per quad edge,
//!     feature lines (open borders, material borders, hard edges) kept.
//!  2. Cross field (`field.rs`) following smoothed principal curvature directions and the features, solved on a copy
//!     of the proxy smoothed at the quad scale, so the singularities come from the form, not from bumps smaller than
//!     a quad.
//!  3. Integer-grid map (`gridmap.rs`): its integer lattice lines are the edge loops; feature lines lie exactly on
//!     lattice lines and singularities on lattice points.
//!  4. Extraction (`extract.rs`): proxy vertices rounding to the same lattice point form one quad vertex, lattice
//!     neighbours are joined, faces traced; the rare non-quad faces are paired off along short paths and split.
//!  5. Loop-aware relaxation and projection onto the source (`relax.rs`).
//!
//! Sizes: uniform, or varying with the curvature (`adaptivness`) and the vertex paint (`use_colors`); see
//! `relative_sizes`. Not in yet: symmetry and guide curves (the options exist in the settings).

use crate::bvh::{SegmentBvh, TriangleBvh};
use crate::extract::{all_quads, clean_quads, extract_lattice, quads_manifold, repair_manifold};
use crate::field::{compute_curvature, solve_cross_field, CrossField, FieldSettings, Frames};
use crate::gridmap::{solve_grid_map, GridMap};
use crate::proxy::{build_proxy, Proxy, MAX_PROXY_TRIANGLES, NONE};
use crate::relax::{relax_loops, untangle, RelaxTargets};
use crate::vec3::V3;
use crate::workmesh::{detect_features, edge_key, WorkMesh};
use std::collections::{HashMap, HashSet};

#[derive(Clone, Debug)]
pub struct PieceOptions {
    /// Wanted number of quads.
    pub want_quads: f64,
    /// Edge loops along material (group) borders.
    pub keep_groups: bool,
    /// Hard edges (faces meeting at more than 60 degrees) get an edge loop.
    pub detect_edges: bool,
    /// Hard edges marked by the user, as vertex pairs of the piece.
    pub user_sharp: Vec<[u32; 2]>,
    /// Relaxation passes.
    pub smooth_iterations: usize,
    /// 0..100: how much smaller the quads get where the surface bends (and larger where it is flat); 0 = uniform.
    pub adaptivness: f32,
    /// Vertex colors of the piece set the density (brighter = smaller quads, mid grey = neutral).
    pub use_colors: bool,
    /// Curves on the surface (closed flag, points) the edge flow follows: near them the cross field is pulled to
    /// their tangent (softly, so a loop runs along the curve without being forced exactly onto it).
    pub guides: Vec<(bool, Vec<V3>)>,
    /// 0..100: how far from a guide it pulls (about 1 + 3 * strength / 100 quad edges).
    pub guide_strength: f32,
}

impl Default for PieceOptions {
    fn default() -> Self {
        PieceOptions { want_quads: 5000.0, keep_groups: false, detect_edges: true, user_sharp: Vec::new(), smooth_iterations: 6, adaptivness: 0.0, use_colors: false, guides: Vec::new(), guide_strength: 50.0 }
    }
}

#[derive(Clone, Debug, Default)]
pub struct QuadMesh {
    pub p: Vec<V3>,
    pub quads: Vec<[u32; 4]>,
    /// Odd faces left without a partner (rare), so the surface stays closed.
    pub tris: Vec<[u32; 3]>,
}

#[derive(Clone, Debug, Default)]
pub struct PieceResult {
    pub mesh: QuadMesh,
    /// Cross-field singularities (the poles the layout needs).
    pub singularities: usize,
    /// Parametrizations solved (the count is corrected once when far off).
    pub attempts: usize,
    /// Proxy triangles the map turned over (should be ~0).
    pub flipped: usize,
    pub proxy_triangles: usize,
    /// Index into `DENSITIES` of the proxy density that produced this result.
    pub density_try: usize,
    /// The source group (material) of every face: the quads first, then the leftover triangles.
    pub groups: Vec<u32>,
}

#[derive(Debug)]
pub enum PieceError {
    Cancelled,
    Failed(String),
}

fn fail<T>(why: &str) -> Result<T, PieceError> {
    Err(PieceError::Failed(why.to_string()))
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

/// Hard edges checked at the proxy's scale: a chain whose faces meet at less than ~55 degrees there (on average), or
/// shorter than three quads, is sculpt detail (a scan's crease), not a hard edge, and stops being a feature.
fn demote_soft_edges(px: &mut Proxy, keep_groups: bool, h0: f32) -> usize {
    let nc = px.sharp.len();
    let mut uf = UnionFind::new(px.p.len());
    for c in 0..nc {
        if px.sharp[c] != 0 && px.opp[c] != NONE {
            uf.unite(px.t[c / 3][c % 3], px.t[c / 3][(c % 3 + 1) % 3]);
        }
    }
    let mut chain: HashMap<u32, (f64, f64)> = HashMap::new(); // root -> (length, length-weighted dihedral)
    for c in 0..nc {
        let o = px.opp[c];
        if px.sharp[c] == 0 || o == NONE || o < c as u32 {
            continue;
        }
        let (a, b) = (px.t[c / 3], px.t[(o / 3) as usize]);
        let normal = |t: [u32; 3]| (px.p[t[1] as usize] - px.p[t[0] as usize]).cross(px.p[t[2] as usize] - px.p[t[0] as usize]).normalized();
        let (na, nb) = (normal(a), normal(b));
        let len = (px.p[a[(c % 3 + 1) % 3] as usize] - px.p[a[c % 3] as usize]).length() as f64;
        let e = chain.entry(uf.find(a[c % 3])).or_insert((0.0, 0.0));
        e.0 += len;
        e.1 += len * (na.dot(nb) as f64).clamp(-1.0, 1.0).acos();
    }
    let mut demoted = 0;
    for c in 0..nc {
        if px.sharp[c] == 0 {
            continue;
        }
        let o = px.opp[c];
        if o == NONE {
            continue;
        }
        let e = chain.get(&uf.find(px.t[c / 3][c % 3])).copied().unwrap_or((0.0, 0.0));
        let mean_angle = if e.0 > 0.0 { e.1 / e.0 } else { 0.0 };
        if e.0 < 3.0 * h0 as f64 || mean_angle < 55f64.to_radians() {
            let other = keep_groups && px.group[c / 3] != px.group[(o / 3) as usize];
            if !other {
                px.feature[c] = 0;
                demoted += 1;
            }
            px.sharp[c] = 0;
        }
    }
    demoted
}

/// Proxy densities in the order they are tried. No density wins everywhere (a sweep over 9 shapes x 5 sizes gave
/// 504 / 437 / 503 / 487 total poles for 2.5 / 3.0 / 3.2 / 4.0), so 3.0 goes first and the others are fallbacks.
const DENSITIES: [f32; 3] = [3.0, 2.5, 3.2];

/// Remeshes one piece into quads. Tries up to three proxy densities: the first result that is a 2-manifold with
/// few poles (at most 1.5 x the field's singularities + 4) is taken at once; otherwise the manifold result with the
/// fewest poles (then the smallest angle error) wins. `progress` gets values in 0..1 and returns false to cancel.
pub fn remesh_piece(piece: &WorkMesh, opt: &PieceOptions, progress: &(dyn Fn(f32) -> bool + Sync)) -> Result<PieceResult, PieceError> {
    // A source much coarser than the quads asked for is facets, not a surface: round it off first (subdiv.rs).
    let area = piece.area();
    let quad_edge = if area > 0.0 && opt.want_quads >= 1.0 { (area / opt.want_quads).sqrt() as f32 } else { 0.0 };
    if let Some(up) = crate::subdiv::upsample(piece, &opt.user_sharp, 0.6 * quad_edge, 40f32.to_radians().cos(), 12, 1_500_000) {
        let opt = PieceOptions { user_sharp: up.sharp, ..opt.clone() };
        return remesh_densities(&up.mesh, &opt, progress);
    }
    remesh_densities(piece, opt, progress)
}

fn remesh_densities(piece: &WorkMesh, opt: &PieceOptions, progress: &(dyn Fn(f32) -> bool + Sync)) -> Result<PieceResult, PieceError> {
    let mut last = String::from("no result");
    let mut best: Option<(usize, f32, PieceResult)> = None;
    for (i, density) in DENSITIES.into_iter().enumerate() {
        match remesh_piece_once(piece, opt, density, progress) {
            Ok(mut r) => {
                if quads_manifold(r.mesh.p.len(), &r.mesh.quads, &r.mesh.tris) {
                    r.density_try = i;
                    let q = quad_quality(&r.mesh);
                    if q.poles as f32 <= 1.5 * r.singularities as f32 + 4.0 {
                        return Ok(r);
                    }
                    if best.as_ref().is_none_or(|b| (q.poles, q.angle_deviation) < (b.0, b.1)) {
                        best = Some((q.poles, q.angle_deviation, r));
                    }
                } else {
                    last = "the quads are not a 2-manifold".into();
                }
            }
            Err(PieceError::Cancelled) => return Err(PieceError::Cancelled),
            Err(PieceError::Failed(why)) => last = why,
        }
    }
    match best {
        Some((_, _, r)) => Ok(r),
        None => Err(PieceError::Failed(last)),
    }
}

/// Guide curves as short segments lying on the surface: resampled about every third of a quad edge, the samples put
/// on the surface (a sample farther than 2.5 quad edges from it breaks the curve there: the curve may lie on another
/// piece).
fn guide_segments(surface: &TriangleBvh, guides: &[(bool, Vec<V3>)], h0: f32) -> (Vec<V3>, Vec<V3>) {
    let step = h0 / 3.0;
    let (mut sa, mut sb) = (Vec::new(), Vec::new());
    for (closed, pts) in guides {
        if pts.len() < 2 {
            continue;
        }
        let mut samples: Vec<V3> = Vec::new();
        let n = if *closed { pts.len() } else { pts.len() - 1 };
        for i in 0..n {
            let (a, b) = (pts[i], pts[(i + 1) % pts.len()]);
            let k = (((b - a).length() / step).ceil() as usize).max(1);
            for j in 0..k {
                samples.push(a + (b - a) * (j as f32 / k as f32));
            }
        }
        if !*closed {
            samples.push(pts[pts.len() - 1]);
        }
        let on: Vec<Option<V3>> = samples.iter().map(|&q| surface.closest_point(q, 2.5 * h0).map(|h| h.position)).collect();
        let m = on.len();
        let last = if *closed { m } else { m - 1 };
        for i in 0..last {
            if let (Some(a), Some(b)) = (on[i], on[(i + 1) % m]) {
                if (a - b).length2() > 1e-12 {
                    sa.push(a);
                    sb.push(b);
                }
            }
        }
    }
    (sa, sb)
}

/// `RR_TIMING=1` prints how long each stage of a piece took (stderr).
fn stage(t0: &std::time::Instant, name: &str) {
    if std::env::var_os("RR_TIMING").is_some() {
        eprintln!("    stage {name:<12} at {:.2} s", t0.elapsed().as_secs_f64());
    }
}

fn smoothstep(a: f64, b: f64, x: f64) -> f64 {
    let t = ((x - a) / (b - a)).clamp(0.0, 1.0);
    t * t * (3.0 - 2.0 * t)
}

/// Relative wanted quad size per proxy vertex (geometric mean 1). Smaller where the surface bends and larger where it
/// is flat (`adaptivness`: at 100 the sizes span about 0.5 to 1.5 of the average) and smaller where the vertex paint
/// is bright (a factor 3 each way at white / black). Then limited so neighbours differ by at most 35% per quad edge
/// and smoothed, since the map follows the size's gradient.
fn relative_sizes(proxy: &Proxy, frames: &Frames, curv: &crate::field::Curvature, piece: &WorkMesh, surface: &TriangleBvh, opt: &PieceOptions, h0: f32) -> Vec<f32> {
    let nv = proxy.p.len();
    let mut lg = vec![0f64; nv];
    let a = (opt.adaptivness as f64 / 100.0).clamp(0.0, 1.0);
    if a > 0.0 {
        let (mut sum, mut w) = (vec![0f64; nv], vec![0f64; nv]);
        for (t, tri) in proxy.t.iter().enumerate() {
            let ar = frames.area[t] as f64;
            for &v in tri {
                sum[v as usize] += ar * curv.magnitude[t] as f64;
                w[v as usize] += ar;
            }
        }
        for v in 0..nv {
            let rel = if w[v] > 0.0 { sum[v] / w[v] * h0 as f64 } else { 0.0 };
            lg[v] += (1.0 + 1.0 * a * (0.5 - smoothstep(0.04, 0.5, rel))).ln();
        }
    }
    if opt.use_colors && !piece.color.is_empty() {
        for v in 0..nv {
            if let Some(hit) = surface.closest_point(proxy.p[v], 4.0 * h0) {
                let tri = piece.t[hit.prim as usize];
                let c = piece.color[tri[0] as usize] * hit.bary.x + piece.color[tri[1] as usize] * hit.bary.y + piece.color[tri[2] as usize] * hit.bary.z;
                let lum = (0.2126 * c.x + 0.7152 * c.y + 0.0722 * c.z) as f64;
                lg[v] -= (lum.clamp(0.0, 1.0) - 0.5) * 2.0 * 3f64.ln();
            }
        }
    }
    if lg.iter().all(|&x| x == 0.0) {
        return vec![1.0; nv];
    }
    let mut edges: Vec<(u32, u32, f64)> = Vec::new();
    for c in 0..proxy.opp.len() {
        if proxy.opp[c] == NONE || proxy.opp[c] > c as u32 {
            let (x, y) = (proxy.t[c / 3][c % 3], proxy.t[c / 3][(c % 3 + 1) % 3]);
            edges.push((x, y, ((proxy.p[x as usize] - proxy.p[y as usize]).length() / h0) as f64));
        }
    }
    let limit = 1.35f64.ln();
    for _ in 0..200 {
        let mut changed = false;
        for &(x, y, l) in &edges {
            let lim = limit * l.max(0.05);
            let (gx, gy) = (lg[x as usize], lg[y as usize]);
            if gx > gy + lim {
                lg[x as usize] = gy + lim;
                changed = true;
            } else if gy > gx + lim {
                lg[y as usize] = gx + lim;
                changed = true;
            }
        }
        if !changed {
            break;
        }
    }
    for _ in 0..64 {
        let (mut sum, mut n) = (vec![0f64; nv], vec![0f64; nv]);
        for &(x, y, _) in &edges {
            sum[x as usize] += lg[y as usize];
            n[x as usize] += 1.0;
            sum[y as usize] += lg[x as usize];
            n[y as usize] += 1.0;
        }
        for v in 0..nv {
            if n[v] > 0.0 {
                lg[v] = 0.5 * lg[v] + 0.5 * sum[v] / n[v];
            }
        }
    }
    let mean = lg.iter().sum::<f64>() / nv.max(1) as f64;
    lg.iter().map(|&x| (x - mean).exp() as f32).collect()
}

/// The size c such that vertex sizes `rel * c` give `want` quads over the proxy: sum over triangles of area / size^2.
fn size_base(proxy: &Proxy, frames: &Frames, rel: &[f32], want: f64) -> f64 {
    let mut denom = 0.0;
    for (t, tri) in proxy.t.iter().enumerate() {
        let g = (rel[tri[0] as usize] as f64 + rel[tri[1] as usize] as f64 + rel[tri[2] as usize] as f64) / 3.0;
        denom += frames.area[t] as f64 / (g * g);
    }
    (denom / want).sqrt()
}

fn remesh_piece_once(piece: &WorkMesh, opt: &PieceOptions, density: f32, progress: &(dyn Fn(f32) -> bool + Sync)) -> Result<PieceResult, PieceError> {
    let step = |f: f32| if progress(f) { Ok(()) } else { Err(PieceError::Cancelled) };
    let cancelled = || !progress(-1.0); // a poll: -1 asks only whether to stop
    let t0 = std::time::Instant::now();
    if piece.t.is_empty() {
        return fail("empty piece");
    }
    let area = piece.area();
    if area.is_nan() || area <= 0.0 || opt.want_quads < 1.0 {
        return fail("no area");
    }
    let h0 = (area / opt.want_quads).sqrt() as f32;
    let curv_radius = h0;

    // Source structures: the surface, its feature lines.
    let surface = TriangleBvh::build(piece.p.clone(), piece.t.clone());
    let sharp_cos = if opt.detect_edges { 60f32.to_radians().cos() } else { 2.0 };
    let features = detect_features(piece, opt.keep_groups, sharp_cos, &opt.user_sharp, h0);
    let segments = (!features.seg_a.is_empty()).then(|| SegmentBvh::build(features.seg_a.clone(), features.seg_b.clone()));
    step(0.08)?;

    // 1. Proxy.
    // About three proxy edges per quad edge, at most ~400k proxy triangles. With varying sizes the smallest quads
    // still need about three proxy edges, so the proxy is built again finer when the first one is too coarse.
    let min_edge = (area / (0.433 * MAX_PROXY_TRIANGLES)).sqrt() as f32;
    let mut l0 = (h0 / density).max(min_edge);
    let adaptive = opt.adaptivness > 0.0 || (opt.use_colors && !piece.color.is_empty());
    let mut pass = 0;
    let (mut proxy, frames, curv, rel) = loop {
        let (builder, mut proxy, _vmap) = build_proxy(piece, &surface, segments.as_ref(), features.sharp.clone(), opt.keep_groups, l0, area);
        drop(builder);
        demote_soft_edges(&mut proxy, opt.keep_groups, h0);
        let frames = Frames::build(&proxy);
        proxy.size = vec![h0; proxy.p.len()];
        let curv = compute_curvature(&proxy, &frames, curv_radius);
        // Sizes follow the curvature at a wider scale than the field does: a size that changes within a quad adds
        // singularities (the field turns with the size's gradient).
        let rel = if adaptive {
            let wide = if opt.adaptivness > 0.0 { compute_curvature(&proxy, &frames, 2.5 * curv_radius) } else { curv.clone() };
            relative_sizes(&proxy, &frames, &wide, piece, &surface, opt, h0)
        } else {
            vec![1.0; proxy.p.len()]
        };
        if adaptive && pass == 0 {
            let smallest = rel.iter().cloned().fold(f32::MAX, f32::min) as f64 * size_base(&proxy, &frames, &rel, opt.want_quads);
            let wanted = ((smallest / density as f64) as f32).max(min_edge);
            if wanted < 0.85 * l0 {
                l0 = wanted;
                pass = 1;
                step(0.1)?;
                continue;
            }
        }
        break (proxy, frames, curv, rel);
    };
    step(0.25)?;
    step(0.35)?;
    stage(&t0, "proxy");

    // Sizes: normalised so the proxy's own area holds the wanted number of quads, with `scale` for the smoothed copy
    // the map is solved on. The cross field turns with the size's gradient (`field_size`).
    let base = size_base(&proxy, &frames, &rel, opt.want_quads);
    let set_sizes = |proxy: &mut Proxy, scale: f64| {
        proxy.size = rel.iter().map(|&g| (g as f64 * base * scale) as f32).collect();
    };
    if adaptive {
        proxy.field_size = rel.clone();
    }
    set_sizes(&mut proxy, 1.0);
    let proxy_area: f64 = frames.area.iter().map(|&a| a as f64).sum();

    // 3. Cross field, solved on a copy of the proxy smoothed at the quad scale: the field's holonomy (where it must
    // turn, hence the singularities) then comes from the form, not from bumps smaller than a quad. Angles are
    // relative to each triangle's first edge, so they carry over to the real proxy unchanged.
    let mut fs = FieldSettings::default();
    let mut smooth = proxy.clone();
    {
        let rounds = ((1.5 * (h0 / l0) as f64 * (h0 / l0) as f64) as i64).clamp(0, 200) as usize;
        let nv = smooth.p.len();
        let mut vt: Vec<Vec<u32>> = vec![Vec::new(); nv];
        for (t, tri) in smooth.t.iter().enumerate() {
            for &v in tri {
                vt[v as usize].push(t as u32);
            }
        }
        let mut pinned = vec![false; nv];
        for c in 0..smooth.feature.len() {
            if smooth.feature[c] != 0 {
                pinned[smooth.t[c / 3][c % 3] as usize] = true;
                pinned[smooth.t[c / 3][(c % 3 + 1) % 3] as usize] = true;
            }
        }
        for _ in 0..rounds {
            let next: Vec<V3> = (0..nv)
                .map(|v| {
                    if pinned[v] {
                        return smooth.p[v];
                    }
                    let (mut s, mut n) = (V3::ZERO, 0usize);
                    for &t in &vt[v] {
                        for &x in &smooth.t[t as usize] {
                            s += smooth.p[x as usize];
                            n += 1;
                        }
                    }
                    if n > 0 {
                        smooth.p[v] * 0.5 + (s / n as f32) * 0.5
                    } else {
                        smooth.p[v]
                    }
                })
                .collect();
            smooth.p = next;
        }
    }
    let sframes = Frames::build(&smooth);
    let (ga, gb) = guide_segments(&surface, &opt.guides, h0);
    if !ga.is_empty() {
        let reach = h0 * (1.0 + 3.0 * (opt.guide_strength / 100.0).clamp(0.0, 1.0));
        let bvh = SegmentBvh::build(ga.clone(), gb.clone());
        let nt = smooth.t.len();
        fs.guide_angle = vec![f32::NAN; nt];
        fs.guide_weight = vec![0.0; nt];
        for t in 0..nt {
            let tri = smooth.t[t];
            let c = (smooth.p[tri[0] as usize] + smooth.p[tri[1] as usize] + smooth.p[tri[2] as usize]) * (1.0 / 3.0);
            if let Some(hit) = bvh.closest_point(c, reach) {
                let tangent = gb[hit.prim as usize] - ga[hit.prim as usize];
                let flat = tangent - sframes.n[t] * sframes.n[t].dot(tangent);
                if flat.length2() > 1e-20 {
                    let w = 1.0 - hit.distance / reach;
                    fs.guide_angle[t] = sframes.angle_in(t, flat) as f32;
                    fs.guide_weight[t] = w * w;
                }
            }
        }
    }
    let scurv = compute_curvature(&smooth, &sframes, 0.5 * curv_radius);
    let Some(field) = solve_cross_field(&smooth, &sframes, &scurv, &fs, &cancelled) else {
        return Err(PieceError::Cancelled);
    };
    let singularities = field.singularities;
    step(0.55)?;
    stage(&t0, "field");

    // 4 + 5. Map and extraction; the count is corrected once when it is far off.
    let proxy_bvh = TriangleBvh::build(proxy.p.clone(), proxy.t.clone());
    let (mut fa, mut fb) = (Vec::new(), Vec::new());
    for (t, tri) in proxy.t.iter().enumerate() {
        for k in 0..3 {
            if proxy.feature[3 * t + k] != 0 {
                fa.push(proxy.p[tri[k] as usize]);
                fb.push(proxy.p[tri[(k + 1) % 3] as usize]);
            }
        }
    }
    let proxy_features = (!fa.is_empty()).then(|| SegmentBvh::build(fa, fb));

    // The map is solved on the smoothed proxy, which has less area than the real one: scale the sizes to match.
    let smooth_area: f64 = sframes.area.iter().map(|&a| a as f64).sum();
    let scale = if proxy_area > 0.0 && smooth_area > 0.0 { (smooth_area / proxy_area).sqrt() } else { 1.0 };
    set_sizes(&mut proxy, scale);
    let (mut quads, mut tris, mut qp): (Vec<[u32; 4]>, Vec<[u32; 3]>, Vec<V3>) = (Vec::new(), Vec::new(), Vec::new());
    let mut count_fix = 1.0f64; // the map's area aims at want_quads * count_fix unit squares
    let (mut attempts, mut flipped) = (0, 0);
    for attempt in 0..2 {
        let mut f: CrossField = field.clone();
        let mut map = GridMap { want_quads: opt.want_quads * count_fix, ..Default::default() };
        smooth.size = proxy.size.clone();
        if !solve_grid_map(&smooth, &sframes, &mut f, &mut map, &cancelled) {
            return if cancelled() { Err(PieceError::Cancelled) } else { fail("the map could not be solved") };
        }
        flipped = map.flipped;
        attempts = attempt + 1;
        stage(&t0, "map");
        let mut poly = extract_lattice(&proxy, &frames, &map, h0);
        tris.clear();
        quads = all_quads(&mut poly, &mut tris);
        qp = std::mem::take(&mut poly.p);
        clean_quads(&mut qp, &mut quads, &mut tris);
        for _ in 0..3 {
            if quads_manifold(qp.len(), &quads, &tris) {
                break;
            }
            repair_manifold(&mut qp, &mut quads, &mut tris);
            clean_quads(&mut qp, &mut quads, &mut tris);
        }
        stage(&t0, "extract");
        let ratio = quads.len() as f64 / opt.want_quads;
        if quads.is_empty() || attempt == 1 || (ratio - 1.0).abs() < 0.12 {
            break;
        }
        count_fix /= ratio;
        step(0.7)?;
    }
    if quads.len() < 4 {
        return fail("the map produced no quads");
    }
    step(0.85)?;

    // 6. Loop-aware relaxation and projection.
    let rt = RelaxTargets {
        surface: &surface,
        features: proxy_features.as_ref(),
        proxy: &proxy,
        curvature: &curv,
        proxy_bvh: &proxy_bvh,
        h: h0,
    };
    let feat = relax_loops(&mut qp, &quads, &rt, opt.smooth_iterations);
    untangle(&mut qp, &quads, &feat, &surface, h0);
    step(0.95)?;
    stage(&t0, "relax");
    // The source group (material) under each face's centre.
    let group_at = |c: V3| surface.closest_point(c, 4.0 * h0).map_or(0, |h| piece.group.get(h.prim as usize).copied().unwrap_or(0));
    let mut groups: Vec<u32> = Vec::with_capacity(quads.len() + tris.len());
    for q in &quads {
        groups.push(group_at((qp[q[0] as usize] + qp[q[1] as usize] + qp[q[2] as usize] + qp[q[3] as usize]) * 0.25));
    }
    for t in &tris {
        groups.push(group_at((qp[t[0] as usize] + qp[t[1] as usize] + qp[t[2] as usize]) * (1.0 / 3.0)));
    }
    Ok(PieceResult {
        groups,
        mesh: QuadMesh { p: qp, quads, tris },
        singularities,
        attempts,
        flipped,
        proxy_triangles: proxy.t.len(),
        density_try: 0,
    })
}

/// How good a quad mesh is.
#[derive(Clone, Debug, Default)]
pub struct QuadQuality {
    pub vertices: usize,
    pub quads: usize,
    pub triangles: usize,
    /// Inside vertices (all their edges shared by two faces) with other than four edges.
    pub poles: usize,
    /// Inside vertices by edge count: [2] .. [6], [7] = 7 or more.
    pub valence: [usize; 8],
    /// Vertices on open edges.
    pub boundary_vertices: usize,
    /// Mean |corner angle - 90| over the quads, degrees.
    pub angle_deviation: f32,
    /// 99th percentile of |corner angle - 90|, degrees.
    pub angle_deviation_p99: f32,
}

pub fn quad_quality(m: &QuadMesh) -> QuadQuality {
    let nv = m.p.len();
    let mut q = QuadQuality { vertices: nv, quads: m.quads.len(), triangles: m.tris.len(), ..Default::default() };
    let mut edges: HashMap<u64, u32> = HashMap::new();
    let mut nbrs: Vec<HashSet<u32>> = vec![HashSet::new(); nv];
    let mut add = |a: u32, b: u32| {
        *edges.entry(edge_key(a, b)).or_insert(0) += 1;
        nbrs[a as usize].insert(b);
        nbrs[b as usize].insert(a);
    };
    for f in &m.quads {
        for k in 0..4 {
            add(f[k], f[(k + 1) % 4]);
        }
    }
    for f in &m.tris {
        for k in 0..3 {
            add(f[k], f[(k + 1) % 3]);
        }
    }
    let mut open = vec![false; nv];
    for (&key, &n) in &edges {
        if n == 1 {
            open[(key >> 32) as usize] = true;
            open[(key & 0xffff_ffff) as usize] = true;
        }
    }
    for v in 0..nv {
        if open[v] {
            q.boundary_vertices += 1;
        } else if !nbrs[v].is_empty() {
            let d = nbrs[v].len();
            q.valence[d.clamp(2, 7)] += 1;
            if d != 4 {
                q.poles += 1;
            }
        }
    }
    let mut dev: Vec<f32> = Vec::with_capacity(4 * m.quads.len());
    for f in &m.quads {
        for k in 0..4 {
            let (a, b, c) = (m.p[f[(k + 3) % 4] as usize], m.p[f[k] as usize], m.p[f[(k + 1) % 4] as usize]);
            let (u, w) = (a - b, c - b);
            dev.push((u.cross(w).length().atan2(u.dot(w)).to_degrees() - 90.0).abs());
        }
    }
    if !dev.is_empty() {
        q.angle_deviation = dev.iter().sum::<f32>() / dev.len() as f32;
        dev.sort_by(|a, b| a.total_cmp(b));
        q.angle_deviation_p99 = dev[((dev.len() - 1) as f64 * 0.99) as usize];
    }
    q
}

/// Splits a mesh into its connected components (by shared vertices): each as its own `WorkMesh` plus, for each of its
/// triangles, ... the vertex map back to the original vertex numbers.
pub struct Component {
    pub mesh: WorkMesh,
    /// Piece vertex -> original vertex.
    pub vertices: Vec<u32>,
}

pub fn split_components(m: &WorkMesh) -> Vec<Component> {
    let nv = m.p.len();
    let mut uf = UnionFind::new(nv);
    for t in &m.t {
        uf.unite(t[0], t[1]);
        uf.unite(t[1], t[2]);
    }
    let mut piece_of_root: HashMap<u32, usize> = HashMap::new();
    let mut comps: Vec<Component> = Vec::new();
    let mut local: Vec<u32> = vec![NONE; nv];
    for (ti, t) in m.t.iter().enumerate() {
        let root = uf.find(t[0]);
        let next = comps.len();
        let ci = *piece_of_root.entry(root).or_insert(next);
        if ci == comps.len() {
            comps.push(Component { mesh: WorkMesh::default(), vertices: Vec::new() });
        }
        let c = &mut comps[ci];
        let mut tri = [0u32; 3];
        for k in 0..3 {
            let v = t[k] as usize;
            if local[v] == NONE {
                local[v] = c.vertices.len() as u32;
                c.vertices.push(t[k]);
                c.mesh.p.push(m.p[v]);
                if !m.color.is_empty() {
                    c.mesh.color.push(m.color[v]);
                }
            }
            tri[k] = local[v];
        }
        c.mesh.t.push(tri);
        c.mesh.group.push(m.group.get(ti).copied().unwrap_or(0));
    }
    comps
}

/// Hard edges (original vertex pairs) that belong to the component, as pairs of its own vertices.
pub fn sharp_for_component(c: &Component, sharp: &[[u32; 2]]) -> Vec<[u32; 2]> {
    let local: HashMap<u32, u32> = c.vertices.iter().enumerate().map(|(i, &v)| (v, i as u32)).collect();
    let mut seen: HashSet<u64> = HashSet::new();
    sharp
        .iter()
        .filter_map(|e| {
            let (a, b) = (*local.get(&e[0])?, *local.get(&e[1])?);
            seen.insert(edge_key(a, b)).then_some([a, b])
        })
        .collect()
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::testmesh::{grid_cube, icosphere, torus, uv_sphere, volume};
    use crate::topology::analyze;

    fn no_progress(_: f32) -> bool {
        true
    }

    fn to_mesh(r: &PieceResult) -> crate::mesh::Mesh {
        let mut faces = Vec::new();
        let mut sizes = Vec::new();
        for q in &r.mesh.quads {
            faces.extend_from_slice(q);
            sizes.push(4);
        }
        for t in &r.mesh.tris {
            faces.extend_from_slice(t);
            sizes.push(3);
        }
        crate::mesh::Mesh {
            positions: r.mesh.p.iter().map(|v| v.to_array()).collect(),
            face_sizes: sizes,
            indices: faces,
            attrs: Default::default(),
        }
    }

    fn remesh(src: &WorkMesh, quads: f64) -> PieceResult {
        let opt = PieceOptions { want_quads: quads, ..Default::default() };
        remesh_piece(src, &opt, &no_progress).expect("remeshing works")
    }

    /// Distance of every vertex from the source surface, and the worst one.
    fn deviation(r: &PieceResult, src: &WorkMesh) -> f32 {
        let bvh = TriangleBvh::build(src.p.clone(), src.t.clone());
        r.mesh.p.iter().map(|&p| bvh.closest_point(p, 1e9).map_or(f32::MAX, |h| h.distance)).fold(0f32, f32::max)
    }

    #[test]
    fn sphere_becomes_a_closed_all_quad_mesh_with_eight_poles() {
        let src = icosphere(5);
        let r = remesh(&src, 500.0);
        let m = to_mesh(&r);
        let st = analyze(&m);
        println!(
            "sphere: {} quads ({} wanted), {} tris, {} vertices, singularities {}, {} flipped, {} attempts",
            r.mesh.quads.len(), 500, r.mesh.tris.len(), r.mesh.p.len(), r.singularities, r.flipped, r.attempts
        );
        assert!(st.is_closed_manifold() && st.euler == 2 && st.components == 1, "{st:?}");
        assert!(r.mesh.tris.is_empty(), "{} triangles left over", r.mesh.tris.len());
        assert!((r.mesh.quads.len() as f64 / 500.0 - 1.0).abs() < 0.15, "{} quads for 500", r.mesh.quads.len());
        // valence: all 4 except exactly the field's singularities (8 of valence 3 on a sphere)
        let mut val = vec![0u32; r.mesh.p.len()];
        for q in &r.mesh.quads {
            for &v in q {
                val[v as usize] += 1;
            }
        }
        let irregular = val.iter().filter(|&&v| v != 4).count();
        let triple = val.iter().filter(|&&v| v == 3).count();
        println!("sphere: {irregular} irregular vertices ({triple} of valence 3)");
        assert!(irregular <= 14, "{irregular} irregular vertices");
        assert!(triple >= 6, "{triple} poles of valence 3");
        // stays on the sphere and faces outward
        assert!(deviation(&r, &src) < 0.01, "deviation {}", deviation(&r, &src));
        let out = WorkMesh { p: r.mesh.p.clone(), t: r.mesh.quads.iter().flat_map(|q| [[q[0], q[1], q[2]], [q[0], q[2], q[3]]]).collect(), group: vec![], color: vec![] };
        assert!(volume(&out) > 0.0, "turned inside out");
    }

    /// Regression: the face tracing used to stop at 64 vertices, so the "outside" face of an open surface with a
    /// longer border (a hemisphere's rim here, ~250) came out in pieces that ended up inside the mesh as odd faces
    /// with huge diagonals and a few dozen extra poles.
    #[test]
    fn open_surface_with_a_long_border_has_no_stray_faces() {
        let sphere = crate::testmesh::uv_sphere(96, 64);
        let keep: Vec<[u32; 3]> = sphere.t.iter().copied().filter(|t| t.iter().map(|&v| sphere.p[v as usize].z).sum::<f32>() > 0.0).collect();
        let mut remap = vec![u32::MAX; sphere.p.len()];
        let mut p = Vec::new();
        let t: Vec<[u32; 3]> = keep
            .iter()
            .map(|tri| {
                tri.map(|v| {
                    if remap[v as usize] == u32::MAX {
                        remap[v as usize] = p.len() as u32;
                        p.push(sphere.p[v as usize]);
                    }
                    remap[v as usize]
                })
            })
            .collect();
        let src = WorkMesh { p, t, group: vec![], color: vec![] };
        let r = remesh(&src, 6000.0);
        let q = quad_quality(&r.mesh);
        assert!(q.boundary_vertices > 150, "{} boundary vertices", q.boundary_vertices);
        assert!(r.mesh.tris.is_empty(), "{} triangles left over", r.mesh.tris.len());
        assert!(q.poles <= 12, "{} poles (the field has {})", q.poles, r.singularities);
        let mut longest = 0f32;
        for f in &r.mesh.quads {
            for k in 0..4 {
                longest = longest.max((r.mesh.p[f[k] as usize] - r.mesh.p[f[(k + 1) % 4] as usize]).length());
            }
        }
        let h = (src.area() / r.mesh.quads.len() as f64).sqrt() as f32;
        assert!(longest < 3.0 * h, "longest edge {longest} for a quad edge of {h}");
    }

    fn quad_centres(r: &PieceResult) -> Vec<V3> {
        r.mesh.quads.iter().map(|q| (r.mesh.p[q[0] as usize] + r.mesh.p[q[1] as usize] + r.mesh.p[q[2] as usize] + r.mesh.p[q[3] as usize]) * 0.25).collect()
    }

    #[test]
    fn vertex_paint_puts_more_quads_where_it_is_bright() {
        let mut src = crate::testmesh::grid_plane(60);
        src.color = src.p.iter().map(|p| if p.x > 0.5 { crate::vec3::v3(1.0, 1.0, 1.0) } else { crate::vec3::v3(0.5, 0.5, 0.5) }).collect();
        let opt = PieceOptions { want_quads: 1200.0, use_colors: true, ..Default::default() };
        let r = remesh_piece(&src, &opt, &no_progress).expect("remeshing works");
        let c = quad_centres(&r);
        let (bright, grey) = (c.iter().filter(|p| p.x > 0.6).count(), c.iter().filter(|p| p.x < 0.4).count());
        println!("paint: {bright} quads on the bright side, {grey} on the grey one, {} in all", c.len());
        assert!(bright as f64 > 2.0 * grey as f64, "{bright} vs {grey}");
        assert!((c.len() as f64 / 1200.0 - 1.0).abs() < 0.2, "{} quads for 1200", c.len());
        assert!(r.mesh.tris.is_empty());
    }

    #[test]
    fn curvature_adaptivness_puts_more_quads_on_the_bend() {
        // a strip: flat on one half, rolled into a cylinder (radius 0.15) on the other
        let (nx, ny) = (120usize, 40usize);
        let mut p = Vec::new();
        for j in 0..=ny {
            for i in 0..=nx {
                let (x, y) = (i as f32 / nx as f32 * 2.0, j as f32 / ny as f32);
                let z = if x > 1.0 {
                    let a = (x - 1.0) / 0.15;
                    p.push(crate::vec3::v3(1.0 + 0.15 * a.sin(), y, 0.15 * (1.0 - a.cos())));
                    continue;
                } else {
                    0.0
                };
                p.push(crate::vec3::v3(x, y, z));
            }
        }
        let id = |i: usize, j: usize| (j * (nx + 1) + i) as u32;
        let mut t = Vec::new();
        for j in 0..ny {
            for i in 0..nx {
                t.push([id(i, j), id(i + 1, j), id(i + 1, j + 1)]);
                t.push([id(i, j), id(i + 1, j + 1), id(i, j + 1)]);
            }
        }
        let src = WorkMesh { p, t: t.clone(), group: vec![0; t.len()], color: vec![] };
        let density = |adapt: f32| {
            let opt = PieceOptions { want_quads: 1500.0, adaptivness: adapt, ..Default::default() };
            let r = remesh_piece(&src, &opt, &no_progress).expect("remeshing works");
            let c = quad_centres(&r);
            let bent = c.iter().filter(|p| p.x > 1.2 || p.z > 0.05).count() as f64;
            let flat = c.iter().filter(|p| p.x < 0.8).count() as f64;
            (bent, flat)
        };
        let (b0, f0) = density(0.0);
        let (b1, f1) = density(100.0);
        println!("bend/flat quads: uniform {b0}/{f0}, adaptive {b1}/{f1}");
        // the strip is as long on both halves, so uniform gives about the same count on both; adaptive favours the bend
        assert!((b1 / f1) > 1.25 * (b0 / f0), "uniform {}, adaptive {}", b0 / f0, b1 / f1);
    }

    #[test]
    fn materials_follow_the_faces_to_the_output() {
        let mut src = icosphere(4);
        // upper and lower half are two materials
        src.group = src.t.iter().map(|t| if t.iter().map(|&v| src.p[v as usize].z).sum::<f32>() > 0.0 { 1 } else { 2 }).collect();
        let opt = PieceOptions { want_quads: 600.0, keep_groups: true, ..Default::default() };
        let r = remesh_piece(&src, &opt, &no_progress).expect("remeshing works");
        assert_eq!(r.groups.len(), r.mesh.quads.len() + r.mesh.tris.len());
        let c = quad_centres(&r);
        let wrong = c.iter().enumerate().filter(|(i, p)| r.groups[*i] != if p.z > 0.0 { 1 } else { 2 }).count();
        assert!(wrong <= c.len() / 50, "{wrong} of {} quads have the wrong material", c.len());
        assert!(r.groups.contains(&1) && r.groups.contains(&2));
    }

    #[test]
    fn guide_curves_pull_the_edge_flow_along_them() {
        use crate::vec3::v3;
        let src = crate::testmesh::grid_plane(80);
        let circle: Vec<V3> = (0..96)
            .map(|i| {
                let a = i as f32 / 96.0 * std::f32::consts::TAU;
                v3(0.5 + 0.3 * a.cos(), 0.5 + 0.3 * a.sin(), 0.0)
            })
            .collect();
        // Fraction of the edges near the circle that run along it or across it. On a plain square the flow follows
        // the axes, so near the circle only the edges at 0, 90, 180, 270 degrees do.
        let aligned = |guides: Vec<(bool, Vec<V3>)>| {
            let opt = PieceOptions { want_quads: 1200.0, guides, guide_strength: 100.0, ..Default::default() };
            let r = remesh_piece(&src, &opt, &no_progress).expect("remeshing works");
            let h = (1.0 / r.mesh.quads.len() as f32).sqrt();
            let (mut near, mut good) = (0, 0);
            for q in &r.mesh.quads {
                for k in 0..4 {
                    let (a, b) = (r.mesh.p[q[k] as usize], r.mesh.p[q[(k + 1) % 4] as usize]);
                    let m = (a + b) * 0.5;
                    let (rx, ry) = (m.x - 0.5, m.y - 0.5);
                    let d = ((rx * rx + ry * ry).sqrt() - 0.3).abs();
                    if d > 1.5 * h {
                        continue;
                    }
                    near += 1;
                    let e = (b - a).normalized();
                    let radial = v3(rx, ry, 0.0).normalized();
                    let c = e.dot(radial).abs();
                    if !(0.26..=0.966).contains(&c) {
                        good += 1; // within 15 degrees of tangent or radial
                    }
                }
            }
            good as f32 / near.max(1) as f32
        };
        let (plain, guided) = (aligned(vec![]), aligned(vec![(true, circle)]));
        println!("guides: edges along/across the circle {plain:.2} without, {guided:.2} with");
        assert!(guided > plain + 0.15 && guided > 0.7, "without {plain}, with {guided}");
    }

    /// Regression: a low-poly source (80 triangles) asked for hundreds of quads used to come out as noise (the flat
    /// facets read as creases); it is rounded off first, so the result is a clean sphere, not a faceted one.
    #[test]
    fn a_low_poly_sphere_remeshes_clean_and_round() {
        let src = icosphere(1);
        let r = remesh(&src, 500.0);
        let st = analyze(&to_mesh(&r));
        assert!(st.is_closed_manifold() && st.euler == 2 && st.components == 1, "{st:?}");
        assert!(r.mesh.tris.is_empty());
        let q = quad_quality(&r.mesh);
        assert!(q.poles <= 14, "{} poles", q.poles);
        // vertices lie on the unit sphere itself (the facets of the source are up to 0.07 inside it)
        let worst = r.mesh.p.iter().map(|p| (p.length() - 1.0).abs()).fold(0f32, f32::max);
        assert!(worst < 0.03, "worst radius error {worst}");
    }

    #[test]
    fn torus_has_no_poles_at_all() {
        let src = torus(1.0, 0.4, 80, 40);
        let r = remesh(&src, 800.0);
        let st = analyze(&to_mesh(&r));
        let mut val = vec![0u32; r.mesh.p.len()];
        for q in &r.mesh.quads {
            for &v in q {
                val[v as usize] += 1;
            }
        }
        let irregular = val.iter().filter(|&&v| v != 4).count();
        println!("torus: {} quads, {} tris, {irregular} irregular vertices, {} singularities", r.mesh.quads.len(), r.mesh.tris.len(), r.singularities);
        assert!(st.is_closed_manifold() && st.euler == 0, "{st:?}");
        assert!(irregular <= 8, "{irregular} irregular vertices on a torus");
        assert!((r.mesh.quads.len() as f64 / 800.0 - 1.0).abs() < 0.2, "{} quads", r.mesh.quads.len());
        assert!(deviation(&r, &src) < 0.02);
    }

    #[test]
    fn cube_keeps_its_edges_and_corners() {
        let src = grid_cube(12);
        let r = remesh(&src, 300.0);
        let st = analyze(&to_mesh(&r));
        println!("cube: {} quads, {} tris, singularities {}", r.mesh.quads.len(), r.mesh.tris.len(), r.singularities);
        assert!(st.is_closed_manifold() && st.euler == 2, "{st:?}");
        // all eight corners are vertices, and every vertex on a cube edge stays exactly on it
        for sx in [-1.0f32, 1.0] {
            for sy in [-1.0f32, 1.0] {
                for sz in [-1.0f32, 1.0] {
                    let c = crate::vec3::v3(sx, sy, sz);
                    assert!(r.mesh.p.iter().any(|p| (*p - c).length() < 0.02), "corner {c:?} lost");
                }
            }
        }
        assert!(deviation(&r, &src) < 0.01);
        // quad count near the wanted one; the flat faces are regular grids
        assert!((r.mesh.quads.len() as f64 / 300.0 - 1.0).abs() < 0.25, "{} quads", r.mesh.quads.len());
        // the cube's edges are straight rows of vertices: count vertices lying on an edge (two coordinates at +-1)
        let on_edge = r.mesh.p.iter().filter(|p| [p.x, p.y, p.z].iter().filter(|c| (c.abs() - 1.0).abs() < 0.01).count() >= 2).count();
        assert!(on_edge >= 8 + 12 * 2, "{on_edge} vertices on cube edges");
    }

    #[test]
    fn uv_sphere_with_needle_poles_remeshes_as_well_as_an_icosphere() {
        let src = uv_sphere(120, 400);
        let r = remesh(&src, 600.0);
        let st = analyze(&to_mesh(&r));
        println!("uv sphere: {} quads, {} tris, singularities {}", r.mesh.quads.len(), r.mesh.tris.len(), r.singularities);
        assert!(st.is_closed_manifold() && st.euler == 2, "{st:?}");
        assert!((r.mesh.quads.len() as f64 / 600.0 - 1.0).abs() < 0.2);
        assert!(deviation(&r, &src) < 0.01);
    }

    #[test]
    fn remeshing_is_deterministic() {
        let src = icosphere(4);
        let run = || remesh(&src, 200.0);
        let (a, b) = (run(), run());
        assert_eq!(a.mesh.quads, b.mesh.quads);
        assert_eq!(a.mesh.p, b.mesh.p);
    }

    #[test]
    fn cancellation_is_honoured() {
        let src = icosphere(4);
        let opt = PieceOptions { want_quads: 200.0, ..Default::default() };
        assert!(matches!(remesh_piece(&src, &opt, &|_| false), Err(PieceError::Cancelled)));
    }

    #[test]
    fn components_are_split_and_their_hard_edges_follow() {
        let mut m = icosphere(1);
        let n = m.p.len() as u32;
        let extra: Vec<V3> = m.p.iter().map(|&p| p + crate::vec3::v3(5.0, 0.0, 0.0)).collect();
        let tris: Vec<[u32; 3]> = m.t.iter().map(|t| [t[0] + n, t[1] + n, t[2] + n]).collect();
        m.p.extend(extra);
        m.t.extend(tris);
        m.group = vec![0; m.t.len()];
        let comps = split_components(&m);
        assert_eq!(comps.len(), 2);
        assert!(comps.iter().all(|c| c.mesh.t.len() == 80 && c.vertices.len() == 42));
        let sharp = vec![[0u32, 1u32], [n, n + 1]];
        assert_eq!(sharp_for_component(&comps[0], &sharp).len(), 1);
        assert_eq!(sharp_for_component(&comps[1], &sharp).len(), 1);
    }
}
