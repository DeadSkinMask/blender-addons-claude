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
ASSETS_DIR = os.path.join(PROJECT, "levels", "test", "_kit", "bridge_assets")
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
    raises(p.PathError, lambda: p.to_res(root, os.path.join(WORK, "other")), "outside the Godot project")
    raises(p.PathError, lambda: p.to_res(root, os.path.join(WORK, "proj_sibling")), "outside the Godot project")
    assert p.from_res(root, "res://levels/a.json") == os.path.join(root, "levels", "a.json")


def t_coords():
    from mathutils import Matrix
    c = mod.coords
    assert c.vec((1, 2, 3)) == [1.0, 3.0, -2.0]
    assert bpy.context.scene.gbridge.vertex_colors == 'DATA'
    assert bpy.context.object.gbridge.vertex_colors == 'SCENE'
    assert c.size((1, 2, 3)) == [1.0, 3.0, 2.0]
    m = Matrix.Translation((1, 2, 3))
    assert c.matrix(m) == [1, 0, 0, 0, 1, 0, 0, 0, 1, 1.0, 3.0, -2.0]
    # Blender +Z rotation of 90° is a Godot +Y rotation of 90°: x axis → -z.
    r = c.matrix(Matrix.Rotation(math.radians(90), 4, 'Z'))
    assert close(r[0:3], [0, 0, -1]), r


def t_requires_root_collection():
    raises(mod.paths.PathError, lambda: mod.export.export_level(bpy.context), "root collection")
    # An operator that reports ERROR raises RuntimeError when called from Python.
    raises(RuntimeError, lambda: bpy.ops.gbridge.export_level(), "root collection")


def t_requires_project_root():
    empty = bpy.data.collections.new("Empty")
    bpy.context.scene.collection.children.link(empty)
    bpy.context.scene.gbridge.root = empty
    prefs().godot_project_root = ""
    raises(mod.paths.PathError, lambda: mod.export.export_level(bpy.context), "add-on preferences")
    os.makedirs(PROJECT, exist_ok=True)
    prefs().godot_project_root = PROJECT
    raises(mod.paths.PathError, lambda: mod.export.export_level(bpy.context), "No project.godot")
    with open(os.path.join(PROJECT, "project.godot"), "w") as f:
        f.write(PROJECT_GODOT)
    receiver = mod.paths.from_res(PROJECT, RECEIVER)
    os.makedirs(os.path.dirname(receiver), exist_ok=True)
    open(receiver, "w").close()


def t_requires_saved_blend():
    assert not bpy.data.filepath
    raises(mod.paths.PathError, lambda: mod.export.export_level(bpy.context), "Save the .blend")


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
    raises(mod.paths.PathError, lambda: mod.export.export_level(bpy.context), "outside the Godot project")
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


def _scatter(name, col, source, count, spacing, location):
    """Mesh object whose Geometry Nodes output only instances of `source` along X."""
    tree = bpy.data.node_groups.new(name, 'GeometryNodeTree')
    tree.interface.new_socket("Geometry", in_out='INPUT', socket_type='NodeSocketGeometry')
    tree.interface.new_socket("Geometry", in_out='OUTPUT', socket_type='NodeSocketGeometry')
    nodes, links = tree.nodes, tree.links
    group_input = nodes.new('NodeGroupInput')
    output = nodes.new('NodeGroupOutput')
    join = nodes.new('GeometryNodeJoinGeometry')
    line = nodes.new('GeometryNodeMeshLine')
    line.inputs["Count"].default_value = count
    line.inputs["Offset"].default_value = (spacing, 0, 0)
    info = nodes.new('GeometryNodeObjectInfo')
    info.inputs["Object"].default_value = source
    on_points = nodes.new('GeometryNodeInstanceOnPoints')
    links.new(line.outputs["Mesh"], on_points.inputs["Points"])
    links.new(info.outputs["Geometry"], on_points.inputs["Instance"])
    # The host keeps its own geometry (like a guide path) joined with the instances.
    links.new(group_input.outputs[0], join.inputs[0])
    links.new(on_points.outputs["Instances"], join.inputs[0])
    links.new(join.outputs[0], output.inputs[0])
    import bmesh
    mesh = bpy.data.meshes.new(name)
    bm = bmesh.new()
    bmesh.ops.create_cube(bm, size=0.5)
    bm.to_mesh(mesh)
    bm.free()
    host = bpy.data.objects.new(name, mesh)
    host.location = location
    host.modifiers.new("Scatter", 'NODES').node_group = tree
    col.objects.link(host)
    return host


