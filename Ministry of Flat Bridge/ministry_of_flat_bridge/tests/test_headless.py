# Run via ../../run_tests.sh. Installs the ZIP as an extension into a temporary repo and exercises it against the real
# UnWrapConsole3.exe (through Wine on Linux). Exit code is non-zero if any test fails.
import os
import sys
import time
import traceback

import bpy
import numpy as np

ARGS = sys.argv[sys.argv.index("--") + 1:]
ZIP, EXE = ARGS[0], ARGS[1]
module_name = "bl_ext.user_default.ministry_of_flat_bridge"
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


def raises(fn, contains=""):
    try:
        fn()
    except RuntimeError as e:
        assert contains in str(e), "message {!r} lacks {!r}".format(str(e), contains)
        return
    raise AssertionError("RuntimeError not raised")


def prefs():
    return bpy.context.preferences.addons[module_name].preferences


def clean_scene():
    sc = bpy.context.scene.mof_bridge if hasattr(bpy.context.scene, "mof_bridge") else None
    if sc is not None:  # every test starts from the default settings
        for p in sc.bl_rna.properties:
            if p.identifier not in ("rna_type", "name"):
                sc.property_unset(p.identifier)
    for obj in list(bpy.data.objects):
        bpy.data.objects.remove(obj)
    for me in list(bpy.data.meshes):
        bpy.data.meshes.remove(me)


def select_only(*objs):
    bpy.context.view_layer.update()
    for o in bpy.context.view_layer.objects:
        o.select_set(False)
    for o in objs:
        o.select_set(True)
    bpy.context.view_layer.objects.active = objs[0]


def add(kind, **kw):
    getattr(bpy.ops.mesh, "primitive_" + kind + "_add")(**kw)
    return bpy.context.object


def uv_array(obj, name="MoF"):
    layer = obj.data.uv_layers[name]
    a = np.empty(len(obj.data.loops) * 2, dtype=np.float32)
    layer.uv.foreach_get("vector", a)
    return a.reshape(-1, 2)


def poly_areas(obj, uv):
    out = []
    for p in obj.data.polygons:
        pts = uv[p.loop_start:p.loop_start + p.loop_total]
        x, y = pts[:, 0], pts[:, 1]
        out.append(0.5 * (np.dot(x, np.roll(y, -1)) - np.dot(y, np.roll(x, -1))))
    return np.array(out)


def unwrap(**props):
    for k, v in props.items():
        setattr(bpy.context.scene.mof_bridge, k, v)
    return bpy.ops.mof_bridge.unwrap()


def t_install():
    global mod
    r = bpy.ops.extensions.package_install_files(filepath=ZIP, repo="user_default", enable_on_install=True)
    assert r == {'FINISHED'}, r
    import importlib
    mod = importlib.import_module(module_name)
    assert hasattr(bpy.types.Scene, "mof_bridge")
    prefs().exe_path = EXE


def t_obj_roundtrip():
    from importlib import import_module
    ex = import_module(module_name + ".obj_exchange")
    part = {"verts": np.array([[0, 0, 0], [1, 0, 0], [1, 1, 0], [0, 1, 0], [2, 0, 0]], dtype=float),
            "loop_vertex": np.array([0, 1, 2, 3, 1, 4, 2]), "loop_start": np.array([0, 4]), "loop_total": np.array([4, 3])}
    path = os.path.join(bpy.app.tempdir, "rt.obj")
    ex.write_obj(path, [part, part])
    text = open(path).read().splitlines()
    assert sum(l.startswith("v ") for l in text) == 10
    faces = [l for l in text if l.startswith("f ")]
    assert faces[2] == "f 6 7 8 9" and faces[3] == "f 7 10 8", faces
    uvs, fs = ex.read_faces(path)
    assert len(fs) == 4 and fs[0][0] == [0, 1, 2, 3] and fs[0][1] == [-1] * 4


