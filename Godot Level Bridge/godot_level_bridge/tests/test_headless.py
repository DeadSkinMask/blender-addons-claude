# Run via ../../run_tests.sh. Installs the ZIP as an extension into a temporary
# repo, then exercises it. Exit code is non-zero if any mandatory test fails.
import json
import math
import os
import shutil
import struct
import sys
import traceback

import bpy

ARGS = sys.argv[sys.argv.index("--") + 1:]
ZIP, WORK = ARGS[0], ARGS[1]
GODOT_FIXTURES = os.environ.get("GODOT_FIXTURES", "")
UPDATE_FIXTURES = os.environ.get("UPDATE_FIXTURES") == "1"
PROJECT = os.path.join(WORK, "godot_project")
ROOM_DIR = os.path.join(PROJECT, "levels", "test", "room_a")
BLEND = os.path.join(ROOM_DIR, "source", "room_a.blend")
MANIFEST = os.path.join(ROOM_DIR, "generated", "room_a.level.json")
RECEIVER = "res://tests/fixtures/scenes/receiver.tscn"
SAMPLE_DIR = os.path.join(PROJECT, "tests", "fixtures", "sample")
PROJECT_GODOT = """config_version=5

[layer_names]

3d_physics/layer_1="world"
3d_physics/layer_2="one_way"
3d_physics/layer_3="player"
3d_physics/layer_12="trigger"
"""

results = []


def record(name, status, detail=""):
    results.append((name, status))
    print("[{}] {} {}".format(status, name, detail))


def check(name, fn):
    try:
        out = fn()
        if out and out[0] == "SKIP":
            record(name, "SKIP", out[1])
        else:
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


def close(a, b, tol=1e-4):
    return all(abs(x - y) <= tol for x, y in zip(a, b)) and len(a) == len(b)


