# Run via ../../run_tests.sh. Installs the ZIP as an extension into a temporary repo, then exercises it against the
# real engine. Exit code is non-zero if any test fails.
import math
import os
import struct
import sys
import time
import traceback

import bpy
import numpy as np

ARGS = sys.argv[sys.argv.index("--") + 1:]
ZIP, ENGINE = ARGS[0], ARGS[1]
module_name = "bl_ext.user_default.rustremesher"
results = []
mod = None


def record(name, status, detail=""):
    results.append((name, status))
    print("[{}] {} {}".format(status, name, detail))


def check(name, fn):
    try:
        fn()
        record(name, "PASS")
    except Exception:
        record(name, "FAIL")
        traceback.print_exc()


def raises(exc_type, fn, contains=""):
    try:
        fn()
    except exc_type as e:
        assert contains in str(e), "message {!r} lacks {!r}".format(str(e), contains)
        return
    raise AssertionError("{} not raised".format(exc_type.__name__))


def prefs():
    return bpy.context.preferences.addons[module_name].preferences


def clean_scene():
    for obj in list(bpy.data.objects):
        bpy.data.objects.remove(obj)
    for me in list(bpy.data.meshes):
        bpy.data.meshes.remove(me)
    col = bpy.data.collections.get("RR_Guides")
    if col:
        bpy.data.collections.remove(col)
    for c in list(bpy.data.curves):
        bpy.data.curves.remove(c)


def add_sphere(segments=48, rings=24):
    bpy.ops.mesh.primitive_uv_sphere_add(segments=segments, ring_count=rings)
    return bpy.context.object


def settings():
    s = bpy.context.scene.rustremesher
    s.target_quads = 600
    s.adaptivness = 0.0
    s.symmetry = (False, False, False)
    s.use_vertex_colors = False
    s.use_materials = False
    s.use_modifiers = False
    s.hide_original = False
    s.guide_strength = 100.0
    return s


def remesh():
    r = bpy.ops.rustremesher.remesh()
    assert r == {'FINISHED'}, r
    return bpy.context.object


def faces_all_quads(me):
    return all(p.loop_total == 4 for p in me.polygons)


def boundary_edges(me):
    count = np.zeros(len(me.edges), dtype=np.int32)
    ce = np.empty(len(me.loops), dtype=np.int32)
    me.loops.foreach_get("edge_index", ce)
    np.add.at(count, ce, 1)
    return int((count == 1).sum())


def coords(me):
    a = np.empty(len(me.vertices) * 3, dtype=np.float32)
    me.vertices.foreach_get("co", a)
    return a.reshape(-1, 3)


def t_install():
    global mod
    r = bpy.ops.extensions.package_install_files(filepath=ZIP, repo="user_default", enable_on_install=True)
    assert r == {'FINISHED'}, r
    mod = sys.modules[module_name]
    assert module_name in bpy.context.preferences.addons
    prefs().engine_path = ENGINE


def t_exchange_roundtrip():
    ex = mod.exchange
    pos = np.array([[0, 0, 0], [1, 0, 0], [1, 1, 0], [0, 1, 0], [0, 0, 1]], dtype=np.float32)
    data = ex.encode_mesh(pos, [4, 3], [0, 1, 2, 3, 0, 3, 4], colors=np.full((5, 3), 0.5), materials=[2, 7],
                          sharp_edges=[[0, 1]], guides=[(True, 3, [[0, 0, 0], [1, 1, 1], [2, 0, 0]])])
    assert data[:8] == b"RRMI0001"
    d = ex.decode_mesh(data)
    assert np.allclose(d["positions"], pos) and list(d["face_sizes"]) == [4, 3] and list(d["indices"]) == [0, 1, 2, 3, 0, 3, 4]
    assert list(d["materials"]) == [2, 7] and np.allclose(d["colors"], 0.5)
    raises(ValueError, lambda: ex.decode_mesh(b"nonsense-bytes-here-and-more"), "bad magic")
    raises(ValueError, lambda: ex.encode_mesh(pos, [4], [0, 1, 2, 3], materials=[1, 2]), "one material per face")
    # layout: magic, V, F, I, positions, sizes, indices, section count
    assert struct.unpack_from("<III", data, 8) == (5, 2, 7)


