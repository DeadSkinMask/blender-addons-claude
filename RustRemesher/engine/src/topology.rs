// SPDX-License-Identifier: GPL-3.0-or-later
//! Edge/face statistics of a polygon mesh (read-only).

use crate::mesh::Mesh;
use std::collections::HashMap;

#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub struct Stats {
    pub vertices: usize,
    pub faces: usize,
    pub triangles: usize,
    pub quads: usize,
    pub ngons: usize,
    pub edges: usize,
    pub boundary_edges: usize,
    pub non_manifold_edges: usize,
    /// Edges whose two faces traverse them in the same direction (flipped neighbour).
    pub inconsistent_edges: usize,
    pub components: usize,
    pub euler: i64,
}

impl Stats {
    pub fn is_closed_manifold(&self) -> bool {
        self.boundary_edges == 0 && self.non_manifold_edges == 0 && self.inconsistent_edges == 0
    }
}

fn find(parent: &mut [u32], mut x: u32) -> u32 {
    while parent[x as usize] != x {
        parent[x as usize] = parent[parent[x as usize] as usize];
        x = parent[x as usize];
    }
    x
}

/// Expects a validated mesh.
pub fn analyze(mesh: &Mesh) -> Stats {
    let nv = mesh.positions.len();
    let mut s = Stats { vertices: nv, faces: mesh.face_count(), ..Default::default() };
    // per undirected edge: (uses, forward uses, backward uses)
    let mut edges: HashMap<u64, (u32, u32, u32)> = HashMap::with_capacity(mesh.indices.len());
    let mut parent: Vec<u32> = (0..nv as u32).collect();
    let mut used = vec![false; nv];

    for f in mesh.faces() {
        match f.len() {
            3 => s.triangles += 1,
            4 => s.quads += 1,
            _ => s.ngons += 1,
        }
        for k in 0..f.len() {
            let (a, b) = (f[k], f[(k + 1) % f.len()]);
            used[a as usize] = true;
            let (lo, hi, fwd) = if a < b { (a, b, true) } else { (b, a, false) };
            let e = edges.entry(((lo as u64) << 32) | hi as u64).or_insert((0, 0, 0));
            e.0 += 1;
            if fwd {
                e.1 += 1;
            } else {
                e.2 += 1;
            }
            let (ra, rb) = (find(&mut parent, a), find(&mut parent, b));
            if ra != rb {
                parent[ra as usize] = rb;
            }
        }
    }

    s.edges = edges.len();
    for &(n, fwd, bwd) in edges.values() {
        if n == 1 {
            s.boundary_edges += 1;
        } else if n > 2 {
            s.non_manifold_edges += 1;
        } else if fwd != 1 || bwd != 1 {
            s.inconsistent_edges += 1;
        }
    }
    let used_count = used.iter().filter(|&&u| u).count();
    let mut roots = std::collections::HashSet::new();
    for v in 0..nv as u32 {
        if used[v as usize] {
            roots.insert(find(&mut parent, v));
        }
    }
    s.components = roots.len();
    s.euler = used_count as i64 - s.edges as i64 + s.faces as i64;
    s
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::mesh::test_cube;

    #[test]
    fn cube_is_a_closed_sphere() {
        let s = analyze(&test_cube());
        assert_eq!((s.vertices, s.edges, s.faces, s.quads), (8, 12, 6, 6));
        assert_eq!(s.euler, 2);
        assert_eq!(s.components, 1);
        assert!(s.is_closed_manifold());
    }

    #[test]
    fn open_mesh_has_boundary() {
        let mut m = test_cube();
        m.face_sizes.pop();
        m.indices.truncate(20);
        let s = analyze(&m);
        assert_eq!(s.boundary_edges, 4);
        assert!(!s.is_closed_manifold());
    }

    #[test]
    fn flipped_face_is_inconsistent() {
        let mut m = test_cube();
        m.indices[0..4].reverse();
        assert_eq!(analyze(&m).inconsistent_edges, 4);
    }

    #[test]
    fn counts_components() {
        let mut m = test_cube();
        let n = m.positions.len() as u32;
        let extra: Vec<_> = m.positions.iter().map(|p| [p[0] + 10., p[1], p[2]]).collect();
        let idx: Vec<u32> = m.indices.iter().map(|i| i + n).collect();
        m.positions.extend(extra);
        m.indices.extend(idx);
        m.face_sizes.extend(vec![4; 6]);
        let s = analyze(&m);
        assert_eq!(s.components, 2);
        assert_eq!(s.euler, 4);
    }
}
