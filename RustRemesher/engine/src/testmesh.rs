// SPDX-License-Identifier: GPL-3.0-or-later
//! Meshes for tests (compiled only for `cargo test`).

use crate::vec3::{v3, V3};
use crate::workmesh::WorkMesh;
use std::collections::HashMap;

/// Unit icosphere with outward counter-clockwise triangles; `subdiv` = 0 is the icosahedron (20 triangles).
pub fn icosphere(subdiv: u32) -> WorkMesh {
    let t = (1.0 + 5f32.sqrt()) / 2.0;
    let mut p: Vec<V3> = [
        (-1., t, 0.), (1., t, 0.), (-1., -t, 0.), (1., -t, 0.),
        (0., -1., t), (0., 1., t), (0., -1., -t), (0., 1., -t),
        (t, 0., -1.), (t, 0., 1.), (-t, 0., -1.), (-t, 0., 1.),
    ]
    .iter()
    .map(|&(x, y, z)| v3(x, y, z).normalized())
    .collect();
    let mut tris: Vec<[u32; 3]> = vec![
        [0, 11, 5], [0, 5, 1], [0, 1, 7], [0, 7, 10], [0, 10, 11],
        [1, 5, 9], [5, 11, 4], [11, 10, 2], [10, 7, 6], [7, 1, 8],
        [3, 9, 4], [3, 4, 2], [3, 2, 6], [3, 6, 8], [3, 8, 9],
        [4, 9, 5], [2, 4, 11], [6, 2, 10], [8, 6, 7], [9, 8, 1],
    ];
    for _ in 0..subdiv {
        let mut mid: HashMap<(u32, u32), u32> = HashMap::new();
        let mut next = Vec::with_capacity(tris.len() * 4);
        for tr in &tris {
            let mut m = [0u32; 3];
            for k in 0..3 {
                let (a, b) = (tr[k], tr[(k + 1) % 3]);
                let key = (a.min(b), a.max(b));
                m[k] = *mid.entry(key).or_insert_with(|| {
                    p.push(((p[a as usize] + p[b as usize]) * 0.5).normalized());
                    p.len() as u32 - 1
                });
            }
            next.push([tr[0], m[0], m[2]]);
            next.push([tr[1], m[1], m[0]]);
            next.push([tr[2], m[2], m[1]]);
            next.push([m[0], m[1], m[2]]);
        }
        tris = next;
    }
    let n = tris.len();
    WorkMesh { p, t: tris, group: vec![0; n], color: Vec::new() }
}

/// UV sphere with one pole vertex at each end (triangle fans of valence `m`) and quads in between. Outward.
pub fn uv_sphere(n: usize, m: usize) -> WorkMesh {
    let mut p = vec![v3(0.0, 0.0, 1.0)];
    for i in 1..n {
        let th = std::f32::consts::PI * i as f32 / n as f32;
        for j in 0..m {
            let ph = 2.0 * std::f32::consts::PI * j as f32 / m as f32;
            p.push(v3(th.sin() * ph.cos(), th.sin() * ph.sin(), th.cos()));
        }
    }
    p.push(v3(0.0, 0.0, -1.0));
    let south = p.len() as u32 - 1;
    let id = |i: usize, j: usize| (1 + (i - 1) * m + j % m) as u32;
    let mut t = Vec::new();
    for j in 0..m {
        t.push([0, id(1, j), id(1, j + 1)]);
    }
    for i in 1..n - 1 {
        for j in 0..m {
            let (a, b, c, d) = (id(i, j), id(i, j + 1), id(i + 1, j + 1), id(i + 1, j));
            t.push([a, c, b]);
            t.push([a, d, c]);
        }
    }
    for j in 0..m {
        t.push([south, id(n - 1, j + 1), id(n - 1, j)]);
    }
    let g = vec![0; t.len()];
    WorkMesh { p, t, group: g, color: Vec::new() }
}

/// Square [0,1]^2 on z = 0, `n` x `n` cells, counter-clockwise seen from +z. Open (4 border sides).
pub fn grid_plane(n: usize) -> WorkMesh {
    let mut p = Vec::new();
    for j in 0..=n {
        for i in 0..=n {
            p.push(v3(i as f32 / n as f32, j as f32 / n as f32, 0.0));
        }
    }
    let id = |i: usize, j: usize| (j * (n + 1) + i) as u32;
    let mut t = Vec::new();
    for j in 0..n {
        for i in 0..n {
            t.push([id(i, j), id(i + 1, j), id(i + 1, j + 1)]);
            t.push([id(i, j), id(i + 1, j + 1), id(i, j + 1)]);
        }
    }
    let g = vec![0; t.len()];
    WorkMesh { p, t, group: g, color: Vec::new() }
}