def t_properties_and_prefs():
    s = bpy.context.scene.rustremesher
    assert s.target_quads == 5000 and s.adaptivness == 50.0 and s.auto_hard_edges and s.use_sharp_edges
    assert not s.use_materials and not s.use_vertex_colors and tuple(s.symmetry) == (False, False, False)
    assert prefs().threads == 0
    assert bpy.ops.rustremesher.check_engine() == {'FINISHED'}


def t_engine_not_configured():
    clean_scene()
    add_sphere()
    settings()
    prefs().engine_path = "/nonexistent/rustremesher"
    try:
        raises(RuntimeError, lambda: bpy.ops.rustremesher.remesh(), "No se encuentra el engine")
        raises(RuntimeError, lambda: bpy.ops.rustremesher.check_engine(), "No se encuentra el engine")
    finally:
        prefs().engine_path = ENGINE


def t_remesh_sphere_makes_a_new_all_quad_object():
    clean_scene()
    src = add_sphere()
    src.location = (1.0, 2.0, 3.0)
    src.rotation_euler = (0.3, 0.2, 0.1)
    src.scale = (1.5, 1.5, 1.5)
    bpy.context.view_layer.update()
    before = coords(src.data).copy()
    settings()
    new = remesh()
    assert new is not src and new.name == src.name + "_remesh", new.name
    me = new.data
    assert faces_all_quads(me), "not all quads"
    assert abs(len(me.polygons) / 600 - 1) < 0.15, len(me.polygons)
    assert boundary_edges(me) == 0
    assert np.allclose(np.array(new.matrix_world), np.array(src.matrix_world))
    assert np.array_equal(before, coords(src.data)), "the source was modified"
    assert all(p.use_smooth for p in me.polygons)
    assert new.select_get() and not src.select_get()
    # radius preserved (the mesh is in local space, unit sphere)
    r = np.linalg.norm(coords(me), axis=1)
    assert abs(r.mean() - 1.0) < 0.02, r.mean()


def t_hide_original_and_modifiers():
    clean_scene()
    bpy.ops.mesh.primitive_cube_add()
    src = bpy.context.object
    mod_sub = src.modifiers.new("sub", 'SUBSURF')
    mod_sub.levels = 2
    s = settings()
    s.use_modifiers = True
    s.hide_original = True
    s.target_quads = 400
    new = remesh()
    assert src.hide_get()
    extent = np.abs(coords(new.data)).max()
    assert extent < 0.99, "the subdivided cube is rounder than the plain one: {}".format(extent)
    s.use_modifiers = False
    s.hide_original = False


def t_symmetry_x():
    clean_scene()
    add_sphere(64, 32)
    s = settings()
    s.symmetry = (True, False, False)
    new = remesh()
    me = new.data
    assert faces_all_quads(me) and boundary_edges(me) == 0
    pts = coords(me)
    key = lambda p: tuple(np.round(p / 1e-3).astype(int))
    have = {key(p) for p in pts}
    miss = sum(1 for p in pts if key(p * np.array([-1, 1, 1], dtype=np.float32)) not in have)
    assert miss <= len(pts) // 100, "{} of {} vertices have no mirror image".format(miss, len(pts))


