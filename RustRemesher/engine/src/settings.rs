// SPDX-License-Identifier: GPL-3.0-or-later
//! Settings file: one `Key=value` per line, values optionally in double quotes. The keys are the ones the
//! Quad Remesher bridge writes (`HostApp`, `HostAppVer` and unknown keys are ignored).

use std::path::{Path, PathBuf};

#[derive(Clone, Debug, PartialEq)]
pub struct Settings {
    pub file_in: PathBuf,
    pub file_out: PathBuf,
    pub progress_file: Option<PathBuf>,
    pub target_quad_count: u32,
    /// 0..100 (percent), 0 = uniform quad size.
    pub curvature_adaptivness: f32,
    pub exact_quad_count: bool,
    pub use_vertex_color_map: bool,
    pub use_material_ids: bool,
    pub use_indexed_normals: bool,
    pub auto_detect_hard_edges: bool,
    /// X, Y, Z mirror planes.
    pub sym_axis: [bool; 3],
    pub sym_local: bool,
    /// 0..100: how far around a guide curve the flow follows it.
    pub guide_strength: f32,
    /// 0 = all hardware threads.
    pub threads: usize,
}

impl Default for Settings {
    fn default() -> Self {
        Settings {
            file_in: PathBuf::new(),
            file_out: PathBuf::new(),
            progress_file: None,
            target_quad_count: 5000,
            curvature_adaptivness: 50.0,
            exact_quad_count: false,
            use_vertex_color_map: false,
            use_material_ids: false,
            use_indexed_normals: false,
            auto_detect_hard_edges: true,
            sym_axis: [false; 3],
            sym_local: true,
            guide_strength: 50.0,
            threads: 0,
        }
    }
}

pub fn parse_pairs(text: &str) -> Result<Vec<(String, String)>, String> {
    let mut out = Vec::new();
    for (i, raw) in text.trim_start_matches('\u{feff}').lines().enumerate() {
        let line = raw.trim();
        if line.is_empty() || line.starts_with('#') {
            continue;
        }
        let (k, v) = line.split_once('=').ok_or_else(|| format!("line {}: expected Key=value", i + 1))?;
        let mut v = v.trim();
        if v.len() >= 2 && v.starts_with('"') && v.ends_with('"') {
            v = &v[1..v.len() - 1];
        }
        out.push((k.trim().to_string(), v.to_string()));
    }
    Ok(out)
}

pub fn read_pairs(path: &Path) -> Result<Vec<(String, String)>, String> {
    let text = std::fs::read_to_string(path).map_err(|e| format!("{}: {}", path.display(), e))?;
    parse_pairs(&text)
}

fn parse_bool(key: &str, v: &str) -> Result<bool, String> {
    match v.to_ascii_lowercase().as_str() {
        "1" | "true" | "yes" | "on" => Ok(true),
        "0" | "false" | "no" | "off" => Ok(false),
        _ => Err(format!("{}: '{}' is not a boolean", key, v)),
    }
}

fn parse_num(key: &str, v: &str) -> Result<f64, String> {
    v.parse::<f64>()
        .ok()
        .filter(|x| x.is_finite())
        .ok_or_else(|| format!("{}: '{}' is not a number", key, v))
}

