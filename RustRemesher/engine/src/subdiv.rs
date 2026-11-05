// SPDX-License-Identifier: GPL-3.0-or-later
//! Smooth upsampling of coarse inputs. A low-poly source (a few hundred triangles asked for thousands of quads) is
//! made of flat facets whose edges look like creases to the curvature and the cross field, so the result is noisy.
//! Every edge longer than the wanted length is split (so the sliver triangles of a cone's apex get fat as well, which
//! splitting every triangle in four would not do); the new vertex on a smooth edge is the midpoint of the cubic curve that
//! leaves both ends along their tangent planes (the PN-triangle edge), which rounds the facets without shrinking the
//! shape. Edges that are creases (open, marked sharp, or with faces meeting at more than the crease angle) get the
//! plain midpoint, so hard edges and borders stay straight. Vertex numbers are kept (new ones are appended).

use crate::vec3::V3;
use crate::workmesh::{edge_key, mesh_edges, WorkMesh};
use std::collections::HashMap;

pub struct Upsampled {
    pub mesh: WorkMesh,
    /// The user's hard edges, split along with the mesh.
    pub sharp: Vec<[u32; 2]>,
    pub rounds: usize,
}

/// Splits edges longer than `max_edge` (round after round, at most `max_rounds`, never past `max_tris` triangles)
/// until none is. `None` when the mesh is fine as it is.
pub fn upsample(m: &WorkMesh, user_sharp: &[[u32; 2]], max_edge: f32, crease_cos: f32, max_rounds: usize, max_tris: usize) -> Option<Upsampled> {
    if max_edge <= 0.0 || m.t.is_empty() {
        return None;
    }
    let (mut mesh, mut sharp, mut rounds) = (m.clone(), user_sharp.to_vec(), 0);
    while rounds < max_rounds {
        let Some((next, next_sharp)) = subdivide_once(&mesh, &sharp, max_edge, crease_cos, max_tris) else { break };
        (mesh, sharp) = (next, next_sharp);
        rounds += 1;
    }
    (rounds > 0).then_some(Upsampled { mesh, sharp, rounds })
}