def t_materials_follow_faces():
    clean_scene()
    src = add_sphere(64, 32)
    for name in ("top", "bottom"):
        m = bpy.data.materials.new(name)
        src.data.materials.append(m)
    mats = np.array([1 if p.center.z > 0 else 0 for p in src.data.polygons], dtype=np.int32)
    src.data.polygons.foreach_set("material_index", mats)
    s = settings()
    s.use_materials = True
    s.target_quads = 800
    new = remesh()
    me = new.data
    assert [m.name for m in me.materials] == ["top", "bottom"]
    idx = np.empty(len(me.polygons), dtype=np.int32)
    me.polygons.foreach_get("material_index", idx)
    assert set(idx) == {0, 1}
    wrong = sum(1 for p in me.polygons if p.material_index != (1 if p.center.z > 0 else 0))
    assert wrong <= len(idx) // 25, "{} of {} faces have the wrong material".format(wrong, len(idx))
    s.use_materials = False


def t_vertex_colors_set_density():
    clean_scene()
    bpy.ops.mesh.primitive_grid_add(x_subdivisions=60, y_subdivisions=60, size=2.0)
    src = bpy.context.object
    attr = src.data.color_attributes.new("density", 'FLOAT_COLOR', 'POINT')
    pts = coords(src.data)
    col = np.where((pts[:, 0] > 0)[:, None], np.array([1, 1, 1, 1.0]), np.array([0.5, 0.5, 0.5, 1.0])).astype(np.float32)
    attr.data.foreach_set("color", col.ravel())
    s = settings()
    s.use_vertex_colors = True
    s.color_attribute = "density"
    s.target_quads = 1200
    new = remesh()
    c = np.array([tuple(p.center) for p in new.data.polygons])
    bright, grey = int((c[:, 0] > 0.2).sum()), int((c[:, 0] < -0.2).sum())
    assert bright > 2 * grey, "{} bright vs {} grey".format(bright, grey)
    s.color_attribute = "missing"
    raises(RuntimeError, lambda: bpy.ops.rustremesher.remesh(), "Falta el atributo de color")
    s.use_vertex_colors = False


def make_circle_guide(radius, center=(0.0, 0.0), z=0.0, name="guide"):
    data = bpy.data.curves.new(name, 'CURVE')
    data.dimensions = '3D'
    sp = data.splines.new('POLY')
    n = 64
    sp.points.add(n - 1)
    for i, p in enumerate(sp.points):
        a = i / n * math.tau
        p.co = (center[0] + radius * math.cos(a), center[1] + radius * math.sin(a), z, 1.0)
    sp.use_cyclic_u = True
    obj = bpy.data.objects.new(name, data)
    mod.guides.guide_collection(create=True).objects.link(obj)
    return obj


def t_guides_are_collected_in_local_space():
    clean_scene()
    bpy.ops.mesh.primitive_plane_add(size=2.0, location=(5.0, 0.0, 0.0))
    target = bpy.context.object
    target.scale = (2.0, 2.0, 2.0)
    make_circle_guide(1.0, center=(5.0, 0.0))
    bez = bpy.data.curves.new("bez", 'CURVE')
    sp = bez.splines.new('BEZIER')
    sp.bezier_points.add(1)
    for p, x in zip(sp.bezier_points, (4.0, 6.0)):
        p.co = (x, 0.0, 0.0)
        p.handle_left_type = p.handle_right_type = 'VECTOR'
    mod.guides.guide_collection().objects.link(bpy.data.objects.new("bez", bez))
    bpy.context.view_layer.update()
    found = mod.guides.collect_guides(target)
    assert len(found) == 2, len(found)
    closed, stroke, pts = found[0]
    assert closed and stroke == 0 and len(pts) == 64
    # world radius 1 around x=5 is, in the plane's local space (scale 2), radius 0.5 around the origin
    r = np.linalg.norm(pts[:, :2], axis=1)
    assert np.allclose(r, 0.5, atol=1e-4), (r.min(), r.max())
    assert not found[1][0] and abs(found[1][2][0][0] + 0.5) < 1e-4 and abs(found[1][2][-1][0] - 0.5) < 1e-4