/// Cube [-1,1]^3, `n` x `n` cells per side, outward triangles, welded along the edges.
pub fn grid_cube(n: usize) -> WorkMesh {
    // (origin, u axis, v axis) with u x v pointing outward
    let faces: [(V3, V3, V3); 6] = [
        (v3(1., -1., -1.), v3(0., 1., 0.), v3(0., 0., 1.)),
        (v3(-1., -1., -1.), v3(0., 0., 1.), v3(0., 1., 0.)),
        (v3(-1., 1., -1.), v3(0., 0., 1.), v3(1., 0., 0.)),
        (v3(-1., -1., -1.), v3(1., 0., 0.), v3(0., 0., 1.)),
        (v3(-1., -1., 1.), v3(1., 0., 0.), v3(0., 1., 0.)),
        (v3(-1., -1., -1.), v3(0., 1., 0.), v3(1., 0., 0.)),
    ];
    let mut p: Vec<V3> = Vec::new();
    let mut weld: HashMap<(i32, i32, i32), u32> = HashMap::new();
    let mut t = Vec::new();
    let step = 2.0 / n as f32;
    for (o, u, v) in faces {
        let mut vid = |i: usize, j: usize| -> u32 {
            let q = o + u * (step * i as f32) + v * (step * j as f32);
            let key = ((q.x * 1000.0).round() as i32, (q.y * 1000.0).round() as i32, (q.z * 1000.0).round() as i32);
            *weld.entry(key).or_insert_with(|| {
                p.push(q);
                p.len() as u32 - 1
            })
        };
        for j in 0..n {
            for i in 0..n {
                let (a, b, c, d) = (vid(i, j), vid(i + 1, j), vid(i + 1, j + 1), vid(i, j + 1));
                t.push([a, b, c]);
                t.push([a, c, d]);
            }
        }
    }
    let g = vec![0; t.len()];
    WorkMesh { p, t, group: g, color: Vec::new() }
}

/// Torus around the z axis (major radius `big`, minor radius `small`), outward triangles.
pub fn torus(big: f32, small: f32, n: usize, m: usize) -> WorkMesh {
    let mut p = Vec::new();
    for i in 0..n {
        let th = 2.0 * std::f32::consts::PI * i as f32 / n as f32;
        for j in 0..m {
            let ph = 2.0 * std::f32::consts::PI * j as f32 / m as f32;
            let r = big + small * ph.cos();
            p.push(v3(r * th.cos(), r * th.sin(), small * ph.sin()));
        }
    }
    let id = |i: usize, j: usize| ((i % n) * m + j % m) as u32;
    let mut t = Vec::new();
    for i in 0..n {
        for j in 0..m {
            let (a, b, c, d) = (id(i, j), id(i + 1, j), id(i + 1, j + 1), id(i, j + 1));
            t.push([a, b, c]);
            t.push([a, c, d]);
        }
    }
    let g = vec![0; t.len()];
    WorkMesh { p, t, group: g, color: Vec::new() }
}

/// Open tube around the z axis: radius 1, z in [-1, 1], `n` segments around, `h` along. Outward triangles.
pub fn tube(n: usize, h: usize) -> WorkMesh {
    let mut p = Vec::new();
    for j in 0..=h {
        let z = -1.0 + 2.0 * j as f32 / h as f32;
        for i in 0..n {
            let th = 2.0 * std::f32::consts::PI * i as f32 / n as f32;
            p.push(v3(th.cos(), th.sin(), z));
        }
    }
    let id = |i: usize, j: usize| (j * n + i % n) as u32;
    let mut t = Vec::new();
    for j in 0..h {
        for i in 0..n {
            let (a, b, c, d) = (id(i, j), id(i + 1, j), id(i + 1, j + 1), id(i, j + 1));
            t.push([a, b, c]);
            t.push([a, c, d]);
        }
    }
    let g = vec![0; t.len()];
    WorkMesh { p, t, group: g, color: Vec::new() }
}

/// Signed volume (positive for outward-facing closed surfaces).
pub fn volume(m: &WorkMesh) -> f64 {
    m.t.iter()
        .map(|t| {
            let (a, b, c) = (m.p[t[0] as usize], m.p[t[1] as usize], m.p[t[2] as usize]);
            a.dot(b.cross(c)) as f64 / 6.0
        })
        .sum()
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::topology::analyze;

    #[test]
    fn icosphere_is_a_closed_outward_sphere() {
        for s in 0..4 {
            let m = icosphere(s);
            assert_eq!(m.t.len(), 20 * 4usize.pow(s));
            let st = analyze(&m.to_mesh());
            assert!(st.is_closed_manifold() && st.euler == 2 && st.components == 1, "subdiv {s}: {st:?}");
            assert!(volume(&m) > 0.0);
        }
    }

    #[test]
    fn cube_is_closed_and_outward() {
        let m = grid_cube(5);
        let st = analyze(&m.to_mesh());
        assert!(st.is_closed_manifold() && st.euler == 2, "{st:?}");
        assert!((volume(&m) - 8.0).abs() < 1e-3);
        assert!((m.area() - 24.0).abs() < 1e-3);
    }

    #[test]
    fn torus_is_a_closed_outward_surface_of_genus_one() {
        let m = torus(1.0, 0.4, 40, 20);
        let st = analyze(&m.to_mesh());
        assert!(st.is_closed_manifold() && st.euler == 0 && st.components == 1, "{st:?}");
        assert!(volume(&m) > 0.0);
    }

    #[test]
    fn tube_is_open_with_two_borders() {
        let m = tube(24, 10);
        let st = analyze(&m.to_mesh());
        assert_eq!(st.boundary_edges, 48);
        assert_eq!(st.inconsistent_edges, 0);
        assert!((m.area() - 4.0 * std::f64::consts::PI).abs() < 0.2);
    }

    #[test]
    fn plane_is_open() {
        let st = analyze(&grid_plane(3).to_mesh());
        assert_eq!(st.boundary_edges, 12);
        assert_eq!(st.inconsistent_edges, 0);
    }
}
