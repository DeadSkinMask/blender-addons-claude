// SPDX-License-Identifier: GPL-3.0-or-later
//! Mirror symmetry by halves: the input is cut at the plane `coordinate = 0` (local space) and the positive side is
//! remeshed, with the cut as an open border so the loops run along it; the quads are then reflected and the vertices
//! on the plane welded. The input is assumed symmetric: whatever lies on the negative side is replaced by the
//! reflection of the positive side.

use crate::mesh::Mesh;
use crate::vec3::V3;
use crate::workmesh::{edge_key, WorkMesh};
use std::collections::HashMap;

pub fn coord(p: V3, axis: usize) -> f32 {
    [p.x, p.y, p.z][axis]
}

pub fn with_coord(mut p: V3, axis: usize, v: f32) -> V3 {
    match axis {
        0 => p.x = v,
        1 => p.y = v,
        _ => p.z = v,
    }
    p
}

pub fn reflect(p: V3, axis: usize) -> V3 {
    with_coord(p, axis, -coord(p, axis))
}

/// The part of `m` with `coordinate >= 0`. Vertices keep their numbers (cut vertices are appended), so hard edges given
/// as vertex pairs stay valid. Vertices within `tol` of the plane are put exactly on it.
pub fn clip_half(m: &WorkMesh, axis: usize, tol: f32) -> WorkMesh {
    let mut out = WorkMesh { p: m.p.clone(), t: Vec::new(), group: Vec::new(), color: m.color.clone() };
    let d = |p: &[V3], v: u32| {
        let x = coord(p[v as usize], axis);
        if x.abs() <= tol {
            0.0
        } else {
            x
        }
    };
    let mut cut: HashMap<u64, u32> = HashMap::new();
    for (ti, tri) in m.t.iter().enumerate() {
        let ds = [d(&m.p, tri[0]), d(&m.p, tri[1]), d(&m.p, tri[2])];
        if ds.iter().all(|&x| x <= 0.0) {
            continue; // on the negative side, or lying in the plane
        }
        let g = m.group.get(ti).copied().unwrap_or(0);
        if ds.iter().all(|&x| x >= 0.0) {
            for k in 0..3 {
                if ds[k] == 0.0 {
                    out.p[tri[k] as usize] = with_coord(out.p[tri[k] as usize], axis, 0.0);
                }
            }
            out.t.push(*tri);
            out.group.push(g);
            continue;
        }
        let mut poly: Vec<u32> = Vec::with_capacity(4);
        for i in 0..3 {
            let (a, b) = (tri[i], tri[(i + 1) % 3]);
            let (da, db) = (ds[i], ds[(i + 1) % 3]);
            if da >= 0.0 {
                poly.push(a);
                if da == 0.0 {
                    out.p[a as usize] = with_coord(out.p[a as usize], axis, 0.0);
                }
            }
            if (da > 0.0 && db < 0.0) || (da < 0.0 && db > 0.0) {
                let id = *cut.entry(edge_key(a, b)).or_insert_with(|| {
                    let u = da / (da - db);
                    let pos = with_coord(m.p[a as usize] + (m.p[b as usize] - m.p[a as usize]) * u, axis, 0.0);
                    out.p.push(pos);
                    if !m.color.is_empty() {
                        out.color.push(m.color[a as usize] + (m.color[b as usize] - m.color[a as usize]) * u);
                    }
                    (out.p.len() - 1) as u32
                });
                poly.push(id);
            }
        }
        for k in 1..poly.len().saturating_sub(1) {
            out.t.push([poly[0], poly[k], poly[k + 1]]);
            out.group.push(g);
        }
    }
    out
}

