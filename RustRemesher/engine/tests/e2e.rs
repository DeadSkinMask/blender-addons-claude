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
fn a_cube_is_remeshed_to_about_the_wanted_quads_and_progress_reports_success() {
    let dir = workdir("ok");
    let input = dir.join("in.rrm");
    io_bin::write_file(&input, &cube(), MAGIC_IN).unwrap();
    // the settings file as the addon writes it: CRLF-free, quoted paths, TargetQuadCount=100
    let settings = write_settings(&dir, &input, "SymAxis=X\n");

    let out = exe().arg("-s").arg(&settings).output().unwrap();
    assert!(out.status.success(), "{}", String::from_utf8_lossy(&out.stderr));
    assert_eq!(std::fs::read_to_string(dir.join("progress.txt")).unwrap(), "2\n");

    let bytes = std::fs::read(dir.join("out.rrm")).unwrap();
    assert_eq!(&bytes[..8], b"RRMO0001");
    let res = io_bin::read_file(&dir.join("out.rrm")).unwrap();
    assert!(res.face_sizes.iter().all(|&s| s == 4), "all quads");
    let st = rustremesher::topology::analyze(&res);
    assert!(st.is_closed_manifold() && st.euler == 2, "{st:?}");
    assert!((res.face_count() as f64 / 100.0 - 1.0).abs() < 0.3, "{} quads for 100", res.face_count());
    // the cube's corners are still there
    for c in [[-1.0f32, -1.0, -1.0], [1.0, 1.0, 1.0], [-1.0, 1.0, -1.0], [1.0, -1.0, 1.0]] {
        assert!(res.positions.iter().any(|p| (0..3).all(|k| (p[k] - c[k]).abs() < 0.05)), "corner {c:?} lost");
    }
}

#[test]
fn the_progress_file_only_ever_holds_valid_non_decreasing_values_and_ends_at_two() {
    let dir = workdir("progress");
    let input = dir.join("in.rrm");
    // an open band of a UV sphere, dense enough that the engine works for a moment
    let n = 60usize;
    let mut m = Mesh::default();
    for j in 0..=n {
        for i in 0..=n {
            let (u, v) = (std::f32::consts::PI * 2.0 * i as f32 / n as f32, std::f32::consts::PI * j as f32 / n as f32);
            m.positions.push([v.sin() * u.cos(), v.sin() * u.sin(), v.cos()]);
        }
    }
    let id = |i: usize, j: usize| (j * (n + 1) + i % (n + 1)) as u32;
    for j in 1..n - 1 {
        for i in 0..n {
            m.face_sizes.push(4);
            m.indices.extend_from_slice(&[id(i, j), id(i + 1, j), id(i + 1, j + 1), id(i, j + 1)]);
        }
    }
    io_bin::write_file(&input, &m, MAGIC_IN).unwrap();
    let settings = write_settings(&dir, &input, "TargetQuadCount=400\n");
    let mut child = exe().arg("-s").arg(&settings).spawn().unwrap();
    let progress = dir.join("progress.txt");
    let mut seen: Vec<f64> = Vec::new();
    while child.try_wait().unwrap().is_none() {
        if let Ok(t) = std::fs::read_to_string(&progress) {
            // the file is replaced atomically, so whatever we read is a whole file
            let first = t.lines().next().expect("a progress file is never empty");
            let value: f64 = first.parse().expect("the first line is a number");
            assert!((0.0..=1.0).contains(&value) || value == 2.0, "invalid progress value {value}");
            if seen.last() != Some(&value) {
                seen.push(value);
            }
        }
        std::thread::sleep(std::time::Duration::from_millis(1));
    }
    assert_eq!(std::fs::read_to_string(&progress).unwrap(), "2\n");
    assert!(seen.windows(2).all(|w| w[0] <= w[1]), "progress went backwards: {seen:?}");
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

#[test]
fn proxy_command_writes_a_closed_triangle_mesh() {
    // a subdivided cube given as quads: 12 hard edges must be detected and the result stays a closed sphere
    let dir = workdir("proxy");
    let n = 12usize;
    let faces: [([f32; 3], [f32; 3], [f32; 3]); 6] = [
        ([1., -1., -1.], [0., 1., 0.], [0., 0., 1.]),
        ([-1., -1., -1.], [0., 0., 1.], [0., 1., 0.]),
        ([-1., 1., -1.], [0., 0., 1.], [1., 0., 0.]),
        ([-1., -1., -1.], [1., 0., 0.], [0., 0., 1.]),
        ([-1., -1., 1.], [1., 0., 0.], [0., 1., 0.]),
        ([-1., -1., -1.], [0., 1., 0.], [1., 0., 0.]),
    ];
    let mut m = Mesh::default();
    let mut weld = std::collections::HashMap::new();
    let step = 2.0 / n as f32;
    for (o, u, v) in faces {
        let mut vid = |i: usize, j: usize| -> u32 {
            let q = [0, 1, 2].map(|k| o[k] + u[k] * step * i as f32 + v[k] * step * j as f32);
            let key = q.map(|c| (c * 1000.0).round() as i32);
            *weld.entry(key).or_insert_with(|| {
                m.positions.push(q);
                m.positions.len() as u32 - 1
            })
        };
        for j in 0..n {
            for i in 0..n {
                let quad = [vid(i, j), vid(i + 1, j), vid(i + 1, j + 1), vid(i, j + 1)];
                m.face_sizes.push(4);
                m.indices.extend_from_slice(&quad);
            }
        }
    }
    let input = dir.join("cube.rrm");
    io_bin::write_file(&input, &m, MAGIC_IN).unwrap();
    let out_path = dir.join("proxy.rrm");
    let out = exe().arg("--proxy").arg(&input).arg(&out_path).arg("300").output().unwrap();
    let text = String::from_utf8_lossy(&out.stdout).to_string();
    assert!(out.status.success(), "{text}{}", String::from_utf8_lossy(&out.stderr));
    assert!(text.contains("144 hard edges"), "{text}"); // 12 cube edges x 12 segments
    let proxy = io_bin::read_file(&out_path).unwrap();
    assert!(proxy.face_sizes.iter().all(|&s| s == 3));
    let st = rustremesher::topology::analyze(&proxy);
    assert!(st.is_closed_manifold() && st.euler == 2, "{st:?}");
    // about area / (0.433 edge^2) triangles, edge = sqrt(area / quads) / 3 (here the proxy is finer than the input)
    let want = 24.0 / (0.433 * (f64::sqrt(24.0 / 300.0) / 3.0).powi(2));
    let got = proxy.face_count() as f64;
    assert!(got > 0.5 * want && got < 2.0 * want, "{got} triangles, wanted about {want:.0}");
}

#[test]
fn proxy_command_rejects_a_bad_quad_count() {
    let dir = workdir("proxybad");
    let input = dir.join("cube.rrm");
    io_bin::write_file(&input, &cube(), MAGIC_IN).unwrap();
    assert_eq!(exe().arg("--proxy").arg(&input).arg(dir.join("o.rrm")).arg("abc").status().unwrap().code(), Some(1));
}