def _wall_with_torches(name, col, collection, count, spacing, location):
    """Mesh host (a wall) whose Geometry Nodes add `count` instances of `collection`
    along X with Collection Info, keeping the wall's own geometry."""
    tree = bpy.data.node_groups.new(name, 'GeometryNodeTree')
    tree.interface.new_socket("Geometry", in_out='INPUT', socket_type='NodeSocketGeometry')
    tree.interface.new_socket("Geometry", in_out='OUTPUT', socket_type='NodeSocketGeometry')
    nodes, links = tree.nodes, tree.links
    group_input = nodes.new('NodeGroupInput')
    output = nodes.new('NodeGroupOutput')
    join = nodes.new('GeometryNodeJoinGeometry')
    line = nodes.new('GeometryNodeMeshLine')
    line.inputs["Count"].default_value = count
    line.inputs["Offset"].default_value = (spacing, 0, 0)
    info = nodes.new('GeometryNodeCollectionInfo')
    info.inputs["Collection"].default_value = collection
    on_points = nodes.new('GeometryNodeInstanceOnPoints')
    links.new(line.outputs["Mesh"], on_points.inputs["Points"])
    links.new(info.outputs[0], on_points.inputs["Instance"])
    links.new(group_input.outputs[0], join.inputs[0])
    links.new(on_points.outputs["Instances"], join.inputs[0])
    links.new(join.outputs[0], output.inputs[0])
    import bmesh
    mesh = bpy.data.meshes.new(name)
    bm = bmesh.new()
    bmesh.ops.create_cube(bm, size=0.5)
    bm.to_mesh(mesh)
    bm.free()
    host = bpy.data.objects.new(name, mesh)
    host.location = location
    host.modifiers.new("Torches", 'NODES').node_group = tree
    host.gbridge.instances = 'SCENES'
    col.objects.link(host)
    return host


def _instance(name, col, collection, location):
    obj = bpy.data.objects.new(name, None)
    obj.instance_type = 'COLLECTION'
    obj.instance_collection = collection
    obj.location = location
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
    floor.gbridge.vertex_colors = 'ALBEDO'
    stone = bpy.data.materials.new("Piedra")
    floor.data.materials.append(stone)
    # Same material, colors used as data (default): Godot must not touch its material.
    mask = _cube("Mascara", geo, location=(0, 4, 1))
    mask.data.color_attributes.new("Mask", 'BYTE_COLOR', 'CORNER')
    mask.data.materials.append(stone)
    no_colors = _cube("SinColores", geo, location=(2, 4, 1))
    no_colors.data.color_attributes.new("Col", 'BYTE_COLOR', 'CORNER')
    no_colors.gbridge.vertex_colors = 'NONE'

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

    # Geometry Nodes instances → MultiMesh (source outside the root, like a library object).
    crystal = _cube("Cristal", scene.collection, location=(0, 0, -20), scale=(0.2, 0.2, 0.5))
    crystal.scale = (1, 1, 1)
    scatter = _scatter("Scatter", geo, crystal, 3, 15.0, (0, 6, 0))
    _fixed_uid(scatter, "s0000001")

    # Asset (FORMAT.md §4c): a torch modeled 10 m below the origin (instance_offset),
    # outside the root and excluded from the view layer, with a collider and a light.
    library = _collection("Assets", scene.collection)
    torch = _collection("Antorcha", library)
    torch.instance_offset = (0, 0, -10)
    _cube("Palo", torch, location=(0, 0, -9.5), scale=(0.1, 0.1, 0.5))
    stick_col = _cube("PaloCol", torch, location=(0, 0, -9.5), scale=(0.1, 0.1, 0.5))
    stick_col.gbridge.role = 'COLLIDER'
    _fixed_uid(stick_col, "a0000001")
    flame = bpy.data.objects.new("Llama", bpy.data.lights.new("Llama", 'POINT'))
    flame.data.energy = 150.0
    flame.data.color = (1.0, 0.5, 0.25)
    flame.data.use_custom_distance = True
    flame.data.cutoff_distance = 6.0
    flame.location = (0, 0, -8.8)
    flame["omni_attenuation"] = 1.5
    torch.objects.link(flame)
    _fixed_uid(flame, "a0000002")
    bpy.context.view_layer.layer_collection.children["Assets"].exclude = True
    single = _instance("Antorcha1", geo, torch, (-8, 1, 2))
    single["lit"] = True
    _fixed_uid(single, "i0000001")
    wall = _wall_with_torches("MuroAntorchas", geo, torch, 3, 4.0, (0, 8, 0))
    _fixed_uid(wall, "w0000001")

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
        "FarolaA": 'STATIC', "FarolaB": 'STATIC', "FarolaC": 'STATIC',
        "Mascara": 'STATIC', "SinColores": 'STATIC', "Scatter": 'STATIC', "OneWayCol": 'COLLIDER', "PillarCol": 'COLLIDER',
        "Receiver": 'PLACEHOLDER', "Spawn": 'MARKER', "Trigger": 'AREA', "Rail": 'PATH',
        "Antorcha1": 'INSTANCE', "MuroAntorchas": 'STATIC',
    }, roles


