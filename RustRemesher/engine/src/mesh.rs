// SPDX-License-Identifier: GPL-3.0-or-later
//! Polygon mesh (triangles, quads, n-gons) plus the optional attributes the addon sends.

#[derive(Clone, Debug, Default, PartialEq)]
pub struct Guide {
    pub closed: bool,
    pub stroke: u32,
    pub points: Vec<[f32; 3]>,
}

#[derive(Clone, Debug, Default, PartialEq)]
pub struct Attributes {
    /// Per vertex RGB (density paint).
    pub colors: Option<Vec<[f32; 3]>>,
    /// Per face material id.
    pub materials: Option<Vec<u32>>,
    /// Hard edges as vertex pairs.
    pub sharp_edges: Vec<[u32; 2]>,
    /// Curves the edge flow should follow.
    pub guides: Vec<Guide>,
}

#[derive(Clone, Debug, Default, PartialEq)]
pub struct Mesh {
    pub positions: Vec<[f32; 3]>,
    /// Vertex count of each face.
    pub face_sizes: Vec<u32>,
    /// Concatenated vertex indices of all faces.
    pub indices: Vec<u32>,
    pub attrs: Attributes,
}

#[derive(Clone, Copy, Debug, Default, PartialEq, Eq)]
pub struct CleanReport {
    pub dropped_faces: usize,
    pub dropped_vertices: usize,
}

impl Mesh {
    pub fn face_count(&self) -> usize {
        self.face_sizes.len()
    }

    /// Faces as index slices. Only valid on a mesh that passed `validate`.
    pub fn faces(&self) -> impl Iterator<Item = &[u32]> + '_ {
        let mut off = 0usize;
        self.face_sizes.iter().map(move |&s| {
            let s = s as usize;
            let f = &self.indices[off..off + s];
            off += s;
            f
        })
    }

    pub fn validate(&self) -> Result<(), String> {
        let v = self.positions.len();
        if v > u32::MAX as usize {
            return Err("too many vertices".into());
        }
        let total: u64 = self.face_sizes.iter().map(|&s| s as u64).sum();
        if total != self.indices.len() as u64 {
            return Err(format!(
                "face sizes add up to {} indices but there are {}",
                total,
                self.indices.len()
            ));
        }
        if let Some(i) = self.face_sizes.iter().position(|&s| s < 3) {
            return Err(format!("face {} has fewer than 3 vertices", i));
        }
        if let Some(&i) = self.indices.iter().find(|&&i| i as usize >= v) {
            return Err(format!("vertex index {} out of range (vertices: {})", i, v));
        }
        if let Some(i) = self.positions.iter().position(|p| p.iter().any(|c| !c.is_finite())) {
            return Err(format!("vertex {} has a non-finite coordinate", i));
        }
        if let Some(c) = &self.attrs.colors {
            if c.len() != v {
                return Err(format!("{} vertex colors for {} vertices", c.len(), v));
            }
        }
        if let Some(m) = &self.attrs.materials {
            if m.len() != self.face_sizes.len() {
                return Err(format!("{} material ids for {} faces", m.len(), self.face_sizes.len()));
            }
        }
        if self.attrs.sharp_edges.iter().any(|e| e[0] as usize >= v || e[1] as usize >= v) {
            return Err("hard edge refers to a missing vertex".into());
        }
        Ok(())
    }

    /// Drops faces that repeat a vertex (degenerate) and vertices no face uses, keeping attributes in step.
    /// Expects a validated mesh.
    pub fn clean(&mut self) -> CleanReport {
        let old_faces = self.face_sizes.len();
        let old_verts = self.positions.len();

        let mut sizes = Vec::with_capacity(old_faces);
        let mut indices = Vec::with_capacity(self.indices.len());
        let mut mats = self.attrs.materials.as_ref().map(|_| Vec::with_capacity(old_faces));
        let mut scratch: Vec<u32> = Vec::new();
        let mut off = 0usize;
        for (fi, &s) in self.face_sizes.iter().enumerate() {
            let f = &self.indices[off..off + s as usize];
            off += s as usize;
            scratch.clear();
            scratch.extend_from_slice(f);
            scratch.sort_unstable();
            if scratch.windows(2).any(|w| w[0] == w[1]) {
                continue;
            }
            sizes.push(s);
            indices.extend_from_slice(f);
            if let (Some(out), Some(src)) = (mats.as_mut(), self.attrs.materials.as_ref()) {
                out.push(src[fi]);
            }
        }

        let mut remap = vec![u32::MAX; old_verts];
        let mut positions = Vec::new();
        let mut colors = self.attrs.colors.as_ref().map(|_| Vec::new());
        for idx in indices.iter_mut() {
            let r = &mut remap[*idx as usize];
            if *r == u32::MAX {
                *r = positions.len() as u32;
                positions.push(self.positions[*idx as usize]);
                if let (Some(out), Some(src)) = (colors.as_mut(), self.attrs.colors.as_ref()) {
                    out.push(src[*idx as usize]);
                }
            }
            *idx = *r;
        }

        let sharp: Vec<[u32; 2]> = self
            .attrs
            .sharp_edges
            .iter()
            .filter_map(|e| {
                let (a, b) = (remap[e[0] as usize], remap[e[1] as usize]);
                (a != u32::MAX && b != u32::MAX && a != b).then_some([a, b])
            })
            .collect();

        self.positions = positions;
        self.face_sizes = sizes;
        self.indices = indices;
        self.attrs.materials = mats;
        self.attrs.colors = colors;
        self.attrs.sharp_edges = sharp;

        CleanReport {
            dropped_faces: old_faces - self.face_sizes.len(),
            dropped_vertices: old_verts - self.positions.len(),
        }
    }
}