def edge_alignment(new, radius):
    me = new.data
    pts = coords(me)
    h = math.sqrt(4.0 / len(me.polygons))
    edge_verts = np.empty(len(me.edges) * 2, dtype=np.int32)
    me.edges.foreach_get("vertices", edge_verts)
    e = edge_verts.reshape(-1, 2)
    a, b = pts[e[:, 0]], pts[e[:, 1]]
    m = (a + b) * 0.5
    near = np.abs(np.hypot(m[:, 0], m[:, 1]) - radius) < 1.5 * h
    d = b[near] - a[near]
    d /= np.maximum(np.linalg.norm(d, axis=1), 1e-9)[:, None]
    rad = m[near][:, :3].copy()
    rad[:, 2] = 0
    rad /= np.maximum(np.linalg.norm(rad, axis=1), 1e-9)[:, None]
    c = np.abs((d * rad).sum(axis=1))
    return float(((c < 0.26) | (c > 0.966)).mean())


def t_guides_steer_the_edge_flow():
    clean_scene()
    bpy.ops.mesh.primitive_grid_add(x_subdivisions=80, y_subdivisions=80, size=2.0)
    s = settings()
    s.target_quads = 1200
    plain = edge_alignment(remesh(), 0.6)
    bpy.context.view_layer.objects.active = [o for o in bpy.data.objects if o.name == "Grid"][0]
    for o in bpy.data.objects:
        o.select_set(o.name == "Grid")
    make_circle_guide(0.6)
    guided = edge_alignment(remesh(), 0.6)
    assert guided > plain + 0.15 and guided > 0.7, "{} -> {}".format(plain, guided)
    bpy.ops.rustremesher.clear_guides()
    assert mod.guides.guide_collection() is None


def edge_use_counts(me):
    count = np.zeros(len(me.edges), dtype=np.int32)
    ce = np.empty(len(me.loops), dtype=np.int32)
    me.loops.foreach_get("edge_index", ce)
    np.add.at(count, ce, 1)
    return count


def components(me):
    parent = list(range(len(me.vertices)))

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x
    ev = np.empty(len(me.edges) * 2, dtype=np.int32)
    me.edges.foreach_get("vertices", ev)
    for a, b in ev.reshape(-1, 2):
        parent[find(a)] = find(b)
    used = set(int(v) for v in np.unique(ev))
    return len({find(v) for v in used})


def t_suzanne_low_poly():
    clean_scene()
    bpy.ops.mesh.primitive_monkey_add()
    src = bpy.context.object
    s = settings()
    s.target_quads = 1500
    new = remesh()
    me = new.data
    assert faces_all_quads(me), "triangles left over"
    assert abs(len(me.polygons) / 1500 - 1) < 0.2, len(me.polygons)
    assert edge_use_counts(me).max() <= 2, "non-manifold edges"
    lo, hi = coords(src.data).min(axis=0), coords(src.data).max(axis=0)
    out = coords(me)
    pad = 0.05 * (hi - lo)
    assert (out.min(axis=0) >= lo - pad).all() and (out.max(axis=0) <= hi + pad).all(), "the result leaves the source's bounding box"


def t_two_pieces_stay_two_pieces():
    clean_scene()
    a = add_sphere(32, 16)
    bpy.ops.mesh.primitive_uv_sphere_add(segments=32, ring_count=16, location=(3.0, 0.0, 0.0), radius=0.5)
    b = bpy.context.object
    bpy.ops.object.select_all(action='DESELECT')
    a.select_set(True)
    b.select_set(True)
    bpy.context.view_layer.objects.active = a
    bpy.ops.object.join()
    assert components(a.data) == 2
    s = settings()
    s.target_quads = 800
    new = remesh()
    assert components(new.data) == 2 and faces_all_quads(new.data) and boundary_edges(new.data) == 0


def t_ngons_and_loose_geometry():
    clean_scene()
    bpy.ops.mesh.primitive_cylinder_add(vertices=32)  # two 32-gon caps
    obj = bpy.context.object
    me = obj.data
    me.vertices.add(2)
    me.vertices[-2].co = (5, 5, 5)
    me.vertices[-1].co = (6, 5, 5)
    me.edges.add(1)
    me.edges[-1].vertices = (len(me.vertices) - 2, len(me.vertices) - 1)
    me.update()
    s = settings()
    s.target_quads = 600
    new = remesh()
    assert faces_all_quads(new.data) and boundary_edges(new.data) == 0
    assert np.abs(coords(new.data)).max() < 2.0, "the loose geometry came along"


