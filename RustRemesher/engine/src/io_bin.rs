// SPDX-License-Identifier: GPL-3.0-or-later
//! Binary mesh exchange format (see FORMAT.md). Little-endian. Input files start with `RRMI0001`,
//! engine output with `RRMO0001`; the layout is the same and the reader accepts both.

use crate::mesh::{Attributes, Guide, Mesh};
use std::path::Path;

pub const MAGIC_IN: &[u8; 8] = b"RRMI0001";
pub const MAGIC_OUT: &[u8; 8] = b"RRMO0001";

const SEC_COLORS: &[u8; 4] = b"COLR";
const SEC_MATERIALS: &[u8; 4] = b"MATL";
const SEC_SHARP: &[u8; 4] = b"SHRP";
const SEC_GUIDES: &[u8; 4] = b"GUID";

// ---------------------------------------------------------------- writing

fn put_u32(out: &mut Vec<u8>, v: u32) {
    out.extend_from_slice(&v.to_le_bytes());
}

fn put_f32s(out: &mut Vec<u8>, v: &[[f32; 3]]) {
    for p in v {
        for c in p {
            out.extend_from_slice(&c.to_le_bytes());
        }
    }
}

fn put_section(out: &mut Vec<u8>, id: &[u8; 4], payload: &[u8]) {
    out.extend_from_slice(id);
    put_u32(out, payload.len() as u32);
    out.extend_from_slice(payload);
}

pub fn encode(mesh: &Mesh, magic: &[u8; 8]) -> Vec<u8> {
    let mut out = Vec::with_capacity(32 + mesh.positions.len() * 12 + mesh.indices.len() * 4);
    out.extend_from_slice(magic);
    put_u32(&mut out, mesh.positions.len() as u32);
    put_u32(&mut out, mesh.face_sizes.len() as u32);
    put_u32(&mut out, mesh.indices.len() as u32);
    put_f32s(&mut out, &mesh.positions);
    for &s in &mesh.face_sizes {
        put_u32(&mut out, s);
    }
    for &i in &mesh.indices {
        put_u32(&mut out, i);
    }

    let mut sections: Vec<(&[u8; 4], Vec<u8>)> = Vec::new();
    if let Some(c) = &mesh.attrs.colors {
        let mut p = Vec::with_capacity(c.len() * 12);
        put_f32s(&mut p, c);
        sections.push((SEC_COLORS, p));
    }
    if let Some(m) = &mesh.attrs.materials {
        let mut p = Vec::with_capacity(m.len() * 4);
        for &v in m {
            put_u32(&mut p, v);
        }
        sections.push((SEC_MATERIALS, p));
    }
    if !mesh.attrs.sharp_edges.is_empty() {
        let mut p = Vec::with_capacity(mesh.attrs.sharp_edges.len() * 8);
        for e in &mesh.attrs.sharp_edges {
            put_u32(&mut p, e[0]);
            put_u32(&mut p, e[1]);
        }
        sections.push((SEC_SHARP, p));
    }
    if !mesh.attrs.guides.is_empty() {
        let mut p = Vec::new();
        put_u32(&mut p, mesh.attrs.guides.len() as u32);
        for g in &mesh.attrs.guides {
            put_u32(&mut p, g.closed as u32);
            put_u32(&mut p, g.stroke);
            put_u32(&mut p, g.points.len() as u32);
            put_f32s(&mut p, &g.points);
        }
        sections.push((SEC_GUIDES, p));
    }
    put_u32(&mut out, sections.len() as u32);
    for (id, payload) in &sections {
        put_section(&mut out, id, payload);
    }
    out
}

pub fn write_file(path: &Path, mesh: &Mesh, magic: &[u8; 8]) -> std::io::Result<()> {
    std::fs::write(path, encode(mesh, magic))
}

// ---------------------------------------------------------------- reading

struct Reader<'a> {
    b: &'a [u8],
    p: usize,
}

impl<'a> Reader<'a> {
    fn new(b: &'a [u8]) -> Self {
        Reader { b, p: 0 }
    }

    fn take(&mut self, n: usize) -> Result<&'a [u8], String> {
        let end = self.p.checked_add(n).ok_or("size overflow")?;
        if end > self.b.len() {
            return Err("file is truncated".into());
        }
        let s = &self.b[self.p..end];
        self.p = end;
        Ok(s)
    }

    fn u32(&mut self) -> Result<u32, String> {
        Ok(u32::from_le_bytes(self.take(4)?.try_into().unwrap()))
    }

    fn u32s(&mut self, n: usize) -> Result<Vec<u32>, String> {
        let bytes = self.take(n.checked_mul(4).ok_or("size overflow")?)?;
        Ok(bytes.chunks_exact(4).map(|c| u32::from_le_bytes(c.try_into().unwrap())).collect())
    }

    fn vec3s(&mut self, n: usize) -> Result<Vec<[f32; 3]>, String> {
        let bytes = self.take(n.checked_mul(12).ok_or("size overflow")?)?;
        Ok(bytes
            .chunks_exact(12)
            .map(|c| {
                let f = |i: usize| f32::from_le_bytes(c[i * 4..i * 4 + 4].try_into().unwrap());
                [f(0), f(1), f(2)]
            })
            .collect())
    }
}

