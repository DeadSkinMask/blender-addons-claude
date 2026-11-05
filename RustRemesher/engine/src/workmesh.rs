// SPDX-License-Identifier: GPL-3.0-or-later
//! The triangle mesh the remesher works on, and the feature lines (open borders, material/group borders, hard
//! edges) detected on it. Follows SculptForge's `WorkMesh` / feature detection (GPL-3.0-or-later).

use crate::mesh::{Attributes, Mesh};
use crate::vec3::V3;
use std::collections::{HashMap, HashSet};

pub fn edge_key(a: u32, b: u32) -> u64 {
    if a < b {
        ((a as u64) << 32) | b as u64
    } else {
        ((b as u64) << 32) | a as u64
    }
}

pub fn key_ends(k: u64) -> (u32, u32) {
    ((k >> 32) as u32, (k & 0xffff_ffff) as u32)
}

#[derive(Clone, Debug, Default)]
pub struct WorkMesh {
    pub p: Vec<V3>,
    pub t: Vec<[u32; 3]>,
    /// Per triangle (material id, or 0).
    pub group: Vec<u32>,
    /// Per vertex RGB; empty when vertex paint is not used.
    pub color: Vec<V3>,
}

impl WorkMesh {
    /// Triangulates the polygons (quads along their shorter diagonal, larger n-gons as a fan). Vertex numbers are
    /// the mesh's, so hard-edge vertex pairs stay valid. Expects a validated mesh.
    pub fn from_mesh(mesh: &Mesh, use_materials: bool, use_colors: bool) -> WorkMesh {
        let mut p: Vec<V3> = mesh.positions.iter().map(|&a| V3::from_array(a)).collect();
        let mut extra_colors: Vec<V3> = Vec::new(); // colors of the centre vertices added to big polygons
        let mut t = Vec::with_capacity(mesh.indices.len());
        let mut group = Vec::with_capacity(mesh.indices.len());
        let mats = if use_materials { mesh.attrs.materials.as_ref() } else { None };
        for (fi, f) in mesh.faces().enumerate() {
            let g = mats.map_or(0, |m| m[fi]);
            match f.len() {
                3 => {
                    t.push([f[0], f[1], f[2]]);
                    group.push(g);
                }
                4 => {
                    let d02 = (p[f[0] as usize] - p[f[2] as usize]).length2();
                    let d13 = (p[f[1] as usize] - p[f[3] as usize]).length2();
                    let (a, b) = if d02 <= d13 {
                        ([f[0], f[1], f[2]], [f[0], f[2], f[3]])
                    } else {
                        ([f[1], f[2], f[3]], [f[1], f[3], f[0]])
                    };
                    t.push(a);
                    t.push(b);
                    group.push(g);
                    group.push(g);
                }
                n => {
                    // A star from the centre keeps the triangles fat (a fan from one corner of a 64-gon gives slivers),
                    // when the polygon is convex enough for that; otherwise the fan.
                    let centre = f.iter().fold(V3::ZERO, |a, &v| a + p[v as usize]) / n as f32;
                    let normal = (1..n - 1).fold(V3::ZERO, |a, k| a + (p[f[k] as usize] - p[f[0] as usize]).cross(p[f[k + 1] as usize] - p[f[0] as usize]));
                    let star = n >= 5 && (0..n).all(|k| (p[f[k] as usize] - centre).cross(p[f[(k + 1) % n] as usize] - centre).dot(normal) > 0.0);
                    if star {
                        let c = p.len() as u32;
                        p.push(centre);
                        extra_colors.push(f.iter().fold(V3::ZERO, |a, &v| a + mesh.attrs.colors.as_ref().map_or(V3::ZERO, |c| V3::from_array(c[v as usize]))) / n as f32);
                        for k in 0..n {
                            t.push([f[k], f[(k + 1) % n], c]);
                            group.push(g);
                        }
                    } else {
                        for k in 1..n - 1 {
                            t.push([f[0], f[k], f[k + 1]]);
                            group.push(g);
                        }
                    }
                }
            }
        }
        let color = if use_colors {
            mesh.attrs.colors.as_ref().map_or_else(Vec::new, |c| c.iter().map(|&a| V3::from_array(a)).chain(extra_colors.iter().copied()).collect())
        } else {
            Vec::new()
        };
        WorkMesh { p, t, group, color }
    }

