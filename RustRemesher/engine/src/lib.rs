// SPDX-License-Identifier: GPL-3.0-or-later
//! RustRemesher engine: command line, settings, progress, mesh I/O, and the quad remesher (proxy surface, cross field,
//! integer-grid map, extraction, relaxation). `run` validates and cleans the input, remeshes it piece by piece and
//! writes the quads. `make_proxy` / `make_field` (`--proxy`, `--field`) expose the intermediate stages.

// The numeric code indexes several parallel arrays by the same loop variable (as the C++ it is ported from does);
// iterator chains would hide that structure.
#![allow(clippy::needless_range_loop)]

pub mod bvh;
pub mod complex;
pub mod eliminator;
pub mod extract;
pub mod field;
pub mod gridmap;
pub mod io_bin;
pub mod io_obj;
pub mod mesh;
pub mod progress;
pub mod proxy;
pub mod relax;
pub mod remesh;
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
use remesh::{PieceError, PieceOptions};
use vec3::V3;
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
    pub pieces: Vec<PieceReport>,
    pub quality: remesh::QuadQuality,
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

/// What happened to one connected piece of the input.
#[derive(Debug, Clone)]
pub struct PieceReport {
    pub source_triangles: usize,
    pub want_quads: usize,
    pub quads: usize,
    /// Leftover triangles (odd faces without a partner), or all of the piece's triangles when it was kept as it was.
    pub triangles: usize,
    pub singularities: usize,
    pub flipped: usize,
    /// Why the piece could not be remeshed (it is then kept as its original triangles).
    pub error: Option<String>,
}

pub struct RemeshResult {
    pub mesh: Mesh,
    pub pieces: Vec<PieceReport>,
}

/// Quality of a mesh made of quads and triangles (other faces are ignored).
pub fn quality_of_mesh(m: &Mesh) -> remesh::QuadQuality {
    let mut q = remesh::QuadMesh { p: m.positions.iter().map(|&a| V3::from_array(a)).collect(), ..Default::default() };
    for f in m.faces() {
        match f.len() {
            4 => q.quads.push([f[0], f[1], f[2], f[3]]),
            3 => q.tris.push([f[0], f[1], f[2]]),
            _ => {}
        }
    }
    remesh::quad_quality(&q)
}

/// Fewest quads asked of a piece: small ones (eyes, rivets) still get a few dozen.
const MIN_PIECE_QUADS: f64 = 24.0;

/// Remeshes a validated, cleaned mesh into quads, piece by piece (connected components). `progress(value, text)`
/// is called with values in 0..1; returning false cancels.
pub fn remesh_mesh(
    mesh: &Mesh,
    settings: &Settings,
    progress: &(dyn Fn(f32, &str) -> bool + Sync),
) -> Result<Option<RemeshResult>, EngineError> {
    let work = WorkMesh::from_mesh(mesh, settings.use_material_ids, settings.use_vertex_color_map);
    let pieces = remesh::split_components(&work);
    let areas: Vec<f64> = pieces.iter().map(|c| c.mesh.area()).collect();
    let total_area: f64 = areas.iter().sum();
    if total_area.is_nan() || total_area <= 0.0 {
        return Err(EngineError::new(ERR_MESH, "The input mesh has no surface area"));
    }
    let n = pieces.len();
    let mut out = Mesh::default();
    let mut reports = Vec::with_capacity(n);
    for (i, c) in pieces.iter().enumerate() {
        let want = (settings.target_quad_count as f64 * areas[i] / total_area).max(MIN_PIECE_QUADS);
        let opt = PieceOptions {
            want_quads: want,
            keep_groups: settings.use_material_ids,
            detect_edges: settings.auto_detect_hard_edges,
            user_sharp: remesh::sharp_for_component(c, &mesh.attrs.sharp_edges),
            smooth_iterations: 6,
        };
        let label = if n > 1 { format!("Remeshing piece {} of {}", i + 1, n) } else { "Remeshing".to_string() };
        // this piece owns the progress range [lo, hi]; a negative value only asks whether to go on
        let (lo, hi) = (0.25 + 0.65 * i as f32 / n as f32, 0.25 + 0.65 * (i + 1) as f32 / n as f32);
        let report = |f: f32| if f < 0.0 { progress(-1.0, "") } else { progress(lo + (hi - lo) * f, &label) };
        let base = out.positions.len() as u32;
        let mut rep = PieceReport {
            source_triangles: c.mesh.t.len(),
            want_quads: want.round() as usize,
            quads: 0,
            triangles: 0,
            singularities: 0,
            flipped: 0,
            error: None,
        };
        match remesh::remesh_piece(&c.mesh, &opt, &report) {
            Ok(r) => {
                out.positions.extend(r.mesh.p.iter().map(|v| v.to_array()));
                for q in &r.mesh.quads {
                    out.face_sizes.push(4);
                    out.indices.extend(q.iter().map(|v| v + base));
                }
                for t in &r.mesh.tris {
                    out.face_sizes.push(3);
                    out.indices.extend(t.iter().map(|v| v + base));
                }
                rep.quads = r.mesh.quads.len();
                rep.triangles = r.mesh.tris.len();
                rep.singularities = r.singularities;
                rep.flipped = r.flipped;
            }
            Err(PieceError::Cancelled) => return Ok(None),
            Err(PieceError::Failed(why)) => {
                // keep the piece as it was rather than lose it
                out.positions.extend(c.mesh.p.iter().map(|v| v.to_array()));
                for t in &c.mesh.t {
                    out.face_sizes.push(3);
                    out.indices.extend(t.iter().map(|v| v + base));
                }
                rep.triangles = c.mesh.t.len();
                rep.error = Some(why);
            }
        }
        reports.push(rep);
    }
    Ok(Some(RemeshResult { mesh: out, pieces: reports }))
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
    let result = remesh_mesh(&mesh, settings, &|f, text| {
        if f >= 0.0 {
            progress.update(f, text, 0.25);
        }
        true
    })?
    .expect("the engine never cancels itself");
    let output = topology::analyze(&result.mesh);

    progress.update(0.95, "Writing result", 0.2);
    write_mesh(&settings.file_out, &result.mesh)
        .map_err(|e| EngineError::new(ERR_WRITE, format!("Cannot write output: {e}")))?;

    progress.done();
    let quality = quality_of_mesh(&result.mesh);
    Ok(RunReport { input, cleaned, output, pieces: result.pieces, quality })
}