def t_export_sample_level():
    result = mod.export.export_level(bpy.context)
    assert result["written"] and result["static_written"], result
    warnings = [(i.object, i.message) for i in result["issues"] if i.level != 'INFO']
    assert warnings == [("Floor", "non-uniform scale not applied"),
                        ("Palo (Antorcha)", "non-uniform scale not applied")], warnings
    data = read_json(MANIFEST)
    assert data["static_scene"] == "room_a_static.glb"
    assert [e["name"] for e in data["entities"]] == \
        ["FloorCol", "OneWayCol", "PillarCol", "Rail", "Receiver", "Spawn", "Trigger", "Antorcha1",
         "MuroAntorchas_Antorcha_0", "MuroAntorchas_Antorcha_1", "MuroAntorchas_Antorcha_2", "Scatter_Cristal"], \
        [e["name"] for e in data["entities"]]


def t_static_glb():
    path = os.path.join(os.path.dirname(MANIFEST), "room_a_static.glb")
    gltf = glb_json(path)
    nodes = {n["name"]: n for n in gltf["nodes"]}
    assert sorted(nodes) == ["FarolaA", "FarolaB", "FarolaC", "Floor", "Mascara", "MuroAntorchas", "PilarA",
                             "PilarB", "SinColores"], list(nodes)
    assert not [n for n in nodes if n.startswith(("Palo", "Llama"))], "asset content leaked into the level"

    def mesh_of(name):
        return gltf["meshes"][nodes[name]["mesh"]]

    assert "COLOR_0" in mesh_of("Floor")["primitives"][0]["attributes"]
    assert mesh_of("Floor").get("extras") == {"gbridge_vertex_colors": "albedo"}, mesh_of("Floor")
    assert "COLOR_0" in mesh_of("Mascara")["primitives"][0]["attributes"]
    assert mesh_of("Mascara").get("extras") == {"gbridge_vertex_colors": "data"}, mesh_of("Mascara")
    assert "COLOR_0" not in mesh_of("SinColores")["primitives"][0]["attributes"]
    assert "extras" not in mesh_of("SinColores")
    # Linked duplicates share one glTF mesh.
    assert nodes["PilarA"]["mesh"] == nodes["PilarB"]["mesh"], nodes
    assert nodes["FarolaA"]["mesh"] == nodes["FarolaB"]["mesh"], "linked duplicates with modifiers"
    assert nodes["FarolaC"]["mesh"] != nodes["FarolaA"]["mesh"], "different modifier result"
    # Without its colors, SinColores is the same cube as the pillars: shared too.
    assert nodes["SinColores"]["mesh"] == nodes["PilarA"]["mesh"]
    assert len(gltf["meshes"]) == 6, len(gltf["meshes"])


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