#[cfg(test)]
pub fn test_cube() -> Mesh {
    let positions = vec![
        [-1., -1., -1.], [1., -1., -1.], [1., 1., -1.], [-1., 1., -1.],
        [-1., -1., 1.], [1., -1., 1.], [1., 1., 1.], [-1., 1., 1.],
    ];
    let indices = vec![
        0, 3, 2, 1, // -z
        4, 5, 6, 7, // +z
        0, 1, 5, 4, // -y
        2, 3, 7, 6, // +y
        1, 2, 6, 5, // +x
        3, 0, 4, 7, // -x
    ];
    Mesh { positions, face_sizes: vec![4; 6], indices, attrs: Attributes::default() }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn cube_validates() {
        assert!(test_cube().validate().is_ok());
        assert_eq!(test_cube().faces().count(), 6);
    }

    #[test]
    fn validate_rejects_bad_input() {
        let mut m = test_cube();
        m.indices[0] = 99;
        assert!(m.validate().is_err());
        let mut m = test_cube();
        m.face_sizes[0] = 2;
        assert!(m.validate().is_err());
        let mut m = test_cube();
        m.positions[0][1] = f32::NAN;
        assert!(m.validate().is_err());
        let mut m = test_cube();
        m.attrs.materials = Some(vec![0; 3]);
        assert!(m.validate().is_err());
    }

    #[test]
    fn clean_drops_degenerate_faces_and_unused_vertices() {
        let mut m = test_cube();
        m.positions.push([5., 5., 5.]); // unused
        m.face_sizes.push(3);
        m.indices.extend_from_slice(&[0, 1, 1]); // degenerate
        m.attrs.materials = Some(vec![0, 1, 2, 3, 4, 5, 6]);
        m.attrs.colors = Some(vec![[0.5; 3]; 9]);
        m.attrs.sharp_edges = vec![[0, 1], [8, 0]];
        assert!(m.validate().is_ok());
        let r = m.clean();
        assert_eq!(r, CleanReport { dropped_faces: 1, dropped_vertices: 1 });
        assert_eq!(m.face_count(), 6);
        assert_eq!(m.attrs.materials.as_ref().unwrap().len(), 6);
        assert_eq!(m.attrs.colors.as_ref().unwrap().len(), 8);
        // old edge (0, 1) must still join the same two points after renumbering
        assert_eq!(m.attrs.sharp_edges.len(), 1);
        let e = m.attrs.sharp_edges[0];
        assert_eq!(m.positions[e[0] as usize], [-1., -1., -1.]);
        assert_eq!(m.positions[e[1] as usize], [1., -1., -1.]);
        assert!(m.validate().is_ok());
    }

    #[test]
    fn clean_remaps_vertices_consistently() {
        let mut m = Mesh {
            positions: vec![[9., 9., 9.], [0., 0., 0.], [1., 0., 0.], [0., 1., 0.]],
            face_sizes: vec![3],
            indices: vec![1, 2, 3],
            attrs: Attributes::default(),
        };
        m.clean();
        assert_eq!(m.positions, vec![[0., 0., 0.], [1., 0., 0.], [0., 1., 0.]]);
        assert_eq!(m.indices, vec![0, 1, 2]);
    }

}

