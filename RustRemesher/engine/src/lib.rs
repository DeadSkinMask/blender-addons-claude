// SPDX-License-Identifier: GPL-3.0-or-later
//! RustRemesher engine. Phases 1-2: command line, settings, progress, mesh I/O, BVH and the proxy surface are in
//! place; `run` still validates and cleans the input and writes it back unchanged (the remeshing itself comes in
//! the next phases). `make_proxy` / `--proxy` expose the proxy for inspection.

pub mod bvh;
pub mod complex;
pub mod field;
pub mod io_bin;
pub mod io_obj;
pub mod mesh;
pub mod progress;
pub mod proxy;
pub mod settings;
pub mod solver;
#[cfg(test)]
mod testmesh;
pub mod topology;
pub mod vec3;
pub mod workmesh;

use bvh::{SegmentBvh, TriangleBvh};
use mesh::{CleanReport, Mesh};
use proxy::Proxy;
use workmesh::{detect_features, WorkMesh};
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

/// Uses `threads` worker threads (0 = all hardware threads). Only the first call has an effect.
pub fn init_threads(threads: usize) {
    if threads > 0 {
        let _ = rayon::ThreadPoolBuilder::new().num_threads(threads).build_global();
    }
}

#[derive(Debug)]
pub struct ProxyInfo {
    pub source_triangles: usize,
    pub area: f64,
    /// Wanted quad edge length, sqrt(area / target quads).
    pub quad_edge: f32,
    /// Proxy triangle edge length.
    pub proxy_edge: f32,
    pub feature_segments: usize,
    pub hard_edges: usize,
}

/// Proxy surface plus curvature and cross field (for inspection; the layout stages come next).
pub struct FieldReport {
    pub proxy: Proxy,
    pub frames: field::Frames,
    pub curvature: field::Curvature,
    pub field: field::CrossField,
    pub info: ProxyInfo,
}

/// Builds the proxy, its curvature and the cross field of a validated, cleaned mesh. `cancelled` is polled by the
/// solver; `Ok(None)` when it said stop.
pub fn make_field(mesh: &Mesh, settings: &Settings, cancelled: &(dyn Fn() -> bool + Sync)) -> Result<Option<FieldReport>, EngineError> {
    let (mut proxy, info) = make_proxy(mesh, settings)?;
    // Wanted quad edge everywhere (adaptive sizes and vertex paint come later).
    proxy.size = vec![info.quad_edge; proxy.p.len()];
    let frames = field::Frames::build(&proxy);
    let curvature = field::compute_curvature(&proxy, &frames, info.quad_edge);
    let Some(cross) = field::solve_cross_field(&proxy, &frames, &curvature, &field::FieldSettings::default(), cancelled) else {
        return Ok(None);
    };
    Ok(Some(FieldReport { proxy, frames, curvature, field: cross, info }))
}

/// Builds the proxy surface of a validated, cleaned mesh as the settings ask (target count, hard edges, materials).
pub fn make_proxy(mesh: &Mesh, settings: &Settings) -> Result<(Proxy, ProxyInfo), EngineError> {
    let work = WorkMesh::from_mesh(mesh, settings.use_material_ids, settings.use_vertex_color_map);
    let area = work.area();
    if area.is_nan() || area <= 0.0 {
        return Err(EngineError::new(ERR_MESH, "The input mesh has no surface area"));
    }
    let h0 = (area / settings.target_quad_count as f64).sqrt() as f32;
    let sharp_cos = if settings.auto_detect_hard_edges { 60f32.to_radians().cos() } else { 2.0 };
    let features = detect_features(&work, settings.use_material_ids, sharp_cos, &mesh.attrs.sharp_edges, h0);
    let info_features = (features.seg_a.len(), features.sharp.len());
    let surface = TriangleBvh::build(work.p.clone(), work.t.clone());
    let segments = (!features.seg_a.is_empty()).then(|| SegmentBvh::build(features.seg_a, features.seg_b));
    let edge = proxy::proxy_edge(area, h0);
    let (_builder, proxy, _vmap) =
        proxy::build_proxy(&work, &surface, segments.as_ref(), features.sharp, settings.use_material_ids, edge, area);
    let info = ProxyInfo {
        source_triangles: work.t.len(),
        area,
        quad_edge: h0,
        proxy_edge: edge,
        feature_segments: info_features.0,
        hard_edges: info_features.1,
    };
    Ok((proxy, info))
}

pub fn run(settings: &Settings, progress: &Progress) -> Result<RunReport, EngineError> {
    init_threads(settings.threads);
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