/// Adds the reflection of `mesh` through the plane `coordinate = 0`: vertices within `eps` of the plane are put on it
/// and shared, the reflected faces are wound the other way round. `materials` (one per face) is extended the same way.
pub fn mirror_weld(mesh: &mut Mesh, materials: &mut Vec<u32>, axis: usize, eps: f32) {
    let nv = mesh.positions.len();
    let mut map: Vec<u32> = Vec::with_capacity(nv);
    for v in 0..nv {
        let p = V3::from_array(mesh.positions[v]);
        if coord(p, axis).abs() <= eps {
            mesh.positions[v] = with_coord(p, axis, 0.0).to_array();
            map.push(v as u32);
        } else {
            mesh.positions.push(reflect(p, axis).to_array());
            map.push((mesh.positions.len() - 1) as u32);
        }
    }
    let (nf, mut at) = (mesh.face_sizes.len(), 0usize);
    for f in 0..nf {
        let n = mesh.face_sizes[f] as usize;
        let face: Vec<u32> = mesh.indices[at..at + n].to_vec();
        at += n;
        mesh.face_sizes.push(n as u32);
        mesh.indices.extend(face.iter().rev().map(|&v| map[v as usize]));
        if !materials.is_empty() {
            materials.push(materials[f]);
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::testmesh::{icosphere, uv_sphere};
    use crate::topology::analyze;

    #[test]
    fn clipping_a_sphere_keeps_a_closed_half_with_a_border_on_the_plane() {
        let s = uv_sphere(24, 32);
        let h = clip_half(&s, 0, 1e-6);
        assert!(!h.t.is_empty() && h.t.len() < s.t.len());
        let used: std::collections::HashSet<u32> = h.t.iter().flatten().copied().collect();
        assert!(used.iter().all(|&v| coord(h.p[v as usize], 0) >= -1e-6), "a vertex is on the negative side");
        let m = h.to_mesh();
        let st = analyze(&m);
        assert!(st.boundary_edges > 0 && st.non_manifold_edges == 0, "{st:?}");
        // area is half of the sphere's
        let (a, b) = (h.area(), s.area());
        assert!((a / b - 0.5).abs() < 0.01, "{a} of {b}");
    }

    #[test]
    fn mirroring_a_clipped_sphere_closes_it_again() {
        let s = icosphere(3);
        let h = clip_half(&s, 1, 1e-6);
        let mut mesh = h.to_mesh();
        // drop the unused vertices the clip keeps, as the remesher's output never has them
        let mut keep = vec![u32::MAX; mesh.positions.len()];
        let mut pos = Vec::new();
        for i in mesh.indices.iter_mut() {
            if keep[*i as usize] == u32::MAX {
                keep[*i as usize] = pos.len() as u32;
                pos.push(mesh.positions[*i as usize]);
            }
            *i = keep[*i as usize];
        }
        mesh.positions = pos;
        let mut mats = vec![];
        mirror_weld(&mut mesh, &mut mats, 1, 1e-5);
        let st = analyze(&mesh);
        assert!(st.is_closed_manifold() && st.euler == 2, "{st:?}");
    }

    fn remeshed(mesh: &Mesh, settings: &str) -> Mesh {
        let st = crate::settings::Settings::parse(&format!("FileIn=a\nFileOut=b\n{settings}")).unwrap();
        crate::remesh_mesh(mesh, &st, &|_, _| true).unwrap().unwrap().mesh
    }

    /// Every vertex has a mirror image among the vertices.
    fn is_symmetric(m: &Mesh, axis: usize, tol: f32) -> bool {
        let mut grid: HashMap<(i64, i64, i64), Vec<V3>> = HashMap::new();
        let key = |p: V3| ((p.x / tol).round() as i64, (p.y / tol).round() as i64, (p.z / tol).round() as i64);
        for p in &m.positions {
            let p = V3::from_array(*p);
            grid.entry(key(p)).or_default().push(p);
        }
        m.positions.iter().all(|&p| {
            let q = reflect(V3::from_array(p), axis);
            let k = key(q);
            (-1..=1).any(|dx| (-1..=1).any(|dy| (-1..=1).any(|dz| grid.get(&(k.0 + dx, k.1 + dy, k.2 + dz)).is_some_and(|v| v.iter().any(|c| (*c - q).length() < 2.0 * tol)))))
        })
    }

    #[test]
    fn a_symmetric_remesh_of_a_sphere_is_closed_all_quads_and_mirrored() {
        let s = uv_sphere(48, 64).to_mesh();
        let out = remeshed(&s, "TargetQuadCount=800\nSymAxis=X\nCurvatureAdaptivness=0\n");
        let st = analyze(&out);
        assert!(st.is_closed_manifold() && st.euler == 2 && st.components == 1, "{st:?}");
        assert!(out.faces().all(|f| f.len() == 4), "not all quads");
        assert!((out.face_count() as f64 / 800.0 - 1.0).abs() < 0.2, "{} faces for 800", out.face_count());
        assert!(is_symmetric(&out, 0, 1e-4), "not mirrored");
    }

    #[test]
    fn two_axes_give_a_quarter_remesh_mirrored_twice() {
        let s = uv_sphere(48, 64).to_mesh();
        let out = remeshed(&s, "TargetQuadCount=800\nSymAxis=XY\nCurvatureAdaptivness=0\n");
        let st = analyze(&out);
        assert!(st.is_closed_manifold() && st.euler == 2, "{st:?}");
        assert!(is_symmetric(&out, 0, 1e-4) && is_symmetric(&out, 1, 1e-4), "not mirrored");
        assert!((out.face_count() as f64 / 800.0 - 1.0).abs() < 0.25, "{} faces for 800", out.face_count());
    }
}
