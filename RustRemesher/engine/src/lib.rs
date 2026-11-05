// SPDX-License-Identifier: GPL-3.0-or-later
//! RustRemesher engine. Phase 1: command line, settings, progress and mesh I/O are in place; `run`
//! validates and cleans the input and writes it back unchanged (no remeshing yet).

pub mod io_bin;
pub mod io_obj;
pub mod mesh;
pub mod progress;
pub mod settings;
pub mod topology;

use mesh::{CleanReport, Mesh};
use progress::Progress;
use settings::Settings;
use std::path::Path;
use topology::Stats;

pub const VERSION: &str = env!("CARGO_PKG_VERSION");

pub const ERR_SETTINGS: i32 = 1;
pub const ERR_READ: i32 = 2;
pub const ERR_MESH: i32 = 3;
pub const ERR_WRITE: i32 = 4;
pub const ERR_INTERNAL: i32 = 5;

#[derive(Debug)]
pub struct EngineError {
    pub code: i32,
    pub message: String,
}

impl EngineError {
    fn new(code: i32, message: impl Into<String>) -> Self {
        EngineError { code, message: message.into() }
    }
}

#[derive(Debug)]
pub struct RunReport {
    pub input: Stats,
    pub cleaned: CleanReport,
    pub output: Stats,
}

fn is_obj(p: &Path) -> bool {
    p.extension().is_some_and(|e| e.eq_ignore_ascii_case("obj"))
}

/// Reads a mesh by extension: `.obj` is OBJ, anything else the binary format.
pub fn read_mesh(path: &Path) -> Result<Mesh, String> {
    if is_obj(path) {
        io_obj::read_file(path)
    } else {
        io_bin::read_file(path)
    }
}

/// Writes a mesh by extension: `.obj` is OBJ, anything else the binary output format (`RRMO0001`).
pub fn write_mesh(path: &Path, mesh: &Mesh) -> std::io::Result<()> {
    if is_obj(path) {
        io_obj::write_file(path, mesh)
    } else {
        io_bin::write_file(path, mesh, io_bin::MAGIC_OUT)
    }
}

pub fn run(settings: &Settings, progress: &Progress) -> Result<RunReport, EngineError> {
    progress.update(0.02, "Reading input mesh", 0.2);
    let mut mesh = read_mesh(&settings.file_in).map_err(|e| EngineError::new(ERR_READ, format!("Cannot read input: {e}")))?;

    progress.update(0.10, "Checking mesh", 0.2);
    mesh.validate().map_err(|e| EngineError::new(ERR_MESH, format!("Invalid input mesh: {e}")))?;
    if mesh.face_count() == 0 {
        return Err(EngineError::new(ERR_MESH, "The input mesh has no faces"));
    }
    let input = topology::analyze(&mesh);

    progress.update(0.20, "Cleaning mesh", 0.2);
    let cleaned = mesh.clean();
    if mesh.face_count() == 0 {
        return Err(EngineError::new(ERR_MESH, "The input mesh has no usable faces"));
    }
    let output = topology::analyze(&mesh);

    // Phase 2+ replaces this with the actual remeshing.
    progress.update(0.90, "Writing result", 0.2);
    write_mesh(&settings.file_out, &mesh)
        .map_err(|e| EngineError::new(ERR_WRITE, format!("Cannot write output: {e}")))?;

    progress.done();
    Ok(RunReport { input, cleaned, output })
}