    pub fn area(&self) -> f64 {
        self.t
            .iter()
            .map(|t| {
                let (a, b, c) = (self.p[t[0] as usize], self.p[t[1] as usize], self.p[t[2] as usize]);
                0.5 * (b - a).cross(c - a).length() as f64
            })
            .sum()
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

/// An undirected edge of a triangle mesh: its ends (a < b), how many triangles use it, and the first two.
#[derive(Clone, Copy, Debug)]
pub struct SrcEdge {
    pub a: u32,
    pub b: u32,
    pub count: u32,
    pub t0: u32,
    pub t1: u32,
}

/// All edges, in a deterministic order (sorted by their vertex pair).
pub fn mesh_edges(tris: &[[u32; 3]]) -> Vec<SrcEdge> {
    let mut uses: Vec<(u64, u32)> = Vec::with_capacity(tris.len() * 3);
    for (ti, t) in tris.iter().enumerate() {
        for k in 0..3 {
            uses.push((edge_key(t[k], t[(k + 1) % 3]), ti as u32));
        }
    }
    uses.sort_unstable();
    let mut out: Vec<SrcEdge> = Vec::with_capacity(uses.len() / 2 + 1);
    let mut i = 0;
    while i < uses.len() {
        let key = uses[i].0;
        let mut j = i;
        while j < uses.len() && uses[j].0 == key {
            j += 1;
        }
        let (a, b) = key_ends(key);
        out.push(SrcEdge {
            a,
            b,
            count: (j - i) as u32,
            t0: uses[i].1,
            t1: if j - i > 1 { uses[i + 1].1 } else { u32::MAX },
        });
        i = j;
    }
    out
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

/// Feature lines of the source: hard edges as vertex pairs (`sharp`), and every feature segment (open borders,
/// group borders, hard edges) for snapping vertices onto the lines.
#[derive(Default)]
pub struct Features {
    pub sharp: HashSet<u64>,
    pub seg_a: Vec<V3>,
    pub seg_b: Vec<V3>,
}

/// `sharp_cos`: edges whose two faces have normals with a dot product below this are hard edges (>= 1 or NaN: none;
/// SculptForge uses cos(60 degrees)). Detected chains shorter than 1.5 `h0` are sculpt detail and dropped.
/// `user_sharp`: hard edges marked by the user (always kept, if they are edges of the mesh).
pub fn detect_features(m: &WorkMesh, keep_groups: bool, sharp_cos: f32, user_sharp: &[[u32; 2]], h0: f32) -> Features {
    let mut f = Features::default();
    let edges = mesh_edges(&m.t);
    let normals: Vec<V3> = m
        .t
        .iter()
        .map(|t| {
            let (a, b, c) = (m.p[t[0] as usize], m.p[t[1] as usize], m.p[t[2] as usize]);
            (b - a).cross(c - a).normalized()
        })
        .collect();
    let user: HashSet<u64> = user_sharp.iter().map(|e| edge_key(e[0], e[1])).collect();

    let mut detected: Vec<usize> = Vec::new();
    for (i, e) in edges.iter().enumerate() {
        let open = e.count != 2;
        let border = !open && keep_groups && m.group[e.t0 as usize] != m.group[e.t1 as usize];
        if open || border {
            f.seg_a.push(m.p[e.a as usize]);
            f.seg_b.push(m.p[e.b as usize]);
            continue;
        }
        if user.contains(&edge_key(e.a, e.b)) {
            f.sharp.insert(edge_key(e.a, e.b));
            f.seg_a.push(m.p[e.a as usize]);
            f.seg_b.push(m.p[e.b as usize]);
        } else if sharp_cos < 1.0 && normals[e.t0 as usize].dot(normals[e.t1 as usize]) < sharp_cos {
            detected.push(i);
        }
    }

    if !detected.is_empty() {
        let mut id: HashMap<u32, u32> = HashMap::new();
        for &i in &detected {
            for v in [edges[i].a, edges[i].b] {
                let n = id.len() as u32;
                id.entry(v).or_insert(n);
            }
        }
        let mut uf = UnionFind::new(id.len());
        for &i in &detected {
            uf.unite(id[&edges[i].a], id[&edges[i].b]);
        }
        let mut len = vec![0f64; id.len()];
        for &i in &detected {
            let r = uf.find(id[&edges[i].a]) as usize;
            len[r] += (m.p[edges[i].a as usize] - m.p[edges[i].b as usize]).length() as f64;
        }
        for &i in &detected {
            if len[uf.find(id[&edges[i].a]) as usize] < 1.5 * h0 as f64 {
                continue;
            }
            f.sharp.insert(edge_key(edges[i].a, edges[i].b));
            f.seg_a.push(m.p[edges[i].a as usize]);
            f.seg_b.push(m.p[edges[i].b as usize]);
        }
    }
    f
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::testmesh;

    #[test]
    fn quads_split_along_the_shorter_diagonal() {
        // a thin rhombus: diagonal 0-2 is long (2), 1-3 is short (0.2)
        let m = Mesh {
            positions: vec![[-1., 0., 0.], [0., -0.1, 0.], [1., 0., 0.], [0., 0.1, 0.]],
            face_sizes: vec![4],
            indices: vec![0, 1, 2, 3],
            attrs: Attributes::default(),
        };
        let w = WorkMesh::from_mesh(&m, false, false);
        assert_eq!(w.t.len(), 2);
        for t in &w.t {
            assert!(t.contains(&1) && t.contains(&3), "{:?}", t);
        }
    }

    #[test]
    fn convex_ngons_become_a_star_and_groups_follow_materials() {
        let m = Mesh {
            positions: vec![[0., 0., 0.], [1., 0., 0.], [2., 1., 0.], [1., 2., 0.], [0., 1., 0.]],
            face_sizes: vec![5],
            indices: vec![0, 1, 2, 3, 4],
            attrs: Attributes { materials: Some(vec![7]), colors: Some(vec![[1.0; 3]; 5]), ..Default::default() },
        };
        let w = WorkMesh::from_mesh(&m, true, true);
        assert_eq!(w.t.len(), 5, "a star of five triangles");
        assert_eq!(w.p.len(), 6, "with a centre vertex appended");
        assert_eq!(w.color.len(), 6, "and a color for it");
        assert_eq!(w.group, vec![7; 5]);
        assert!((w.area() - 2.5).abs() < 1e-5);
        assert!(WorkMesh::from_mesh(&m, false, false).group.iter().all(|&g| g == 0));
    }

    #[test]
    fn concave_ngons_are_fanned() {
        // an arrow head: the centre would see the notch from outside
        let m = Mesh {
            positions: vec![[0., 0., 0.], [4., 0., 0.], [4., 4., 0.], [2., 1., 0.], [0., 4., 0.]],
            face_sizes: vec![5],
            indices: vec![0, 1, 2, 3, 4],
            attrs: Attributes::default(),
        };
        let w = WorkMesh::from_mesh(&m, false, false);
        assert_eq!(w.t.len(), 3);
        assert_eq!(w.p.len(), 5);
    }

    #[test]
    fn edge_table_counts_uses() {
        let w = testmesh::icosphere(1);
        let e = mesh_edges(&w.t);
        assert_eq!(e.len(), w.t.len() * 3 / 2);
        assert!(e.iter().all(|e| e.count == 2 && e.a < e.b && e.t1 != u32::MAX));
        let plane = testmesh::grid_plane(3);
        let open = mesh_edges(&plane.t).iter().filter(|e| e.count == 1).count();
        assert_eq!(open, 12);
    }

    #[test]
    fn cube_has_twelve_long_hard_edges() {
        let w = testmesh::grid_cube(4);
        let f = detect_features(&w, false, (60f32).to_radians().cos(), &[], 0.5);
        assert_eq!(f.sharp.len(), 12 * 4);
        assert_eq!(f.seg_a.len(), f.sharp.len());
        // no angle detection: nothing
        assert!(detect_features(&w, false, 2.0, &[], 0.5).sharp.is_empty());
    }

    #[test]
    fn short_hard_chains_are_dropped_but_user_edges_stay() {
        let w = testmesh::grid_cube(4); // hard edges are 4 * 0.5 = 2 long each
        // the 12 edges join into one chain of length 24
        let f = detect_features(&w, false, (60f32).to_radians().cos(), &[], 20.0);
        assert!(f.sharp.is_empty());
        let e = mesh_edges(&w.t).into_iter().find(|e| e.count == 2).unwrap();
        let f = detect_features(&w, false, 2.0, &[[e.a, e.b]], 5.0);
        assert_eq!(f.sharp.len(), 1);
    }

    #[test]
    fn open_borders_and_group_borders_are_segments_only() {
        let plane = testmesh::grid_plane(4);
        let f = detect_features(&plane, false, 2.0, &[], 0.1);
        assert_eq!(f.seg_a.len(), 16);
        assert!(f.sharp.is_empty());
        let mut two = testmesh::grid_plane(4);
        let half = two.t.len() / 2;
        for g in two.group.iter_mut().skip(half) {
            *g = 1;
        }
        let none = detect_features(&two, false, 2.0, &[], 0.1).seg_a.len();
        let grouped = detect_features(&two, true, 2.0, &[], 0.1).seg_a.len();
        assert!(grouped > none);
    }
}
