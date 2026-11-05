// SPDX-License-Identifier: GPL-3.0-or-later
//! Loop-aware relaxation of the extracted quads and their projection back onto the source. Port of SculptForge's
//! `relaxLoops` and the untangling step of `QRemesh2.cpp` (GPL-3.0-or-later).
//!
//! Tangential smoothing on the source in which a vertex may not move across a loop that runs along a ridge (the
//! curvature across the loop is high), so loops placed on ridges by the field stay on them. Vertices on feature lines
//! slide along them; where feature lines meet they stay.

use crate::bvh::{SegmentBvh, TriangleBvh};
use crate::field::Curvature;
use crate::proxy::{Proxy, NONE};
use crate::vec3::V3;
use crate::workmesh::edge_key;
use rayon::prelude::*;
use std::collections::HashMap;

pub struct RelaxTargets<'a> {
    pub surface: &'a TriangleBvh,
    /// Feature lines of the proxy (including open edges).
    pub features: Option<&'a SegmentBvh>,
    pub proxy: &'a Proxy,
    pub curvature: &'a Curvature,
    pub proxy_bvh: &'a TriangleBvh,
    /// Wanted quad edge.
    pub h: f32,
}

/// 0 = free, 1 = on a feature line (slides along it), 2 = pinned (lines meet or turn).
pub type FeatureKind = u8;

/// Relaxes `p` in place; returns each vertex's feature kind.
pub fn relax_loops(p: &mut [V3], quads: &[[u32; 4]], rt: &RelaxTargets, iterations: usize) -> Vec<FeatureKind> {
    let nv = p.len();
    let mut nb: Vec<Vec<u32>> = vec![Vec::new(); nv];
    let mut usage: HashMap<u64, u32> = HashMap::new();
    for q in quads {
        for k in 0..4 {
            let (a, b) = (q[k], q[(k + 1) % 4]);
            let u = usage.entry(edge_key(a, b)).or_insert(0);
            *u += 1;
            if *u == 1 {
                nb[a as usize].push(b);
                nb[b as usize].push(a);
            }
        }
    }
    // Feature vertices: on a proxy feature line (within a small distance); corners: 3+ feature neighbours or a turn.
    let mut feat: Vec<FeatureKind> = vec![0; nv];
    if let Some(features) = rt.features {
        for v in 0..nv {
            if features.closest_point(p[v], 0.05 * rt.h).is_some() {
                feat[v] = 1;
            }
        }
    }
    let mut line: Vec<[u32; 2]> = vec![[NONE; 2]; nv];
    if let Some(features) = rt.features {
        for v in 0..nv {
            if feat[v] == 0 {
                continue;
            }
            // The edge runs along the line: its middle is on it too.
            let f: Vec<u32> = nb[v]
                .iter()
                .copied()
                .filter(|&u| feat[u as usize] != 0 && features.closest_point((p[u as usize] + p[v]) * 0.5, 0.05 * rt.h).is_some())
                .collect();
            if f.len() == 2 {
                let a = (p[f[0] as usize] - p[v]).normalized();
                let b = (p[f[1] as usize] - p[v]).normalized();
                if a.dot(b) < -0.7 {
                    line[v] = [f[0], f[1]];
                    continue;
                }
            }
            feat[v] = 2;
        }
    }
    // Per vertex: the curvature tensor's strongest direction and anisotropy (from the proxy triangle under it).
    let ridge_info: Vec<(V3, f32)> = (0..nv)
        .into_par_iter()
        .map(|v| {
            let Some(hit) = rt.proxy_bvh.closest_point(p[v], 2.0 * rt.h) else { return (V3::ZERO, 0.0) };
            let t = hit.prim as usize;
            let tri = rt.proxy.t[t];
            let (a, b, c) = (rt.proxy.p[tri[0] as usize], rt.proxy.p[tri[1] as usize], rt.proxy.p[tri[2] as usize]);
            let n = (b - a).cross(c - a).normalized();
            let e1 = (b - a).normalized();
            let e2 = n.cross(e1);
            let ang = rt.curvature.angle[t];
            (e1 * ang.cos() + e2 * ang.sin(), (rt.curvature.anisotropy[t] * rt.h / 0.5).min(1.0))
        })
        .collect();
    let p0: Vec<V3> = p.to_vec();
    let mut cur = p0;
    for _ in 0..iterations {
        // Normals from the quads.
        let mut normals = vec![V3::ZERO; nv];
        for q in quads {
            let n = (cur[q[2] as usize] - cur[q[0] as usize]).cross(cur[q[3] as usize] - cur[q[1] as usize]);
            for &v in q {
                normals[v as usize] += n;
            }
        }
        for n in normals.iter_mut() {
            *n = n.normalized();
        }
        let next: Vec<V3> = (0..nv)
            .into_par_iter()
            .map(|v| {
                let pv = cur[v];
                if feat[v] == 2 || nb[v].is_empty() {
                    return pv;
                }
                if feat[v] == 1 {
                    let c = (cur[line[v][0] as usize] + cur[line[v][1] as usize]) * 0.5;
                    let mut q = pv + (c - pv) * 0.5;
                    if let Some(h) = rt.features.and_then(|f| f.closest_point(q, rt.h)) {
                        q = h.position;
                    }
                    return q;
                }
                let mut c = V3::ZERO;
                for &u in &nb[v] {
                    c += cur[u as usize];
                }
                c /= nb[v].len() as f32;
                let n = normals[v];
                let mut d = c - pv;
                d -= n * n.dot(d);
                // Moving along the strongest bending direction would pull a loop lying along the ridge off it.
                let (ridge_dir, ridge) = ridge_info[v];
                if ridge > 0.0 {
                    let rd = (ridge_dir - n * n.dot(ridge_dir)).safe_normalize(V3::ZERO);
                    d -= rd * (0.85 * ridge * rd.dot(d));
                }
                let mut q = pv + d * 0.5;
                if let Some(h) = rt.surface.closest_point(q, 2.0 * rt.h) {
                    q = h.position;
                }
                q
            })
            .collect();
        cur = next;
    }
    // Final projection of everything onto the source (feature vertices onto their lines).
    let fin: Vec<V3> = (0..nv)
        .into_par_iter()
        .map(|v| {
            if feat[v] != 0 {
                if let Some(h) = rt.features.and_then(|f| f.closest_point(cur[v], 0.25 * rt.h)) {
                    return h.position;
                }
            }
            rt.surface.closest_point(cur[v], 2.0 * rt.h).map_or(cur[v], |h| h.position)
        })
        .collect();
    p.copy_from_slice(&fin);
    feat
}