fn subdivide_once(m: &WorkMesh, user_sharp: &[[u32; 2]], max_edge: f32, crease_cos: f32, max_tris: usize) -> Option<(WorkMesh, Vec<[u32; 2]>)> {
    let nt = m.t.len();
    let edges = mesh_edges(&m.t);
    let split: Vec<bool> = edges.iter().map(|e| (m.p[e.a as usize] - m.p[e.b as usize]).length() > max_edge).collect();
    if !split.iter().any(|&x| x) {
        return None;
    }
    let mut total = 0usize;
    {
        let index: HashMap<u64, usize> = edges.iter().enumerate().map(|(i, e)| (edge_key(e.a, e.b), i)).collect();
        for t in &m.t {
            let k = (0..3).filter(|&k| split[index[&edge_key(t[k], t[(k + 1) % 3])]]).count();
            total += [1, 2, 3, 4][k];
        }
    }
    if total > max_tris {
        return None;
    }
    let user: std::collections::HashSet<u64> = user_sharp.iter().map(|e| edge_key(e[0], e[1])).collect();
    let fnormal: Vec<V3> = m
        .t
        .iter()
        .map(|t| (m.p[t[1] as usize] - m.p[t[0] as usize]).cross(m.p[t[2] as usize] - m.p[t[0] as usize]))
        .collect(); // length = twice the area
    let unit = |v: V3| v.safe_normalize(V3::ZERO);
    let mut crease = vec![false; edges.len()];
    for (i, e) in edges.iter().enumerate() {
        crease[i] = e.count != 2
            || user.contains(&edge_key(e.a, e.b))
            || unit(fnormal[e.t0 as usize]).dot(unit(fnormal[e.t1 as usize])) < crease_cos;
    }
    // Corner normals: the corners of a vertex joined across smooth edges share one normal.
    let corner_of = |t: u32, v: u32| -> usize { 3 * t as usize + (0..3).find(|&k| m.t[t as usize][k] == v).unwrap_or(0) };
    let mut parent: Vec<u32> = (0..3 * nt as u32).collect();
    fn find(p: &mut [u32], mut x: u32) -> u32 {
        while p[x as usize] != x {
            p[x as usize] = p[p[x as usize] as usize];
            x = p[x as usize];
        }
        x
    }
    for (i, e) in edges.iter().enumerate() {
        if crease[i] {
            continue;
        }
        for v in [e.a, e.b] {
            let (x, y) = (find(&mut parent, corner_of(e.t0, v) as u32), find(&mut parent, corner_of(e.t1, v) as u32));
            if x != y {
                parent[x.max(y) as usize] = x.min(y);
            }
        }
    }
    let mut acc = vec![V3::ZERO; 3 * nt];
    for t in 0..nt {
        for k in 0..3 {
            let r = find(&mut parent, (3 * t + k) as u32) as usize;
            acc[r] += fnormal[t];
        }
    }
    let corner_normal = |parent: &mut Vec<u32>, t: u32, v: u32| -> V3 {
        let r = find(parent, corner_of(t, v) as u32) as usize;
        unit(acc[r])
    };

    let mut out = WorkMesh { p: m.p.clone(), t: Vec::with_capacity(4 * nt), group: Vec::with_capacity(4 * nt), color: m.color.clone() };
    let mut mid: HashMap<u64, u32> = HashMap::with_capacity(edges.len());
    for (i, e) in edges.iter().enumerate() {
        if !split[i] {
            continue;
        }
        let (pa, pb) = (m.p[e.a as usize], m.p[e.b as usize]);
        let mut pos = (pa + pb) * 0.5;
        if !crease[i] {
            let (na, nb) = (corner_normal(&mut parent, e.t0, e.a), corner_normal(&mut parent, e.t0, e.b));
            if na.length2() > 0.0 && nb.length2() > 0.0 {
                let d = pb - pa;
                // midpoint of the cubic with end tangents projected into the tangent planes
                pos -= (na * na.dot(d) - nb * nb.dot(d)) * (1.0 / 8.0);
            }
        }
        out.p.push(pos);
        if !m.color.is_empty() {
            out.color.push((m.color[e.a as usize] + m.color[e.b as usize]) * 0.5);
        }
        mid.insert(edge_key(e.a, e.b), (out.p.len() - 1) as u32);
    }
    for (ti, t) in m.t.iter().enumerate() {
        let g = m.group.get(ti).copied().unwrap_or(0);
        let mut push = |tri: [u32; 3]| {
            out.t.push(tri);
            out.group.push(g);
        };
        // m[k]: the new vertex on the edge t[k] -> t[k + 1], if it is split
        let m3: [Option<u32>; 3] = std::array::from_fn(|k| mid.get(&edge_key(t[k], t[(k + 1) % 3])).copied());
        match m3.iter().filter(|x| x.is_some()).count() {
            0 => push(*t),
            1 => {
                let k = m3.iter().position(|x| x.is_some()).unwrap();
                let (a, b, c, x) = (t[k], t[(k + 1) % 3], t[(k + 2) % 3], m3[k].unwrap());
                push([a, x, c]);
                push([x, b, c]);
            }
            2 => {
                // the unsplit edge is k; the corner opposite it is t[(k + 2) % 3]
                let k = m3.iter().position(|x| x.is_none()).unwrap();
                let (a, b, c) = (t[k], t[(k + 1) % 3], t[(k + 2) % 3]);
                let (x, y) = (m3[(k + 1) % 3].unwrap(), m3[(k + 2) % 3].unwrap()); // on b-c and c-a
                push([x, c, y]);
                // the quad a, b, x, y along its shorter diagonal
                if (out.p[a as usize] - out.p[x as usize]).length2() <= (out.p[b as usize] - out.p[y as usize]).length2() {
                    push([a, b, x]);
                    push([a, x, y]);
                } else {
                    push([a, b, y]);
                    push([b, x, y]);
                }
            }
            _ => {
                let (ab, bc, ca) = (m3[0].unwrap(), m3[1].unwrap(), m3[2].unwrap());
                for tri in [[t[0], ab, ca], [ab, t[1], bc], [ca, bc, t[2]], [ab, bc, ca]] {
                    push(tri);
                }
            }
        }
    }
    let sharp = user_sharp
        .iter()
        .flat_map(|e| {
            let k = mid.get(&edge_key(e[0], e[1])).copied();
            k.into_iter().flat_map(move |x| [[e[0], x], [x, e[1]]])
        })
        .collect();
    Some((out, sharp))
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::testmesh::{grid_cube, icosphere};
    use crate::topology::analyze;

    fn longest_edge(m: &WorkMesh) -> f32 {
        mesh_edges(&m.t).iter().map(|e| (m.p[e.a as usize] - m.p[e.b as usize]).length()).fold(0f32, f32::max)
    }

    #[test]
    fn a_coarse_sphere_gets_rounder_and_stays_closed() {
        let s = icosphere(1); // 80 triangles
        let u = upsample(&s, &[], 0.2, 0.7, 8, 1_000_000).expect("coarse");
        assert!(u.rounds >= 2 && longest_edge(&u.mesh) <= 0.2 + 1e-4, "{} rounds, longest {}", u.rounds, longest_edge(&u.mesh));
        let st = analyze(&u.mesh.to_mesh());
        assert!(st.is_closed_manifold() && st.euler == 2, "{st:?}");
        // the new vertices sit nearer the true sphere than the plain midpoints would
        let new_only = u.mesh.p[s.p.len()..].iter().map(|p| (p.length() - 1.0).abs()).fold(0f32, f32::max);
        let plain = (1.0 - (s.p[s.t[0][0] as usize] + s.p[s.t[0][1] as usize]).length() * 0.5).abs();
        assert!(new_only < plain * 0.6, "{new_only} vs plain {plain}");
        let all = u.mesh.p.iter().map(|p| (p.length() - 1.0).abs()).fold(0f32, f32::max);
        assert!(all < 0.02, "{all}");
    }

    #[test]
    fn slivers_get_fat_not_just_smaller() {
        // a fan of 32 needles: apex at the origin, rim at distance 2
        let mut p = vec![crate::vec3::v3(0.0, 0.0, 0.0)];
        for i in 0..32 {
            let a = i as f32 / 32.0 * std::f32::consts::TAU;
            p.push(crate::vec3::v3(2.0 * a.cos(), 2.0 * a.sin(), 0.0));
        }
        let t: Vec<[u32; 3]> = (0..32).map(|i| [0, 1 + i as u32, 1 + ((i + 1) % 32) as u32]).collect();
        let m = WorkMesh { p, group: vec![0; t.len()], t, color: vec![] };
        let u = upsample(&m, &[], 0.25, 0.7, 12, 1_000_000).expect("long edges");
        assert!(longest_edge(&u.mesh) <= 0.25 + 1e-4, "{}", longest_edge(&u.mesh));
        // far fewer triangles than four-way splitting down to a 0.25 edge would give (~32 * 4^4)
        assert!(u.mesh.t.len() < 32 * 256, "{} triangles", u.mesh.t.len());
        let st = analyze(&u.mesh.to_mesh());
        assert!(st.non_manifold_edges == 0 && st.inconsistent_edges == 0, "{st:?}");
    }

    #[test]
    fn a_cube_keeps_its_hard_edges_and_flat_faces() {
        let c = grid_cube(1);
        let u = upsample(&c, &[], 0.3, 0.7, 8, 1_000_000).expect("coarse");
        // every vertex still lies on the cube's surface (max coordinate 1) with no rounding of the edges
        for p in &u.mesh.p {
            let m = p.x.abs().max(p.y.abs()).max(p.z.abs());
            assert!((m - 1.0).abs() < 1e-5 && m <= 1.0 + 1e-5, "{p:?}");
        }
        let st = analyze(&u.mesh.to_mesh());
        assert!(st.is_closed_manifold() && st.euler == 2, "{st:?}");
    }

    #[test]
    fn fine_meshes_are_left_alone_and_user_edges_are_split() {
        let s = icosphere(3);
        assert!(upsample(&s, &[], 10.0, 0.7, 8, 1_000_000).is_none());
        let sharp = vec![[s.t[0][0], s.t[0][1]]];
        let u = upsample(&s, &sharp, 0.05, 0.7, 1, 1_000_000).expect("coarse");
        assert_eq!(u.rounds, 1);
        assert!(u.sharp.len() >= 2);
        assert!(u.sharp.iter().all(|e| u.mesh.t.iter().any(|t| t.contains(&e[0]) && t.contains(&e[1]))));
        // the triangle budget stops it
        assert!(upsample(&s, &[], 0.001, 0.7, 8, s.t.len()).is_none());
    }
}