def t_not_configured():
    prefs().exe_path = "/nonexistent/UnWrapConsole3.exe"
    clean_scene()
    select_only(add("cube"))
    try:
        raises(lambda: bpy.ops.mof_bridge.unwrap(), "not found")
    finally:
        prefs().exe_path = EXE


def t_wine_missing():
    if sys.platform == "win32":
        return
    old = prefs().wine_path
    prefs().wine_path = "/nonexistent/wine"
    try:
        raises(lambda: bpy.ops.mof_bridge.unwrap(), "Wine not found")
    finally:
        prefs().wine_path = old


def t_check_engine():
    assert bpy.ops.mof_bridge.check_engine() == {'FINISHED'}


def t_cube():
    clean_scene()
    cube = add("cube")
    select_only(cube)
    before = [tuple(v.co) for v in cube.data.vertices]
    assert unwrap() == {'FINISHED'}
    uv = uv_array(cube)
    assert len(uv) == 24
    assert uv.min() >= -1e-5 and uv.max() <= 1 + 1e-5, (uv.min(), uv.max())
    assert uv.max() > 0.97, "the layout fills the tile along its longest side"
    areas = poly_areas(cube, uv)
    assert (areas > 1e-4).all() or (areas < -1e-4).all(), areas
    assert cube.data.uv_layers.active.name == "MoF"
    assert [tuple(v.co) for v in cube.data.vertices] == before, "geometry changed"


def t_mixed_faces_and_loose():
    import math
    clean_scene()
    n = 5
    verts = [(math.cos(2 * math.pi * i / n), math.sin(2 * math.pi * i / n), z) for z in (0, 1) for i in range(n)]
    faces = [tuple(range(n - 1, -1, -1)), tuple(range(n, 2 * n))]
    faces += [(i, (i + 1) % n, n + (i + 1) % n, n + i) for i in range(n)]
    base = len(verts)
    verts += [(3, 0, 0), (4, 0, 0), (3, 1, 0), (8, 8, 8), (9, 9, 9)]      # a separate triangle and a loose edge
    faces.append((base, base + 1, base + 2))
    me = bpy.data.meshes.new("mixed")
    me.from_pydata(verts, [(base + 3, base + 4)], faces)
    ob = bpy.data.objects.new("mixed", me)
    bpy.context.scene.collection.objects.link(ob)
    select_only(ob)
    assert unwrap() == {'FINISHED'}
    uv = uv_array(ob)
    assert len(uv) == len(me.loops) == 5 * 2 + 4 * 5 + 3
    assert np.isfinite(uv).all()


def t_raw_output_when_fit_is_off():
    clean_scene()
    c = add("cube")
    select_only(c)
    assert unwrap(fit_to_tiles=False) == {'FINISHED'}
    assert uv_array(c).max() > 2.0, "raw output is the tool's own scale"
    unwrap(fit_to_tiles=True)


def t_timeout_stops_the_tool():
    clean_scene()
    s = add("monkey")
    m = s.modifiers.new("sub", 'SUBSURF')
    m.levels = 3
    bpy.context.view_layer.update()
    me = bpy.data.meshes.new_from_object(s.evaluated_get(bpy.context.evaluated_depsgraph_get()))
    s.modifiers.remove(m)
    s.data = me
    select_only(s)
    prefs().timeout = 1
    try:
        raises(lambda: bpy.ops.mof_bridge.unwrap(), "did not finish in 1 s")
    finally:
        prefs().timeout = 300


def t_suzanne_seams_and_options():
    clean_scene()
    s = add("monkey")
    select_only(s)
    for p in s.data.polygons:
        p.use_smooth = True
    assert unwrap(mark_seams=True, separate_hard_edges=True, use_normals=True, resolution=2048) == {'FINISHED'}
    uv = uv_array(s)
    assert uv.min() >= -1e-4 and uv.max() <= 1 + 1e-4
    seams = sum(e.use_seam for e in s.data.edges)
    assert seams > 5, seams
    # a seam is exactly an edge whose faces disagree: every seam edge borders >= 2 faces here (closed mesh)
    assert 0.2 < np.abs(poly_areas(s, uv)).sum() < 1.0
    unwrap(mark_seams=False, separate_hard_edges=False, use_normals=False, resolution=1024)