impl Settings {
    pub fn from_pairs(pairs: &[(String, String)]) -> Result<Settings, String> {
        let mut s = Settings::default();
        let (mut has_in, mut has_out) = (false, false);
        for (k, v) in pairs {
            match k.as_str() {
                "FileIn" => {
                    s.file_in = PathBuf::from(v);
                    has_in = true;
                }
                "FileOut" => {
                    s.file_out = PathBuf::from(v);
                    has_out = true;
                }
                "ProgressFile" => s.progress_file = (!v.is_empty()).then(|| PathBuf::from(v)),
                "TargetQuadCount" => {
                    let n = parse_num(k, v)?.round();
                    if !(1.0..=u32::MAX as f64).contains(&n) {
                        return Err(format!("{}: must be at least 1", k));
                    }
                    s.target_quad_count = n as u32;
                }
                "CurvatureAdaptivness" => s.curvature_adaptivness = parse_num(k, v)?.clamp(0.0, 100.0) as f32,
                "ExactQuadCount" => s.exact_quad_count = parse_bool(k, v)?,
                "UseVertexColorMap" => s.use_vertex_color_map = parse_bool(k, v)?,
                "UseMaterialIds" => s.use_material_ids = parse_bool(k, v)?,
                "UseIndexedNormals" => s.use_indexed_normals = parse_bool(k, v)?,
                "AutoDetectHardEdges" => s.auto_detect_hard_edges = parse_bool(k, v)?,
                "SymAxis" => {
                    s.sym_axis = [false; 3];
                    for c in v.chars() {
                        match c.to_ascii_uppercase() {
                            'X' => s.sym_axis[0] = true,
                            'Y' => s.sym_axis[1] = true,
                            'Z' => s.sym_axis[2] = true,
                            _ => return Err(format!("{}: '{}' is not X, Y or Z", k, c)),
                        }
                    }
                }
                "SymLocal" => s.sym_local = parse_bool(k, v)?,
                "GuideStrength" => s.guide_strength = parse_num(k, v)?.clamp(0.0, 100.0) as f32,
                "Threads" => s.threads = parse_num(k, v)?.max(0.0) as usize,
                _ => {}
            }
        }
        if !has_in {
            return Err("FileIn is missing".into());
        }
        if !has_out {
            return Err("FileOut is missing".into());
        }
        Ok(s)
    }

    pub fn parse(text: &str) -> Result<Settings, String> {
        Settings::from_pairs(&parse_pairs(text)?)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    // What the bridge's writeSettingsFile produces (Windows paths, str(bool) for some flags, %d for others).
    const SAMPLE: &str = "HostApp=Blender\nHostAppVer=5.2.3\nFileIn=\"C:/tmp/in file.rrm\"\nFileOut=\"C:/tmp/out.rrm\"\n\
        ProgressFile=\"C:/tmp/progress.txt\"\nTargetQuadCount=1234\nCurvatureAdaptivness=25.0\nExactQuadCount=1\n\
        UseVertexColorMap=True\nUseMaterialIds=0\nUseIndexedNormals=1\nAutoDetectHardEdges=0\nSymAxis=XZ\nSymLocal=1\n";

    #[test]
    fn parses_bridge_style_file() {
        let s = Settings::parse(SAMPLE).unwrap();
        assert_eq!(s.file_in, PathBuf::from("C:/tmp/in file.rrm"));
        assert_eq!(s.progress_file, Some(PathBuf::from("C:/tmp/progress.txt")));
        assert_eq!(s.target_quad_count, 1234);
        assert_eq!(s.curvature_adaptivness, 25.0);
        assert!(s.exact_quad_count && s.use_vertex_color_map && s.use_indexed_normals && s.sym_local);
        assert!(!s.use_material_ids && !s.auto_detect_hard_edges);
        assert_eq!(s.sym_axis, [true, false, true]);
    }

    #[test]
    fn defaults_and_required_keys() {
        let s = Settings::parse("FileIn=a\nFileOut=b\n").unwrap();
        assert_eq!(s.target_quad_count, 5000);
        assert_eq!(s.sym_axis, [false; 3]);
        assert!(s.progress_file.is_none());
        assert!(Settings::parse("FileOut=b\n").is_err());
        assert!(Settings::parse("FileIn=a\n").is_err());
    }

    #[test]
    fn rejects_bad_values() {
        let base = "FileIn=a\nFileOut=b\n";
        assert!(Settings::parse(&format!("{base}TargetQuadCount=0\n")).is_err());
        assert!(Settings::parse(&format!("{base}TargetQuadCount=abc\n")).is_err());
        assert!(Settings::parse(&format!("{base}ExactQuadCount=maybe\n")).is_err());
        assert!(Settings::parse(&format!("{base}SymAxis=XQ\n")).is_err());
        assert!(Settings::parse("FileIn\n").is_err());
    }

    #[test]
    fn clamps_percentages_and_ignores_unknown_keys() {
        let s = Settings::parse("FileIn=a\nFileOut=b\nCurvatureAdaptivness=250\nSomethingNew=1\n# note\n").unwrap();
        assert_eq!(s.curvature_adaptivness, 100.0);
    }

    #[test]
    fn handles_bom_and_crlf() {
        let s = Settings::parse("\u{feff}FileIn=a\r\nFileOut=b\r\nTargetQuadCount=77\r\n").unwrap();
        assert_eq!(s.target_quad_count, 77);
        assert_eq!(s.file_out, PathBuf::from("b"));
    }
}
