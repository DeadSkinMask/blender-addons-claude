// SPDX-License-Identifier: GPL-3.0-or-later
use rustremesher::progress::Progress;
use rustremesher::settings::{self, Settings};
use rustremesher::topology::{self, Stats};
use rustremesher::{read_mesh, run, write_mesh, ERR_INTERNAL, ERR_MESH, ERR_READ, ERR_SETTINGS, ERR_WRITE};
use std::path::{Path, PathBuf};
use std::process::ExitCode;

const USAGE: &str = "\
rustremesher - quad remesher engine

USAGE:
  rustremesher -s <settings.txt>        remesh as described by the settings file
  rustremesher --info <mesh>            print mesh statistics
  rustremesher --convert <in> <out>     convert between .obj and the binary format (debugging)
  rustremesher --version

Meshes ending in .obj are OBJ; any other extension is the binary format (FORMAT.md).";

fn print_stats(label: &str, s: &Stats) {
    println!(
        "{label}: {} vertices, {} faces ({} quads, {} tris, {} n-gons), {} edges, {} boundary, {} non-manifold, \
         {} flipped, {} components, euler {}",
        s.vertices, s.faces, s.quads, s.triangles, s.ngons, s.edges, s.boundary_edges, s.non_manifold_edges,
        s.inconsistent_edges, s.components, s.euler
    );
}

fn cmd_remesh(settings_path: &Path) -> u8 {
    let pairs = match settings::read_pairs(settings_path) {
        Ok(p) => p,
        Err(e) => {
            eprintln!("settings: {e}");
            return ERR_SETTINGS as u8;
        }
    };
    // Even if the settings are invalid we can still tell the addon through the progress file.
    let progress_path = pairs.iter().find(|(k, _)| k == "ProgressFile").map(|(_, v)| PathBuf::from(v));
    let progress = Progress::new(progress_path);
    let settings = match Settings::from_pairs(&pairs) {
        Ok(s) => s,
        Err(e) => {
            eprintln!("settings: {e}");
            progress.error(ERR_SETTINGS, &format!("Invalid settings: {e}"));
            return ERR_SETTINGS as u8;
        }
    };

    let result = std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| run(&settings, &progress)));
    match result {
        Ok(Ok(report)) => {
            print_stats("input ", &report.input);
            println!(
                "cleaned: {} degenerate faces and {} unused vertices removed",
                report.cleaned.dropped_faces, report.cleaned.dropped_vertices
            );
            print_stats("output", &report.output);
            0
        }
        Ok(Err(e)) => {
            eprintln!("error: {}", e.message);
            progress.error(e.code, &e.message);
            e.code as u8
        }
        Err(_) => {
            progress.error(ERR_INTERNAL, "Internal engine failure");
            ERR_INTERNAL as u8
        }
    }
}

fn cmd_info(path: &Path) -> u8 {
    let mesh = match read_mesh(path) {
        Ok(m) => m,
        Err(e) => {
            eprintln!("{e}");
            return ERR_READ as u8;
        }
    };
    if let Err(e) = mesh.validate() {
        eprintln!("invalid mesh: {e}");
        return ERR_MESH as u8;
    }
    print_stats("mesh", &topology::analyze(&mesh));
    println!(
        "attributes: colors={} materials={} hard edges={} guides={}",
        mesh.attrs.colors.is_some(),
        mesh.attrs.materials.is_some(),
        mesh.attrs.sharp_edges.len(),
        mesh.attrs.guides.len()
    );
    0
}

fn cmd_convert(input: &Path, output: &Path) -> u8 {
    let mesh = match read_mesh(input) {
        Ok(m) => m,
        Err(e) => {
            eprintln!("{e}");
            return ERR_READ as u8;
        }
    };
    // Converting to the binary format yields an *input* file (RRMI0001) so it can be fed to the engine.
    let res = if output.extension().is_some_and(|e| e.eq_ignore_ascii_case("obj")) {
        write_mesh(output, &mesh)
    } else {
        rustremesher::io_bin::write_file(output, &mesh, rustremesher::io_bin::MAGIC_IN)
    };
    match res {
        Ok(()) => 0,
        Err(e) => {
            eprintln!("{}: {e}", output.display());
            ERR_WRITE as u8
        }
    }
}

fn main() -> ExitCode {
    let args: Vec<String> = std::env::args().skip(1).collect();
    let code = match args.iter().map(String::as_str).collect::<Vec<_>>().as_slice() {
        ["-s", file] => cmd_remesh(Path::new(file)),
        ["--info", file] => cmd_info(Path::new(file)),
        ["--convert", a, b] => cmd_convert(Path::new(a), Path::new(b)),
        ["--version"] => {
            println!("rustremesher {}", rustremesher::VERSION);
            0
        }
        ["--help"] | ["-h"] => {
            println!("{USAGE}");
            0
        }
        _ => {
            eprintln!("{USAGE}");
            ERR_SETTINGS as u8
        }
    };
    ExitCode::from(code)
}