def t_multimesh_instances():
    data = read_json(MANIFEST)
    mm = by_name(data, "Scatter_Cristal")
    assert mm["type"] == "multimesh" and mm["uid"] == "s0000001:Cristal", mm
    assert mm["mesh"] == "multimesh/room_a_Scatter_Cristal.glb", mm
    # x = 0, 15, 30 with 20 m chunks: chunk 0 has two instances, chunk 1 one.
    assert mm["chunks"] == [
        {"transforms": "multimesh/room_a_Scatter_Cristal_0.gmmi", "count": 2},
        {"transforms": "multimesh/room_a_Scatter_Cristal_1.gmmi", "count": 1},
    ], mm["chunks"]
    folder = os.path.dirname(MANIFEST)
    with open(os.path.join(folder, mm["chunks"][0]["transforms"]), "rb") as f:
        raw = f.read()
    assert raw[:8] == b"GMMI0001" and struct.unpack("<I", raw[8:12])[0] == 2
    first = struct.unpack("<12f", raw[12:60])
    assert close(first, [1, 0, 0, 0, 1, 0, 0, 0, 1, 0, 0, -6]), first  # Blender (0, 6, 0) → Godot (0, 0, -6)
    second = struct.unpack("<12f", raw[60:108])
    assert close(second[9:], [15, 0, -6]), second
    mesh_gltf = glb_json(os.path.join(folder, mm["mesh"]))
    assert len(mesh_gltf["nodes"]) == 1 and mesh_gltf["nodes"][0]["name"].startswith("Cristal"), mesh_gltf["nodes"]
    assert "translation" not in mesh_gltf["nodes"][0], "the MultiMesh mesh must be at the origin"
    # A MultiMesh host only exports its instances, not its own geometry.
    static = glb_json(os.path.join(folder, "room_a_static.glb"))
    assert "Scatter" not in [n["name"] for n in static["nodes"]]


def t_instance_policies():
    scatter = bpy.data.objects["Scatter"]
    folder = os.path.dirname(MANIFEST)
    scatter.gbridge.instances = 'REALIZE'
    try:
        mod_result = mod.export.export_level(bpy.context)
        data = read_json(MANIFEST)
        assert not [e for e in data["entities"] if e["type"] == "multimesh"]
        assert not os.path.exists(os.path.join(folder, "multimesh", "room_a_Scatter_Cristal_0.gmmi")), "stale gmmi"
        static = glb_json(os.path.join(folder, "room_a_static.glb"))
        nodes = {n["name"]: n for n in static["nodes"]}
        realized = [n for n in nodes if n.startswith("Scatter_Cristal_")]
        assert sorted(realized) == ["Scatter_Cristal_0", "Scatter_Cristal_1", "Scatter_Cristal_2"], list(nodes)
        assert "Scatter" in nodes, "with Realize the host keeps its own geometry"
        assert len({nodes[n]["mesh"] for n in realized}) == 1, "realized instances should share one mesh"
        scatter.gbridge.instances = 'IGNORE'
        result = mod.export.export_level(bpy.context)
        assert any(i.object == "Scatter" and "ignored" in i.message for i in result["issues"])
        static = glb_json(os.path.join(folder, "room_a_static.glb"))
        names = [n["name"] for n in static["nodes"]]
        assert "Scatter" in names, "Ignore drops the instances, not the host's own geometry"
        assert not [n for n in names if n.startswith("Scatter_Cristal")]
    finally:
        scatter.gbridge.instances = 'SCENE'
        mod.export.export_level(bpy.context)


def t_assets():
    data = read_json(MANIFEST)
    assert list(data["assets"]) == ["Antorcha"], data["assets"]
    entry = data["assets"]["Antorcha"]
    assert entry["manifest"] == "res://levels/test/_kit/bridge_assets/Antorcha.asset.json", entry
    assert len(entry["hash"]) == 40
    single = by_name(data, "Antorcha1")
    assert single["type"] == "instance" and single["asset"] == "Antorcha" and single["uid"] == "i0000001"
    assert single["props"] == {"lit": True}
    assert single["xform"][9:] == [-8.0, 2.0, -1.0], single["xform"]  # Blender (-8, 1, 2)
    asset = read_json(os.path.join(ASSETS_DIR, "Antorcha.asset.json"))
    assert asset["format"] == "gbridge-level" and asset["level"] == "Antorcha"
    assert asset["static_scene"] == "Antorcha.glb" and asset["assets"] == {}
    assert [e["name"] for e in asset["entities"]] == ["Llama", "PaloCol"], asset["entities"]
    col = by_name(asset, "PaloCol")
    # Modeled at z = -9.5 with instance_offset z = -10 → 0.5 above the asset origin.
    assert col["xform"][9:] == [0.0, 0.5, 0.0] and col["size"] == [0.2, 1.0, 0.2], col
    light = by_name(asset, "Llama")
    assert light["type"] == "light" and light["light"] == "omni", light
    assert light["energy"] == 1.5 and light["range"] == 6.0 and light["color"] == [1.0, 0.5, 0.25], light
    assert light["props"] == {"omni_attenuation": 1.5} and light["xform"][9:] == [0.0, 1.2, 0.0], light
    gltf = glb_json(os.path.join(ASSETS_DIR, "Antorcha.glb"))
    nodes = {n["name"]: n for n in gltf["nodes"]}
    assert sorted(nodes) == ["Palo"], list(nodes)
    assert close(nodes["Palo"]["translation"], [0, 0.5, 0]), nodes["Palo"]
    assert "KHR_lights_punctual" not in gltf.get("extensionsUsed", []), "lights go as entities, not glTF"


