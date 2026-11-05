// SPDX-License-Identifier: GPL-3.0-or-later
//! Runs the real executable the way the addon does: settings file in, progress file polled, mesh out.

use rustremesher::io_bin::{self, MAGIC_IN};
use rustremesher::mesh::{Attributes, Mesh};
use std::path::{Path, PathBuf};
use std::process::Command;

fn exe() -> Command {
    Command::new(env!("CARGO_BIN_EXE_rustremesher"))
}

fn workdir(name: &str) -> PathBuf {
    let d = std::env::temp_dir().join(format!("rr_e2e_{}_{}", std::process::id(), name));
    let _ = std::fs::remove_dir_all(&d);
    std::fs::create_dir_all(&d).unwrap();
    d
}

fn cube() -> Mesh {
    Mesh {
        positions: vec![
            [-1., -1., -1.], [1., -1., -1.], [1., 1., -1.], [-1., 1., -1.],
            [-1., -1., 1.], [1., -1., 1.], [1., 1., 1.], [-1., 1., 1.],
        ],
        face_sizes: vec![4; 6],
        indices: vec![0, 3, 2, 1, 4, 5, 6, 7, 0, 1, 5, 4, 2, 3, 7, 6, 1, 2, 6, 5, 3, 0, 4, 7],
        attrs: Attributes::default(),
    }
}

fn write_settings(dir: &Path, input: &Path, extra: &str) -> PathBuf {
    let s = dir.join("RetopoSettings.txt");
    let text = format!(
        "HostApp=Blender\nFileIn=\"{}\"\nFileOut=\"{}\"\nProgressFile=\"{}\"\nTargetQuadCount=100\n{}",
        input.display(),
        dir.join("out.rrm").display(),
        dir.join("progress.txt").display(),
        extra
    );
    std::fs::write(&s, text).unwrap();
    s
}

#[test]
fn quads_pass_through_and_progress_reports_success() {
    let dir = workdir("ok");
    let input = dir.join("in.rrm");
    let mut m = cube();
    m.attrs.materials = Some(vec![0, 0, 1, 1, 2, 2]);
    io_bin::write_file(&input, &m, MAGIC_IN).unwrap();
    let settings = write_settings(&dir, &input, "SymAxis=X\n");

    let out = exe().arg("-s").arg(&settings).output().unwrap();
    assert!(out.status.success(), "{}", String::from_utf8_lossy(&out.stderr));
    assert_eq!(std::fs::read_to_string(dir.join("progress.txt")).unwrap(), "2\n");

    let res = io_bin::read_file(&dir.join("out.rrm")).unwrap();
    assert_eq!(res.face_sizes, vec![4; 6]); // quads survive
    assert_eq!(res.attrs.materials, m.attrs.materials);
    let bytes = std::fs::read(dir.join("out.rrm")).unwrap();
    assert_eq!(&bytes[..8], b"RRMO0001");
}

#[test]
fn invalid_mesh_reports_a_negative_progress_value() {
    let dir = workdir("bad");
    let input = dir.join("in.rrm");
    let mut m = cube();
    m.indices[3] = 77;
    io_bin::write_file(&input, &m, MAGIC_IN).unwrap();
    let settings = write_settings(&dir, &input, "");

    let out = exe().arg("-s").arg(&settings).output().unwrap();
    assert_eq!(out.status.code(), Some(3));
    let p = std::fs::read_to_string(dir.join("progress.txt")).unwrap();
    let mut lines = p.lines();
    assert_eq!(lines.next(), Some("-3"));
    assert!(lines.next().unwrap().contains("out of range"));
    assert!(!dir.join("out.rrm").exists());
}

#[test]
fn missing_input_and_bad_settings_are_reported() {
    let dir = workdir("missing");
    let settings = write_settings(&dir, &dir.join("nope.rrm"), "");
    assert_eq!(exe().arg("-s").arg(&settings).status().unwrap().code(), Some(2));
    assert!(std::fs::read_to_string(dir.join("progress.txt")).unwrap().starts_with("-2\n"));

    let settings = write_settings(&dir, &dir.join("nope.rrm"), "TargetQuadCount=abc\n");
    assert_eq!(exe().arg("-s").arg(&settings).status().unwrap().code(), Some(1));
    assert!(std::fs::read_to_string(dir.join("progress.txt")).unwrap().starts_with("-1\n"));
}

#[test]
fn convert_roundtrip_keeps_quads() {
    let dir = workdir("conv");
    let obj = dir.join("cube.obj");
    rustremesher::io_obj::write_file(&obj, &cube()).unwrap();
    let bin = dir.join("cube.rrm");
    assert!(exe().args(["--convert"]).arg(&obj).arg(&bin).status().unwrap().success());
    let back = io_bin::read_file(&bin).unwrap();
    assert_eq!(back.face_sizes, vec![4; 6]);
    assert_eq!(&std::fs::read(&bin).unwrap()[..8], b"RRMI0001");
    let info = exe().arg("--info").arg(&bin).output().unwrap();
    let text = String::from_utf8_lossy(&info.stdout);
    assert!(info.status.success() && text.contains("6 quads") && text.contains("euler 2"), "{text}");
}

#[test]
fn usage_on_bad_arguments() {
    assert_eq!(exe().status().unwrap().code(), Some(1));
    let v = exe().arg("--version").output().unwrap();
    assert!(String::from_utf8_lossy(&v.stdout).starts_with("rustremesher "));
}
