// SPDX-License-Identifier: GPL-3.0-or-later
//! Wavefront OBJ, only for debugging by hand (the addon uses the binary format). Reads `v`, `f` and
//! `usemtl` (materials get ids in order of appearance); everything else is ignored. Quads and n-gons are kept.

use crate::mesh::{Attributes, Mesh};
use std::collections::HashMap;
use std::fmt::Write;
use std::path::Path;

pub fn parse(text: &str) -> Result<Mesh, String> {
    let mut positions: Vec<[f32; 3]> = Vec::new();
    let mut face_sizes = Vec::new();
    let mut indices: Vec<u32> = Vec::new();
    let mut mats: Vec<u32> = Vec::new();
    let mut names: HashMap<String, u32> = HashMap::new();
    let mut current = 0u32;

    for (ln, line) in text.lines().enumerate() {
        let line = line.trim();
        let mut it = line.split_whitespace();
        let err = |m: &str| format!("line {}: {}", ln + 1, m);
        match it.next() {
            Some("v") => {
                let mut c = [0f32; 3];
                for slot in c.iter_mut() {
                    *slot = it
                        .next()
                        .ok_or_else(|| err("vertex needs 3 coordinates"))?
                        .parse()
                        .map_err(|_| err("bad coordinate"))?;
                }
                positions.push(c);
            }
            Some("usemtl") => {
                let name = it.next().unwrap_or("").to_string();
                let next = names.len() as u32;
                current = *names.entry(name).or_insert(next);
            }
            Some("f") => {
                let mut n = 0u32;
                for tok in it {
                    let first = tok.split('/').next().unwrap_or("");
                    let i: i64 = first.parse().map_err(|_| err("bad face index"))?;
                    let idx = if i > 0 { i - 1 } else { positions.len() as i64 + i };
                    if idx < 0 {
                        return Err(err("face index out of range"));
                    }
                    indices.push(idx as u32);
                    n += 1;
                }
                face_sizes.push(n);
                mats.push(current);
            }
            _ => {}
        }
    }
    let materials = if names.is_empty() { None } else { Some(mats) };
    Ok(Mesh { positions, face_sizes, indices, attrs: Attributes { materials, ..Default::default() } })
}

pub fn encode(mesh: &Mesh) -> String {
    let mut s = String::new();
    for p in &mesh.positions {
        writeln!(s, "v {} {} {}", p[0], p[1], p[2]).unwrap();
    }
    let mut last = u32::MAX;
    for (fi, f) in mesh.faces().enumerate() {
        if let Some(m) = &mesh.attrs.materials {
            if m[fi] != last {
                last = m[fi];
                writeln!(s, "usemtl m{}", last).unwrap();
            }
        }
        s.push('f');
        for i in f {
            write!(s, " {}", i + 1).unwrap();
        }
        s.push('\n');
    }
    s
}

pub fn read_file(path: &Path) -> Result<Mesh, String> {
    let t = std::fs::read_to_string(path).map_err(|e| format!("{}: {}", path.display(), e))?;
    parse(&t)
}

pub fn write_file(path: &Path, mesh: &Mesh) -> std::io::Result<()> {
    std::fs::write(path, encode(mesh))
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::mesh::test_cube;

    #[test]
    fn quads_survive_roundtrip() {
        let m = test_cube();
        let back = parse(&encode(&m)).unwrap();
        assert_eq!(back.face_sizes, vec![4; 6]);
        assert_eq!(back.indices, m.indices);
        assert_eq!(back.positions, m.positions);
    }

    #[test]
    fn parses_slashes_negative_indices_and_materials() {
        let t = "v 0 0 0\nv 1 0 0\nv 1 1 0\nv 0 1 0\nusemtl a\nf 1/1/1 2/2/2 3/3/3 4/4/4\nusemtl b\nf -4 -3 -2\n";
        let m = parse(t).unwrap();
        assert_eq!(m.face_sizes, vec![4, 3]);
        assert_eq!(m.indices, vec![0, 1, 2, 3, 0, 1, 2]);
        assert_eq!(m.attrs.materials, Some(vec![0, 1]));
    }

    #[test]
    fn reports_bad_lines() {
        assert!(parse("v 1 2\n").is_err());
        assert!(parse("v 0 0 0\nf x\n").is_err());
        assert!(parse("f -5\n").is_err());
    }
}