def t_shared_uv_name_overwrite():
    clean_scene()
    c = add("cube")
    select_only(c)
    unwrap(uv_map_name="Other")
    first = uv_array(c, "Other").copy()
    unwrap(uv_map_name="Other")
    assert len([l for l in c.data.uv_layers if l.name == "Other"]) == 1
    assert np.allclose(first, uv_array(c, "Other"), atol=1e-5)
    unwrap(uv_map_name="MoF")


def t_combine_packs_in_one_space():
    clean_scene()
    a = add("cube", location=(0, 0, 0))
    b = add("uv_sphere", location=(5, 0, 0), segments=16, ring_count=8)
    select_only(a, b)
    assert unwrap(combine=True) == {'FINISHED'}
    ua, ub = uv_array(a), uv_array(b)
    allu = np.concatenate([ua, ub])
    assert allu.min() >= -1e-4 and allu.max() <= 1 + 1e-4
    # the two objects must not occupy the very same bounding box if they share one space
    box = lambda u: (u.min(0), u.max(0))
    (amin, amax), (bmin, bmax) = box(ua), box(ub)
    assert not (np.allclose(amin, bmin, atol=0.02) and np.allclose(amax, bmax, atol=0.02))
    unwrap(combine=False)


def t_separate_objects_each_fill_space():
    clean_scene()
    a = add("cube")
    b = add("cone", location=(4, 0, 0))
    select_only(a, b)
    assert unwrap(combine=False) == {'FINISHED'}
    for o in (a, b):
        u = uv_array(o)
        assert u.max() > 0.8, "each object is packed on its own into 0-1"


def t_udims_and_worldscale():
    clean_scene()
    s = add("uv_sphere", segments=24, ring_count=12)
    select_only(s)
    assert unwrap(udims=2) == {'FINISHED'}
    assert uv_array(s)[:, 0].max() <= 2.0 + 1e-4
    unwrap(udims=1)
    s.scale = (3, 3, 3)
    bpy.context.view_layer.update()
    assert unwrap(world_scale=True, density=1024.0, resolution=1024) == {'FINISHED'}
    assert np.isfinite(uv_array(s)).all()
    assert uv_array(s).max() > 1.0, "world scale is not normalized to 0-1"
    unwrap(world_scale=False)


def t_denser_mesh_speed():
    clean_scene()
    s = add("monkey")
    m = s.modifiers.new("sub", 'SUBSURF')
    m.levels = 3
    m.render_levels = 3
    bpy.context.view_layer.update()
    ev = s.evaluated_get(bpy.context.evaluated_depsgraph_get())
    me = bpy.data.meshes.new_from_object(ev)
    s.modifiers.remove(m)
    s.data = me
    select_only(s)
    t0 = time.time()
    assert unwrap() == {'FINISHED'}
    print("   {} faces in {:.1f} s".format(len(me.polygons), time.time() - t0))
    assert len(uv_array(s)) == len(me.loops)


def t_engine_failure_is_reported():
    clean_scene()
    c = add("cube")
    select_only(c)
    # a fake tool that exits without writing anything
    fake = os.path.join(bpy.app.tempdir, "UnWrapConsole3.exe")
    open(fake, "w").write("not an exe")
    prefs().exe_path = fake
    try:
        raises(lambda: bpy.ops.mof_bridge.unwrap(), "")
    finally:
        prefs().exe_path = EXE


def t_cancel_stops_the_tool():
    from importlib import import_module
    ops = import_module(module_name + ".operators")
    clean_scene()
    s = add("monkey")
    m = s.modifiers.new("sub", 'SUBSURF')
    m.levels = 3
    bpy.context.view_layer.update()
    me = bpy.data.meshes.new_from_object(s.evaluated_get(bpy.context.evaluated_depsgraph_get()))
    s.modifiers.remove(m)
    s.data = me
    select_only(s)
    task = ops.start_group(bpy.context, [s])
    d = task.job.dir
    assert os.path.isdir(d)
    task.job.cancel()
    assert task.job.proc.poll() is not None and not os.path.isdir(d)