def t_geometry_nodes_scenes():
    data = read_json(MANIFEST)
    torches = [e for e in data["entities"] if e["name"].startswith("MuroAntorchas_")]
    assert [e["uid"] for e in torches] == ["w0000001:Antorcha:0", "w0000001:Antorcha:1", "w0000001:Antorcha:2"]
    for i, e in enumerate(torches):
        assert e["type"] == "instance" and e["asset"] == "Antorcha", e
        # Wall at Blender (0, 8, 0), torches every 4 m along X → Godot (4i, 0, -8).
        assert close(e["xform"], [1, 0, 0, 0, 1, 0, 0, 0, 1, 4 * i, 0, -8]), e["xform"]
    assert not [e for e in data["entities"] if e["type"] == "multimesh" and "MuroAntorchas" in e["name"]]


def t_asset_hash_follows_content():
    flame = bpy.data.objects["Llama"]
    before = read_json(MANIFEST)["assets"]["Antorcha"]["hash"]
    flame.data.energy = 300.0
    try:
        result = mod.export.export_level(bpy.context)
        assert any(p.endswith("Antorcha.asset.json") for p in result["assets_written"]), result["assets_written"]
        assert read_json(MANIFEST)["assets"]["Antorcha"]["hash"] != before
    finally:
        flame.data.energy = 150.0
        mod.export.export_level(bpy.context)
    assert read_json(MANIFEST)["assets"]["Antorcha"]["hash"] == before


def t_export_asset_operator():
    exporter = mod.export.export_asset(bpy.context, bpy.data.objects["Antorcha1"])
    assert sorted(exporter.results) == ["Antorcha"] and exporter.written == [], exporter.written
    bpy.context.view_layer.objects.active = bpy.data.objects["Antorcha1"]
    assert bpy.ops.gbridge.export_asset() == {'FINISHED'}
    bpy.context.view_layer.objects.active = bpy.data.objects["Floor"]
    raises(RuntimeError, lambda: bpy.ops.gbridge.export_asset())


def t_instance_in_a_static_collection():
    """A collection instance inside a collection whose default role is Static
    geometry is still an instance (it used to become a MultiMesh host)."""
    geo = bpy.data.collections["Geometria"]
    geo.gbridge.role = 'STATIC'
    torch = bpy.data.collections["Antorcha"]
    torch.gbridge.role = 'STATIC'  # a light inside the asset stays a light
    try:
        roles = dict(mod.scope.collect(bpy.context.scene, bpy.context.view_layer))
        assert roles[bpy.data.objects["Antorcha1"]] == 'INSTANCE', roles[bpy.data.objects["Antorcha1"]]
        asset_roles = dict(mod.scope.collect_collection(torch, in_asset=True))
        assert asset_roles[bpy.data.objects["Llama"]] == 'LIGHT'
        _entries, issues, _root = mod.export.run_validation(bpy.context)
        assert not [i for i in issues if "without geometry" in i.message], issues
    finally:
        geo.gbridge.role = 'NONE'
        torch.gbridge.role = 'NONE'


def t_asset_inside_root_warns():
    torch = bpy.data.collections["Antorcha"]
    root = bpy.context.scene.gbridge.root
    root.children.link(torch)
    try:
        _entries, issues, _root = mod.export.run_validation(bpy.context)
        assert any(i.object == "Antorcha" and "inside the root" in i.message for i in issues), issues
    finally:
        root.children.unlink(torch)