/// Untangle: a quad turned against its neighbours (a fold left by a repair) gets its vertices moved to their
/// neighbours' average and back onto the source. Returns how many vertex moves it made.
pub fn untangle(p: &mut [V3], quads: &[[u32; 4]], feat: &[FeatureKind], surface: &TriangleBvh, h: f32) -> usize {
    let nv = p.len();
    let mut nbq: Vec<Vec<u32>> = vec![Vec::new(); nv];
    let mut vq: Vec<Vec<u32>> = vec![Vec::new(); nv];
    for (f, q) in quads.iter().enumerate() {
        for k in 0..4 {
            vq[q[k] as usize].push(f as u32);
            nbq[q[k] as usize].push(q[(k + 1) % 4]);
            nbq[q[k] as usize].push(q[(k + 3) % 4]);
        }
    }
    let is_feat = |v: usize| v < feat.len() && feat[v] != 0;
    let mut moved = 0;
    for _ in 0..8 {
        let qn: Vec<V3> = quads
            .iter()
            .map(|q| (p[q[2] as usize] - p[q[0] as usize]).cross(p[q[3] as usize] - p[q[1] as usize]).safe_normalize(V3::ZERO))
            .collect();
        let mut bad: Vec<u32> = Vec::new();
        for (f, q) in quads.iter().enumerate() {
            let mut around = V3::ZERO;
            for &v in q {
                for &g in &vq[v as usize] {
                    around += qn[g as usize];
                }
            }
            if qn[f].dot(around - qn[f] * 4.0) < 0.0 {
                bad.extend_from_slice(q);
            }
        }
        if bad.is_empty() {
            break;
        }
        bad.sort_unstable();
        bad.dedup();
        for v in bad {
            let v = v as usize;
            if is_feat(v) || nbq[v].is_empty() {
                continue;
            }
            let mut c = V3::ZERO;
            for &u in &nbq[v] {
                c += p[u as usize];
            }
            c /= nbq[v].len() as f32;
            p[v] = surface.closest_point(c, 2.0 * h).map_or(c, |hit| hit.position);
            moved += 1;
        }
    }
    moved
}