def t_coverage_function():
    from importlib import import_module
    ex = import_module(module_name + ".obj_exchange")
    quad = lambda x0, y0, x1, y1: np.array([(x0, y0), (x1, y0), (x1, y1), (x0, y1)], float)
    st, tt = np.array([0]), np.array([4])
    f = lambda *uvs: ex.coverage([(u, st, tt) for u in uvs], 256)[0]
    assert abs(f(quad(0, 0, 1, 1)) - 1.0) < 0.01
    assert abs(f(quad(0, 0, .5, 1)) - 0.5) < 0.01
    assert abs(f(quad(0, 0, .5, .5), quad(0, 0, .5, .5)) - 0.25) < 0.01, "overlapped copies count once"
    assert abs(f(quad(0, 0, .5, .5), quad(.5, .5, 1, 1)) - 0.5) < 0.01
    frac, tx, ty, ntx, nty = ex.coverage([(quad(1, 1, 2, 2), st, tt)], 256)
    assert (tx, ty, ntx, nty) == (1, 1, 1, 1) and abs(frac - 1) < 0.01
    frac, tx, ty, ntx, nty = ex.coverage([(quad(0, 0, 2, 1), st, tt)], 256)
    assert ntx == 2 and abs(frac - 1) < 0.01


def two_planes():
    clean_scene()
    me = bpy.data.meshes.new("planes")
    me.from_pydata([(0, 0, 0), (1, 0, 0), (1, 1, 0), (0, 1, 0), (3, 0, 0), (7, 0, 0), (7, 4, 0), (3, 4, 0)], [], [(0, 1, 2, 3), (4, 5, 6, 7)])
    ob = bpy.data.objects.new("planes", me)
    bpy.context.scene.collection.objects.link(ob)
    select_only(ob)
    return ob


def island_boxes(ob):
    uv = uv_array(ob)
    return uv[:4], uv[4:]


def t_pack_gap_in_pixels_and_usage():
    ob = two_planes()
    sc = bpy.context.scene.mof_bridge
    assert unwrap(pack_after=True, pack_gap_px=16, pack_rotation='NONE', resolution=1024, pack_tile=1001) == {'FINISHED'}
    a, b = island_boxes(ob)
    gx = max(b[:, 0].min() - a[:, 0].max(), a[:, 0].min() - b[:, 0].max()) * 1024
    gy = max(b[:, 1].min() - a[:, 1].max(), a[:, 1].min() - b[:, 1].max()) * 1024
    gap = max(gx, gy)
    assert abs(gap - 16) < 1.5, (gx, gy)
    uv = uv_array(ob)
    assert abs(uv.min() * 1024 - 8) < 1.5, "distance to the border is half the gap"
    assert "%" in sc.last_report and "gap 16 px @ 1024" in sc.last_report and "tile 1001" in sc.last_report, sc.last_report
    pct = float(sc.last_report.split("%")[0])
    # two squares: 1x1 and 4x4 world units packed in one tile -> a bit over half the tile at best
    assert 30 < pct < 100, pct
    areas = np.abs(poly_areas(ob, uv))
    print("   area ratio of the 4x4 and 1x1 planes after packing: {:.2f}".format(areas[1] / areas[0]))
    unwrap(pack_after=False, pack_gap_px=4)


def t_pack_without_rotation_keeps_axes_aligned():
    ob = two_planes()
    assert unwrap(pack_after=True, pack_rotation='NONE') == {'FINISHED'}
    uv = uv_array(ob)
    for isl in (uv[:4], uv[4:]):
        assert np.allclose(isl[0, 1], isl[1, 1], atol=1e-5) or np.allclose(isl[0, 0], isl[1, 0], atol=1e-5)
    for r in ('CARDINAL', 'ANY'):
        assert unwrap(pack_after=True, pack_rotation=r) == {'FINISHED'}
    unwrap(pack_after=False, pack_rotation='NONE')