def t_cancel_stops_the_engine_and_cleans_up():
    clean_scene()
    src = add_sphere(400, 200)
    s = settings()
    s.target_quads = 60000
    job = mod.operators.start_job(bpy.context, src)
    folder = job.dir
    assert os.path.isdir(folder)
    time.sleep(0.4)
    assert job.poll()[0] == 'running'
    t0 = time.time()
    job.cancel()
    assert time.time() - t0 < 5.0
    assert job.proc.poll() is not None, "the engine is still running"
    assert not os.path.exists(folder), "the temporary folder is still there"


def t_engine_failure_is_reported():
    clean_scene()
    src = add_sphere(8, 4)
    s = settings()
    # a mesh the engine refuses: NaN positions
    me = src.data
    me.vertices[0].co = (float("nan"), 0.0, 0.0)
    raises(RuntimeError, lambda: bpy.ops.rustremesher.remesh(), "El engine terminó con código")
    clean_scene()
    bpy.data.objects.new("empty", bpy.data.meshes.new("empty"))
    obj = bpy.data.objects["empty"]
    bpy.context.collection.objects.link(obj)
    bpy.context.view_layer.objects.active = obj
    raises(RuntimeError, lambda: bpy.ops.rustremesher.remesh(), "no tiene caras")


def t_threads_preference_reaches_the_engine():
    clean_scene()
    src = add_sphere(24, 12)
    settings()
    prefs().threads = 2
    try:
        job = mod.operators.start_job(bpy.context, src)
        text = open(os.path.join(job.dir, "settings.txt")).read()
        job.cancel()
        assert "Threads=2" in text and "TargetQuadCount=600" in text and "SymLocal=1" in text, text
        assert 'FileIn="' in text and "\\" not in text
    finally:
        prefs().threads = 0


def t_disable_enable():
    bpy.ops.preferences.addon_disable(module=module_name)
    assert not hasattr(bpy.types.Scene, "rustremesher")
    bpy.ops.preferences.addon_enable(module=module_name)
    assert hasattr(bpy.types.Scene, "rustremesher")


for name, fn in [
    ("install", t_install),
    ("exchange_roundtrip", t_exchange_roundtrip),
    ("properties_and_prefs", t_properties_and_prefs),
    ("engine_not_configured", t_engine_not_configured),
    ("remesh_sphere_makes_a_new_all_quad_object", t_remesh_sphere_makes_a_new_all_quad_object),
    ("hide_original_and_modifiers", t_hide_original_and_modifiers),
    ("symmetry_x", t_symmetry_x),
    ("materials_follow_faces", t_materials_follow_faces),
    ("vertex_colors_set_density", t_vertex_colors_set_density),
    ("guides_are_collected_in_local_space", t_guides_are_collected_in_local_space),
    ("guides_steer_the_edge_flow", t_guides_steer_the_edge_flow),
    ("suzanne_low_poly", t_suzanne_low_poly),
    ("two_pieces_stay_two_pieces", t_two_pieces_stay_two_pieces),
    ("ngons_and_loose_geometry", t_ngons_and_loose_geometry),
    ("cancel_stops_the_engine_and_cleans_up", t_cancel_stops_the_engine_and_cleans_up),
    ("engine_failure_is_reported", t_engine_failure_is_reported),
    ("threads_preference_reaches_the_engine", t_threads_preference_reaches_the_engine),
    ("disable_enable", t_disable_enable),
]:
    check(name, fn)

failed = [n for n, s in results if s == "FAIL"]
print("\n{} passed, {} failed".format(sum(s == "PASS" for _, s in results), len(failed)))
sys.exit(1 if failed else 0)