def t_two_hundred_pillars():
    """Acceptance B4: 200 copies of an asset → one GLB and 200 instance entities."""
    scene = bpy.context.scene
    library = bpy.data.collections["Assets"]
    pillar = _collection("Columna", library)
    _cube("Fuste", pillar, location=(0, 0, 1), scale=(0.3, 0.3, 1))
    geo = bpy.data.collections["Geometria"]
    copies = [_instance("Columna{:03d}".format(i), geo, pillar, (i * 2.0, 3, 0)) for i in range(200)]
    try:
        mod.export.export_level(bpy.context)
        data = read_json(MANIFEST)
        instances_ = [e for e in data["entities"] if e.get("asset") == "Columna"]
        assert len(instances_) == 200, len(instances_)
        assert list(data["assets"]) == ["Antorcha", "Columna"]
        glbs = [n for n in os.listdir(ASSETS_DIR) if n.startswith("Columna") and n.endswith(".glb")]
        assert glbs == ["Columna.glb"], glbs
        level = glb_json(os.path.join(os.path.dirname(MANIFEST), "room_a_static.glb"))
        assert not [n for n in level["nodes"] if n["name"].startswith("Fuste")]
    finally:
        for obj in copies:
            bpy.data.objects.remove(obj)
        for obj in list(pillar.objects):
            bpy.data.objects.remove(obj)
        bpy.data.collections.remove(pillar)
        mod.export.export_level(bpy.context)
    assert list(read_json(MANIFEST)["assets"]) == ["Antorcha"]


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
    assert any(i.object == "Spawn2" and "duplicated" in i.message for i in result["issues"])
    bpy.data.objects.remove(dup)
    mod.export.export_level(bpy.context)


def t_validation_errors_block_export():
    receiver = bpy.data.objects["Receiver"]
    before = open(MANIFEST, encoding="utf-8").read()
    receiver.gbridge.scene = "res://missing.tscn"
    raises(mod.export.ExportError, lambda: mod.export.export_level(bpy.context), "errors")
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
    scene.gbridge.assets_dir = os.path.join(SAMPLE_DIR, "assets")
    try:
        mod.export.export_level(bpy.context)
    finally:
        scene.gbridge.level_name = ""
        scene.gbridge.output_dir = "//../generated/"
        scene.gbridge.assets_dir = "//../../_kit/bridge_assets/"
    produced = read_json(os.path.join(SAMPLE_DIR, "sample.level.json"))
    fixture_dir = os.path.join(GODOT_FIXTURES, "sample")
    if UPDATE_FIXTURES and os.path.isdir(GODOT_FIXTURES):
        os.makedirs(fixture_dir, exist_ok=True)
        for name in ("sample.level.json", "sample_static.glb"):
            shutil.copyfile(os.path.join(SAMPLE_DIR, name), os.path.join(fixture_dir, name))
        fixture_mm = os.path.join(fixture_dir, "multimesh")
        if os.path.isdir(fixture_mm):
            for old in os.listdir(fixture_mm):
                if old.endswith((".glb", ".gmmi")):
                    os.remove(os.path.join(fixture_mm, old))
        os.makedirs(fixture_mm, exist_ok=True)
        for name in os.listdir(os.path.join(SAMPLE_DIR, "multimesh")):
            shutil.copyfile(os.path.join(SAMPLE_DIR, "multimesh", name), os.path.join(fixture_mm, name))
        fixture_assets = os.path.join(fixture_dir, "assets")
        os.makedirs(fixture_assets, exist_ok=True)
        for name in os.listdir(os.path.join(SAMPLE_DIR, "assets")):
            shutil.copyfile(os.path.join(SAMPLE_DIR, "assets", name), os.path.join(fixture_assets, name))
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
    ("multimesh_instances", t_multimesh_instances),
    ("instance_policies", t_instance_policies),
    ("assets", t_assets),
    ("geometry_nodes_scenes", t_geometry_nodes_scenes),
    ("asset_hash_follows_content", t_asset_hash_follows_content),
    ("export_asset_operator", t_export_asset_operator),
    ("instance_in_a_static_collection", t_instance_in_a_static_collection),
    ("asset_inside_root_warns", t_asset_inside_root_warns),
    ("reexport_sample_unchanged", t_reexport_sample_unchanged),
    ("duplicate_uid_is_reassigned", t_duplicate_uid_is_reassigned),
    ("validation_errors_block_export", t_validation_errors_block_export),
    ("sample_fixture_for_godot", t_sample_fixture_for_godot),
    ("two_hundred_pillars", t_two_hundred_pillars),
    ("disable_enable", t_disable_enable),
]:
    check(name, fn)

failed = [n for n, s in results if s == "FAIL"]
print("\n{} passed, {} skipped, {} failed".format(
    sum(s == "PASS" for _, s in results), sum(s == "SKIP" for _, s in results), len(failed)))
sys.exit(1 if failed else 0)