def read_json(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def glb_json(path):
    with open(path, "rb") as f:
        data = f.read()
    length = struct.unpack("<I", data[12:16])[0]
    return json.loads(data[20:20 + length])


print("Blender", bpy.app.version_string, "| Python", sys.version.split()[0])

module_name = "bl_ext.user_default.godot_level_bridge"
mod = None


def prefs():
    return bpy.context.preferences.addons[module_name].preferences


def by_name(manifest, name):
    return next(e for e in manifest["entities"] if e["name"] == name)


# --- B0: configuration and paths -------------------------------------------

def t_install():
    global mod
    r = bpy.ops.extensions.package_install_files(
        filepath=ZIP, repo="user_default", enable_on_install=True)
    assert r == {'FINISHED'}, r
    mod = sys.modules[module_name]
    assert module_name in bpy.context.preferences.addons


def t_properties():
    scene = bpy.context.scene
    assert scene.gbridge.root is None
    assert scene.gbridge.output_dir == "//../generated/"
    assert scene.gbridge.assets_dir == "//../../_kit/bridge_assets/"
    assert scene.gbridge.modifiers == 'APPLY' and scene.gbridge.instances == 'MULTIMESH'
    obj = bpy.context.object  # factory startup cube
    assert obj.gbridge.role == 'AUTO' and obj.gbridge.uid == "" and not obj.gbridge.skip
    assert obj.gbridge.shape == 'BOX' and not any(obj.gbridge.layer)
    col = bpy.data.collections["Collection"]
    assert col.gbridge.role == 'NONE' and not col.gbridge.skip


def t_res_paths():
    p = mod.paths
    root = os.path.join(WORK, "proj")
    assert p.to_res(root, root) == "res://"
    assert p.to_res(root, os.path.join(root, "levels", "a.json")) == "res://levels/a.json"
    raises(p.PathError, lambda: p.to_res(root, os.path.join(WORK, "other")), "fuera del proyecto")
    raises(p.PathError, lambda: p.to_res(root, os.path.join(WORK, "proj_sibling")), "fuera del proyecto")
    assert p.from_res(root, "res://levels/a.json") == os.path.join(root, "levels", "a.json")


def t_coords():
    from mathutils import Matrix
    c = mod.coords
    assert c.vec((1, 2, 3)) == [1.0, 3.0, -2.0]
    assert c.size((1, 2, 3)) == [1.0, 3.0, 2.0]
    m = Matrix.Translation((1, 2, 3))
    assert c.matrix(m) == [1, 0, 0, 0, 1, 0, 0, 0, 1, 1.0, 3.0, -2.0]
    # Blender +Z rotation of 90° is a Godot +Y rotation of 90°: x axis → -z.
    r = c.matrix(Matrix.Rotation(math.radians(90), 4, 'Z'))
    assert close(r[0:3], [0, 0, -1]), r


def t_requires_root_collection():
    raises(mod.paths.PathError, lambda: mod.export.export_level(bpy.context), "colección raíz")
    # An operator that reports ERROR raises RuntimeError when called from Python.
    raises(RuntimeError, lambda: bpy.ops.gbridge.export_level(), "colección raíz")


def t_requires_project_root():
    empty = bpy.data.collections.new("Empty")
    bpy.context.scene.collection.children.link(empty)
    bpy.context.scene.gbridge.root = empty
    prefs().godot_project_root = ""
    raises(mod.paths.PathError, lambda: mod.export.export_level(bpy.context), "preferencias")
    os.makedirs(PROJECT, exist_ok=True)
    prefs().godot_project_root = PROJECT
    raises(mod.paths.PathError, lambda: mod.export.export_level(bpy.context), "No hay project.godot")
    with open(os.path.join(PROJECT, "project.godot"), "w") as f:
        f.write(PROJECT_GODOT)
    receiver = mod.paths.from_res(PROJECT, RECEIVER)
    os.makedirs(os.path.dirname(receiver), exist_ok=True)
    open(receiver, "w").close()


def t_requires_saved_blend():
    assert not bpy.data.filepath
    raises(mod.paths.PathError, lambda: mod.export.export_level(bpy.context), "Guarda el .blend")


def t_export_empty_level():
    os.makedirs(os.path.dirname(BLEND), exist_ok=True)
    bpy.ops.wm.save_as_mainfile(filepath=BLEND)
    assert bpy.ops.gbridge.export_level() == {'FINISHED'}
    assert read_json(MANIFEST) == {
        "format": "gbridge-level", "version": 1, "level": "room_a",
        "static_scene": None, "assets": {}, "entities": [],
    }


def t_reexport_unchanged_does_not_rewrite():
    mtime = os.path.getmtime(MANIFEST)
    result = mod.export.export_level(bpy.context)
    assert result["path"] == MANIFEST and not result["written"]
    assert os.path.getmtime(MANIFEST) == mtime


def t_describe_dir():
    p = mod.paths
    assert p.describe_dir(PROJECT, "//../generated/") == ("res://levels/test/room_a/generated", 'CHECKMARK')
    text, icon = p.describe_dir(PROJECT, "//../not_yet/")
    assert icon == 'INFO' and text.startswith("res://levels/test/room_a/not_yet"), text
    assert p.describe_dir(PROJECT, os.path.join(WORK, "elsewhere"))[1] == 'ERROR'


def t_level_name_override():
    scene = bpy.context.scene
    scene.gbridge.level_name = "Hall Este"
    path = mod.export.export_level(bpy.context)["path"]
    assert os.path.basename(path) == "Hall_Este.level.json", path
    scene.gbridge.level_name = ""


def t_output_outside_project():
    scene = bpy.context.scene
    scene.gbridge.output_dir = os.path.join(WORK, "elsewhere")
    raises(mod.paths.PathError, lambda: mod.export.export_level(bpy.context), "fuera del proyecto")
    scene.gbridge.output_dir = "//../generated/"


def t_matches_godot_empty_fixture():
    fixture = os.path.join(GODOT_FIXTURES, "empty.level.json")
    if not os.path.isfile(fixture):
        return ("SKIP", "no fixture at {}".format(fixture))
    assert mod.manifest.new_manifest("empty") == read_json(fixture)


def t_layer_names():
    names = mod.godot_project.layer_names(PROJECT)
    assert names == {1: "world", 2: "one_way", 3: "player", 12: "trigger"}, names
    assert mod.godot_project.layer_number(PROJECT, "trigger") == 12


# --- B1/B2: sample level ----------------------------------------------------

def _collection(name, parent, role='NONE'):
    col = bpy.data.collections.new(name)
    parent.children.link(col)
    col.gbridge.role = role
    return col


def _cube(name, col, location=(0, 0, 0), scale=(1, 1, 1), rotation=(0, 0, 0)):
    mesh = bpy.data.meshes.new(name)
    import bmesh
    bm = bmesh.new()
    bmesh.ops.create_cube(bm, size=2.0)
    bm.to_mesh(mesh)
    bm.free()
    obj = bpy.data.objects.new(name, mesh)
    obj.location, obj.scale, obj.rotation_euler = location, scale, rotation
    col.objects.link(obj)
    return obj


def _empty(name, col, location=(0, 0, 0), role='AUTO'):
    obj = bpy.data.objects.new(name, None)
    obj.location = location
    obj.gbridge.role = role
    col.objects.link(obj)
    return obj


def _fixed_uid(obj, uid):
    obj.gbridge.uid = uid
    obj.gbridge.uid_owner = obj.name


def t_build_sample_level():
    scene = bpy.context.scene
    root = _collection("Nivel", scene.collection)
    geo = _collection("Geometria", root)
    floor = _cube("Floor", geo, location=(0, 0, -0.5), scale=(10, 2, 0.5))
    colors = floor.data.color_attributes.new("Col", 'FLOAT_COLOR', 'POINT')
    for i, d in enumerate(colors.data):
        d.color = (1.0, 0.25 * (i % 4), 0.0, 1.0)
    floor.data.color_attributes.active_color = colors

    # Linked duplicates (Alt+D): one mesh shared by two objects.
    pillar_a = _cube("PilarA", geo, location=(-4, 2, 1))
    pillar_b = bpy.data.objects.new("PilarB", pillar_a.data)
    pillar_b.location = (4, 2, 1)
    geo.objects.link(pillar_b)
    # Linked duplicates with the same modifier: same evaluated geometry, shared too.
    lamp_a = _cube("FarolaA", geo, location=(-6, 2, 1))
    lamp_a.modifiers.new("Bevel", 'BEVEL')
    lamp_b = lamp_a.copy()
    lamp_b.name = "FarolaB"
    lamp_b.location = (6, 2, 1)
    geo.objects.link(lamp_b)
    # Same mesh but a different modifier result: must not be shared.
    lamp_c = lamp_a.copy()
    lamp_c.name = "FarolaC"
    lamp_c.location = (8, 2, 1)
    lamp_c.modifiers["Bevel"].width = 0.3
    geo.objects.link(lamp_c)

    cols = _collection("Colisiones", root, 'COLLIDER')
    _fixed_uid(_cube("FloorCol", cols, location=(0, 0, -0.25), scale=(5, 1, 0.25)), "c0000001")
    pillar = _cube("PillarCol", cols, location=(3, 0, 1), scale=(0.5, 0.5, 1),
                   rotation=(0, 0, math.radians(90)))
    pillar.gbridge.shape = 'CAPSULE'
    _fixed_uid(pillar, "c0000002")
    one_way = _cube("OneWayCol", cols, location=(-3, 0, 2), scale=(2, 1, 0.1))
    one_way.gbridge.layer[1] = True  # layer 2: one_way
    one_way.gbridge.body_group = "platforms"
    _fixed_uid(one_way, "c0000003")

    ents = _collection("Entidades", root)
    spawn = _empty("Spawn", ents, location=(1, 0, 0.1), role='MARKER')
    spawn["entry_id"] = "default"
    _fixed_uid(spawn, "e0000001")
    receiver = _empty("Receiver", ents, location=(2, 0, 0), role='PLACEHOLDER')
    receiver.gbridge.scene = RECEIVER
    receiver["speed"] = 2.5
    receiver["count"] = 3
    receiver["enabled"] = True
    receiver["label"] = "hola"
    receiver["offset"] = [1.0, 2.0, 3.0]
    receiver["target"] = spawn
    _fixed_uid(receiver, "e0000002")
    trigger = _empty("Trigger", ents, location=(6, 0, 1), role='AREA')
    trigger.empty_display_size = 0.5
    trigger["event"] = "door"
    _fixed_uid(trigger, "e0000003")

    curve = bpy.data.curves.new("Rail", 'CURVE')
    spline = curve.splines.new('BEZIER')
    spline.bezier_points.add(1)
    for bp, co in zip(spline.bezier_points, [(0, 0, 0), (4, 0, 2)]):
        bp.co = co
        bp.handle_left = (co[0] - 1, 0, co[2])
        bp.handle_right = (co[0] + 1, 0, co[2])
    rail = bpy.data.objects.new("Rail", curve)
    rail.location = (-2, 0, 3)
    rail.gbridge.role = 'PATH'
    rail["speed"] = 1.5
    ents.objects.link(rail)
    _fixed_uid(rail, "e0000004")
    receiver["path"] = rail

    # Outside the root, in a skipped collection and in an excluded one: never exported.
    _cube("Helper", scene.collection)
    skipped = _collection("WIP", root)
    skipped.gbridge.skip = True
    _cube("WipCube", skipped)
    excluded = _collection("Excluded", root)
    _cube("ExcludedCube", excluded)
    bpy.context.view_layer.layer_collection.children["Nivel"].children["Excluded"].exclude = True
    hidden = _cube("SkippedCube", geo)
    hidden.gbridge.skip = True

    scene.gbridge.root = root
    entries = mod.scope.collect(scene, bpy.context.view_layer)
    roles = {o.name: r for o, r in entries}
    assert roles == {
        "Floor": 'STATIC', "PilarA": 'STATIC', "PilarB": 'STATIC', "FloorCol": 'COLLIDER',
        "FarolaA": 'STATIC', "FarolaB": 'STATIC', "FarolaC": 'STATIC', "OneWayCol": 'COLLIDER', "PillarCol": 'COLLIDER',
        "Receiver": 'PLACEHOLDER', "Spawn": 'MARKER', "Trigger": 'AREA', "Rail": 'PATH',
    }, roles


def t_export_sample_level():
    result = mod.export.export_level(bpy.context)
    assert result["written"] and result["static_written"], result
    warnings = [(i.object, i.message) for i in result["issues"] if i.level != 'INFO']
    assert warnings == [("Floor", "escala no uniforme sin aplicar")], warnings
    data = read_json(MANIFEST)
    assert data["static_scene"] == "room_a_static.glb"
    assert [e["name"] for e in data["entities"]] == \
        ["FloorCol", "OneWayCol", "PillarCol", "Rail", "Receiver", "Spawn", "Trigger"], data["entities"]


def t_static_glb():
    path = os.path.join(os.path.dirname(MANIFEST), "room_a_static.glb")
    gltf = glb_json(path)
    nodes = {n["name"]: n for n in gltf["nodes"]}
    assert sorted(nodes) == ["FarolaA", "FarolaB", "FarolaC", "Floor", "PilarA", "PilarB"], list(nodes)
    attributes = gltf["meshes"][nodes["Floor"]["mesh"]]["primitives"][0]["attributes"]
    assert "COLOR_0" in attributes, attributes
    # Linked duplicates share one glTF mesh.
    assert nodes["PilarA"]["mesh"] == nodes["PilarB"]["mesh"], nodes
    assert nodes["FarolaA"]["mesh"] == nodes["FarolaB"]["mesh"], "linked duplicates with modifiers"
    assert nodes["FarolaC"]["mesh"] != nodes["FarolaA"]["mesh"], "different modifier result"
    assert len(gltf["meshes"]) == 4, len(gltf["meshes"])


def t_blend_untouched():
    floor = bpy.data.objects["Floor"]
    attr = floor.data.color_attributes["Col"]
    assert (attr.domain, attr.data_type) == ('POINT', 'FLOAT_COLOR')
    assert [s.name for s in bpy.data.scenes] == ["Scene"], "staging scene left behind"
    assert not [o for o in bpy.data.objects if "gbridge_tmp" in o.name]
    assert bpy.context.scene.name == "Scene" and bpy.context.window.scene.name == "Scene"
    assert "Floor" in bpy.data.objects and "Floor.001" not in bpy.data.objects


def t_collider_shapes():
    data = read_json(MANIFEST)
    floor = by_name(data, "FloorCol")
    assert floor["shape"] == "box" and floor["size"] == [10.0, 0.5, 2.0], floor
    assert floor["xform"] == [1, 0, 0, 0, 1, 0, 0, 0, 1, 0.0, -0.25, 0.0], floor["xform"]
    assert floor["layer"] == [1] and floor["mask"] == [] and floor["body"] == "static"
    pillar = by_name(data, "PillarCol")
    assert pillar["shape"] == "capsule" and pillar["radius"] == 0.5 and pillar["height"] == 2.0, pillar
    assert close(pillar["xform"][0:3], [0, 0, -1]), pillar["xform"]  # rotated, scale removed
    assert pillar["xform"][9:] == [3.0, 1.0, 0.0]
    one_way = by_name(data, "OneWayCol")
    assert one_way["layer"] == [2] and one_way["group"] == "platforms"


def t_area_marker_placeholder_path():
    data = read_json(MANIFEST)
    trigger = by_name(data, "Trigger")
    assert trigger["type"] == "area" and trigger["size"] == [1.0, 1.0, 1.0], trigger
    assert trigger["layer"] == [] and trigger["mask"] == [3] and trigger["props"] == {"event": "door"}
    spawn = by_name(data, "Spawn")
    assert spawn["type"] == "marker" and spawn["props"] == {"entry_id": "default"}
    assert spawn["xform"][9:] == [1.0, 0.1, 0.0]
    receiver = by_name(data, "Receiver")
    assert receiver["scene"] == RECEIVER
    assert receiver["props"] == {
        "count": 3, "enabled": True, "label": "hola", "offset": [1.0, 2.0, 3.0],
        "path": {"$ref": "e0000004"}, "speed": 2.5, "target": {"$ref": "e0000001"},
    }, receiver["props"]
    rail = by_name(data, "Rail")
    points = rail["splines"][0]["points"]
    assert rail["splines"][0]["closed"] is False and len(points) == 2
    assert points[1]["p"] == [4.0, 2.0, 0.0] and points[1]["in"] == [-1.0, 0.0, 0.0], points[1]
    assert rail["xform"][9:] == [-2.0, 3.0, 0.0]


def t_reexport_sample_unchanged():
    m_manifest = os.path.getmtime(MANIFEST)
    result = mod.export.export_level(bpy.context)
    assert not result["written"] and not result["static_written"], result
    assert os.path.getmtime(MANIFEST) == m_manifest


def t_duplicate_uid_is_reassigned():
    spawn = bpy.data.objects["Spawn"]
    dup = spawn.copy()
    dup.name = "Spawn2"
    spawn.users_collection[0].objects.link(dup)
    assert dup.gbridge.uid == spawn.gbridge.uid
    result = mod.export.export_level(bpy.context)
    assert spawn.gbridge.uid == "e0000001", spawn.gbridge.uid
    assert dup.gbridge.uid and dup.gbridge.uid != "e0000001"
    assert any(i.object == "Spawn2" and "duplicado" in i.message for i in result["issues"])
    bpy.data.objects.remove(dup)
    mod.export.export_level(bpy.context)


def t_validation_errors_block_export():
    receiver = bpy.data.objects["Receiver"]
    before = open(MANIFEST, encoding="utf-8").read()
    receiver.gbridge.scene = "res://missing.tscn"
    raises(mod.export.ExportError, lambda: mod.export.export_level(bpy.context), "errores")
    assert any(i.level == 'ERROR' and i.object == "Receiver" for i in mod.export.last_issues)
    receiver.gbridge.scene = RECEIVER
    receiver.scale = (1, 2, 1)
    raises(mod.export.ExportError, lambda: mod.export.export_level(bpy.context))
    receiver.scale = (1, 1, 1)
    helper_ref = bpy.data.objects["Helper"]
    receiver["bad"] = helper_ref
    raises(mod.export.ExportError, lambda: mod.export.export_level(bpy.context))
    del receiver["bad"]
    assert open(MANIFEST, encoding="utf-8").read() == before


def t_sample_fixture_for_godot():
    """Export the sample level as 'sample' and compare with / update the Godot fixture."""
    scene = bpy.context.scene
    scene.gbridge.level_name = "sample"
    scene.gbridge.output_dir = SAMPLE_DIR
    try:
        mod.export.export_level(bpy.context)
    finally:
        scene.gbridge.level_name = ""
        scene.gbridge.output_dir = "//../generated/"
    produced = read_json(os.path.join(SAMPLE_DIR, "sample.level.json"))
    fixture_dir = os.path.join(GODOT_FIXTURES, "sample")
    if UPDATE_FIXTURES and os.path.isdir(GODOT_FIXTURES):
        os.makedirs(fixture_dir, exist_ok=True)
        for name in ("sample.level.json", "sample_static.glb"):
            shutil.copyfile(os.path.join(SAMPLE_DIR, name), os.path.join(fixture_dir, name))
        print("   fixtures updated in", fixture_dir)
    fixture = os.path.join(fixture_dir, "sample.level.json")
    if not os.path.isfile(fixture):
        return ("SKIP", "no fixture at {} (run with UPDATE_FIXTURES=1)".format(fixture))
    assert produced == read_json(fixture), "Godot fixture is stale: run with UPDATE_FIXTURES=1"


def t_disable_enable():
    bpy.ops.preferences.addon_disable(module=module_name)
    assert not hasattr(bpy.types.Scene, "gbridge")
    bpy.ops.preferences.addon_enable(module=module_name)
    assert hasattr(bpy.types.Scene, "gbridge")


for name, fn in [
    ("install", t_install),
    ("properties", t_properties),
    ("res_paths", t_res_paths),
    ("coords", t_coords),
    ("requires_root_collection", t_requires_root_collection),
    ("requires_project_root", t_requires_project_root),
    ("requires_saved_blend", t_requires_saved_blend),
    ("export_empty_level", t_export_empty_level),
    ("reexport_unchanged_does_not_rewrite", t_reexport_unchanged_does_not_rewrite),
    ("describe_dir", t_describe_dir),
    ("level_name_override", t_level_name_override),
    ("output_outside_project", t_output_outside_project),
    ("matches_godot_empty_fixture", t_matches_godot_empty_fixture),
    ("layer_names", t_layer_names),
    ("build_sample_level", t_build_sample_level),
    ("export_sample_level", t_export_sample_level),
    ("static_glb", t_static_glb),
    ("blend_untouched", t_blend_untouched),
    ("collider_shapes", t_collider_shapes),
    ("area_marker_placeholder_path", t_area_marker_placeholder_path),
    ("reexport_sample_unchanged", t_reexport_sample_unchanged),
    ("duplicate_uid_is_reassigned", t_duplicate_uid_is_reassigned),
    ("validation_errors_block_export", t_validation_errors_block_export),
    ("sample_fixture_for_godot", t_sample_fixture_for_godot),
    ("disable_enable", t_disable_enable),
]:
    check(name, fn)

failed = [n for n, s in results if s == "FAIL"]
print("\n{} passed, {} skipped, {} failed".format(
    sum(s == "PASS" for _, s in results), sum(s == "SKIP" for _, s in results), len(failed)))
sys.exit(1 if failed else 0)