pub fn decode(bytes: &[u8]) -> Result<Mesh, String> {
    let mut r = Reader::new(bytes);
    let magic = r.take(8)?;
    if magic != MAGIC_IN && magic != MAGIC_OUT {
        return Err("not a RustRemesher mesh file (bad magic)".into());
    }
    let nv = r.u32()? as usize;
    let nf = r.u32()? as usize;
    let ni = r.u32()? as usize;
    let positions = r.vec3s(nv)?;
    let face_sizes = r.u32s(nf)?;
    let indices = r.u32s(ni)?;
    let mut attrs = Attributes::default();

    let nsec = r.u32()?;
    for _ in 0..nsec {
        let id: [u8; 4] = r.take(4)?.try_into().unwrap();
        let len = r.u32()? as usize;
        let mut s = Reader::new(r.take(len)?);
        match &id {
            SEC_COLORS => {
                if len != nv * 12 {
                    return Err("COLR section has the wrong size".into());
                }
                attrs.colors = Some(s.vec3s(nv)?);
            }
            SEC_MATERIALS => {
                if len != nf * 4 {
                    return Err("MATL section has the wrong size".into());
                }
                attrs.materials = Some(s.u32s(nf)?);
            }
            SEC_SHARP => {
                if !len.is_multiple_of(8) {
                    return Err("SHRP section has the wrong size".into());
                }
                let v = s.u32s(len / 4)?;
                attrs.sharp_edges = v.chunks_exact(2).map(|c| [c[0], c[1]]).collect();
            }
            SEC_GUIDES => {
                let n = s.u32()?;
                for _ in 0..n {
                    let closed = s.u32()? != 0;
                    let stroke = s.u32()?;
                    let np = s.u32()? as usize;
                    attrs.guides.push(Guide { closed, stroke, points: s.vec3s(np)? });
                }
            }
            _ => {} // unknown section: written by a newer addon, skipped
        }
    }
    Ok(Mesh { positions, face_sizes, indices, attrs })
}

pub fn read_file(path: &Path) -> Result<Mesh, String> {
    let bytes = std::fs::read(path).map_err(|e| format!("{}: {}", path.display(), e))?;
    decode(&bytes)
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::mesh::test_cube;

    fn full() -> Mesh {
        let mut m = test_cube();
        m.attrs.colors = Some((0..8).map(|i| [i as f32 / 8., 0.5, 1.0]).collect());
        m.attrs.materials = Some(vec![0, 1, 1, 2, 2, 2]);
        m.attrs.sharp_edges = vec![[0, 1], [2, 3]];
        m.attrs.guides = vec![
            Guide { closed: true, stroke: 3, points: vec![[0., 0., 0.], [1., 2., 3.], [4., 5., 6.]] },
            Guide { closed: false, stroke: 4, points: vec![[1., 1., 1.], [2., 2., 2.]] },
        ];
        m
    }

    #[test]
    fn roundtrip_with_all_sections() {
        let m = full();
        assert_eq!(decode(&encode(&m, MAGIC_IN)).unwrap(), m);
        assert_eq!(decode(&encode(&m, MAGIC_OUT)).unwrap(), m);
    }

    #[test]
    fn roundtrip_plain() {
        let m = test_cube();
        assert_eq!(decode(&encode(&m, MAGIC_IN)).unwrap(), m);
    }

    #[test]
    fn bytes_are_little_endian_with_magic() {
        let b = encode(&test_cube(), MAGIC_IN);
        assert_eq!(&b[..8], b"RRMI0001");
        assert_eq!(&b[8..12], &[8, 0, 0, 0]);
        assert_eq!(&b[12..16], &[6, 0, 0, 0]);
        assert_eq!(&b[16..20], &[24, 0, 0, 0]);
        assert_eq!(&b[20..24], &(-1.0f32).to_le_bytes());
    }

    #[test]
    fn rejects_bad_magic_and_truncation() {
        assert!(decode(b"NOTAMESH").is_err());
        let b = encode(&full(), MAGIC_IN);
        for cut in [4, 20, 100, b.len() - 1] {
            assert!(decode(&b[..cut]).is_err(), "cut at {}", cut);
        }
    }

    #[test]
    fn huge_counts_do_not_allocate() {
        let mut b = Vec::new();
        b.extend_from_slice(MAGIC_IN);
        for _ in 0..3 {
            b.extend_from_slice(&u32::MAX.to_le_bytes());
        }
        assert!(decode(&b).is_err());
    }

    #[test]
    fn unknown_sections_are_skipped() {
        let mut b = encode(&test_cube(), MAGIC_IN);
        let n = b.len();
        b[n - 4..].copy_from_slice(&1u32.to_le_bytes()); // section count 0 -> 1
        b.extend_from_slice(b"FUTR");
        b.extend_from_slice(&3u32.to_le_bytes());
        b.extend_from_slice(&[1, 2, 3]);
        assert_eq!(decode(&b).unwrap(), test_cube());
    }

    #[test]
    fn wrong_section_size_is_an_error() {
        let mut m = test_cube();
        m.attrs.materials = Some(vec![0; 6]);
        let mut b = encode(&m, MAGIC_IN);
        let n = b.len();
        // shrink the MATL length field (4 bytes before the 24-byte payload)
        b[n - 28..n - 24].copy_from_slice(&20u32.to_le_bytes());
        assert!(decode(&b).is_err());
    }
}