def t_pack_target_tile_and_restores_state():
    ob = two_planes()
    other = add("cube", location=(10, 0, 0))
    select_only(ob)
    other.select_set(False)
    assert unwrap(pack_after=True, pack_tile=1012) == {'FINISHED'}
    uv = uv_array(ob)
    assert uv.min() >= 1 and uv.max() <= 2, (uv.min(), uv.max())
    assert "tile 1012" in bpy.context.scene.mof_bridge.last_report, bpy.context.scene.mof_bridge.last_report
    assert bpy.context.mode == 'OBJECT'
    assert bpy.context.view_layer.objects.active == ob and ob.select_get() and not other.select_get()
    assert "MoF" not in other.data.uv_layers, "unselected objects are not touched"
    unwrap(pack_after=False, pack_tile=1001)


def t_pack_combined_objects_share_one_tile():
    clean_scene()
    a = add("cube", location=(0, 0, 0))
    b = add("uv_sphere", location=(5, 0, 0), segments=16, ring_count=8)
    select_only(a, b)
    assert unwrap(combine=True, pack_after=True, pack_gap_px=8) == {'FINISHED'}
    allu = np.concatenate([uv_array(a), uv_array(b)])
    assert allu.min() >= 0 and allu.max() <= 1 + 1e-5
    assert bpy.context.scene.mof_bridge.last_report.count("tile 1001") == 1
    unwrap(combine=False, pack_after=False)


def t_usage_is_reported_without_packing():
    clean_scene()
    c = add("cube")
    select_only(c)
    assert unwrap() == {'FINISHED'}
    r = bpy.context.scene.mof_bridge.last_report
    assert "% of tile 1001 used" in r and "gap" not in r, r


def t_disable_enable():
    bpy.ops.preferences.addon_disable(module=module_name)
    assert not hasattr(bpy.types.Scene, "mof_bridge")
    bpy.ops.preferences.addon_enable(module=module_name)
    assert hasattr(bpy.types.Scene, "mof_bridge")


for name, fn in [
    ("install", t_install),
    ("obj_roundtrip", t_obj_roundtrip),
    ("not_configured", t_not_configured),
    ("wine_missing", t_wine_missing),
    ("check_engine", t_check_engine),
    ("cube", t_cube),
    ("mixed_faces_and_loose", t_mixed_faces_and_loose),
    ("raw_output_when_fit_is_off", t_raw_output_when_fit_is_off),
    ("timeout_stops_the_tool", t_timeout_stops_the_tool),
    ("suzanne_seams_and_options", t_suzanne_seams_and_options),
    ("uv_name_overwrite", t_shared_uv_name_overwrite),
    ("combine_packs_in_one_space", t_combine_packs_in_one_space),
    ("separate_objects_each_fill_space", t_separate_objects_each_fill_space),
    ("udims_and_worldscale", t_udims_and_worldscale),
    ("denser_mesh_speed", t_denser_mesh_speed),
    ("engine_failure_is_reported", t_engine_failure_is_reported),
    ("cancel_stops_the_tool", t_cancel_stops_the_tool),
    ("coverage_function", t_coverage_function),
    ("pack_gap_in_pixels_and_usage", t_pack_gap_in_pixels_and_usage),
    ("pack_without_rotation_keeps_axes_aligned", t_pack_without_rotation_keeps_axes_aligned),
    ("pack_target_tile_and_restores_state", t_pack_target_tile_and_restores_state),
    ("pack_combined_objects_share_one_tile", t_pack_combined_objects_share_one_tile),
    ("usage_is_reported_without_packing", t_usage_is_reported_without_packing),
    ("disable_enable", t_disable_enable),
]:
    check(name, fn)

failed = [n for n, s in results if s == "FAIL"]
print("\n{} passed, {} failed".format(sum(s == "PASS" for _, s in results), len(failed)))
sys.exit(1 if failed else 0)
