"""Static geometry → <level>_static.glb through staging copies (the .blend is not modified).

Each exported object gets a temporary copy in an auxiliary scene: its evaluated
mesh (or base mesh), color attributes converted to Face Corner + Byte Color so
Godot reads them as COLOR_0. The glTF exporter only sees that scene.

Objects whose final geometry is identical share one copy, so the GLB stores the
mesh once and Godot gives every node the same Mesh: linked duplicates (Alt+D)
without viewport modifiers reuse it directly; anything else is compared by a
hash of its evaluated geometry (so linked duplicates with modifiers, or separate
objects with the same geometry, are shared too).
"""

import array
import hashlib
import os
import tempfile

import bpy

from . import manifest

STAGING_SCENE = "gbridge_staging"
# foreach_get field, components and array typecode per attribute data type.
_ATTRIBUTE_FIELDS = {
    'FLOAT': ("value", 1, 'f'), 'INT': ("value", 1, 'i'), 'INT8': ("value", 1, 'b'),
    'BOOLEAN': ("value", 1, 'b'), 'FLOAT2': ("vector", 2, 'f'), 'INT32_2D': ("value", 2, 'i'),
    'FLOAT_VECTOR': ("vector", 3, 'f'), 'FLOAT_COLOR': ("color", 4, 'f'),
    'BYTE_COLOR': ("color", 4, 'f'), 'QUATERNION': ("value", 4, 'f'), 'FLOAT4X4': ("value", 16, 'f'),
}
GLTF_OPTIONS = dict(
    export_format='GLB',
    use_active_scene=True,
    export_yup=True,
    export_apply=False,
    export_vertex_color='ACTIVE',
    export_cameras=False,
    export_lights=False,
    export_animations=False,
    export_extras=False,
)


def _policy(obj, scene):
    value = obj.gbridge.modifiers
    return scene.gbridge.modifiers if value == 'SCENE' else value


def _mesh_copy(obj, depsgraph, policy):
    if policy == 'BASE' and obj.type == 'MESH':
        return obj.data.copy()
    return bpy.data.meshes.new_from_object(
        obj.evaluated_get(depsgraph), preserve_all_data_layers=True, depsgraph=depsgraph)


def _share_key(obj, policy):
    """Key for objects that can reuse one staging mesh, or None."""
    if obj.type != 'MESH':
        return None
    if policy == 'BASE' or not any(m.show_viewport for m in obj.modifiers):
        return obj.data
    return None


def geometry_signature(mesh, obj):
    """Hash of everything the GLB stores for this mesh on this object, or None if unknown data."""
    h = hashlib.sha1()

    def add(collection, field, components, typecode):
        values = array.array(typecode, [0]) * (len(collection) * components)
        collection.foreach_get(field, values)
        h.update(values.tobytes())

    add(mesh.loops, "vertex_index", 1, 'i')
    add(mesh.polygons, "loop_total", 1, 'i')
    for attr in sorted(mesh.attributes, key=lambda a: a.name):
        spec = _ATTRIBUTE_FIELDS.get(attr.data_type)
        if spec is None:
            return None
        h.update("{}|{}|{}".format(attr.name, attr.domain, attr.data_type).encode())
        try:
            add(attr.data, *spec)
        except (TypeError, AttributeError, RuntimeError):
            return None
    active = mesh.color_attributes.active_color
    h.update((active.name if active else "").encode())
    materials = [slot.material.name if slot.material else "" for slot in obj.material_slots]
    h.update("|".join(materials).encode())
    return h.hexdigest()


def normalize_colors(mesh):
    """Convert every color attribute to CORNER + BYTE_COLOR, keeping the active one."""
    active = mesh.color_attributes.active_color
    active_name = active.name if active else None
    loop_vertex = None
    for attr in list(mesh.color_attributes):
        if attr.domain == 'CORNER' and attr.data_type == 'BYTE_COLOR':
            continue
        name, domain = attr.name, attr.domain
        values = [0.0] * (len(attr.data) * 4)
        attr.data.foreach_get("color", values)
        if domain == 'POINT':
            if loop_vertex is None:
                loop_vertex = [0] * len(mesh.loops)
                mesh.loops.foreach_get("vertex_index", loop_vertex)
            values = [c for v in loop_vertex for c in values[v * 4:v * 4 + 4]]
        mesh.color_attributes.remove(attr)
        converted = mesh.color_attributes.new(name, 'BYTE_COLOR', 'CORNER')
        converted.data.foreach_set("color", values)
    if mesh.color_attributes:
        target = mesh.color_attributes.get(active_name) if active_name else mesh.color_attributes[0]
        mesh.color_attributes.active_color = target
        mesh.color_attributes.render_color_index = mesh.color_attributes.active_color_index


def export(context, objects, path, level_name=STAGING_SCENE):
    """Write the GLB for `objects` if its content changed. Returns True if written.

    The staging scene is named after the level: Godot prefixes imported mesh names with it.
    """
    scene = context.scene
    window = context.window
    previous_scene = window.scene if window else None
    depsgraph = context.evaluated_depsgraph_get()
    staging = bpy.data.scenes.new(level_name)
    copies, meshes, renamed = [], [], []
    shared = {}       # obj.data -> staging mesh (plain linked duplicates)
    by_signature = {}  # geometry hash -> staging mesh
    try:
        for obj in objects:
            if obj.type not in ('MESH', 'CURVE', 'SURFACE', 'FONT', 'META'):
                continue
            policy = _policy(obj, scene)
            key = _share_key(obj, policy)
            mesh = shared.get(key) if key is not None else None
            if mesh is None:
                mesh = _mesh_copy(obj, depsgraph, policy)
                normalize_colors(mesh)
                signature = geometry_signature(mesh, obj)
                if signature is not None and signature in by_signature:
                    bpy.data.meshes.remove(mesh)
                    mesh = by_signature[signature]
                else:
                    meshes.append(mesh)
                    if signature is not None:
                        by_signature[signature] = mesh
                if key is not None:
                    shared[key] = mesh
            # The copy must carry the original name into the GLB: rename the
            # original for the duration of the export (restored in finally).
            original_name = obj.name
            obj.name = original_name + ".gbridge_tmp"
            renamed.append((obj, original_name))
            copy = bpy.data.objects.new(original_name, mesh)
            copy.matrix_world = obj.matrix_world
            for i, slot in enumerate(obj.material_slots):
                if slot.link == 'OBJECT' and i < len(copy.material_slots):
                    copy.material_slots[i].link = 'OBJECT'
                    copy.material_slots[i].material = slot.material
            staging.collection.objects.link(copy)
            copies.append(copy)

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = os.path.join(tmp, os.path.basename(path))
            with context.temp_override(scene=staging):
                bpy.ops.export_scene.gltf(filepath=tmp_path, **GLTF_OPTIONS)
            with open(tmp_path, "rb") as f:
                data = f.read()
        return manifest.write_bytes_if_changed(path, data)
    finally:
        # The glTF exporter switches the window to the scene it exported.
        if window and previous_scene:
            window.scene = previous_scene
        for copy in copies:
            bpy.data.objects.remove(copy)
        for mesh in meshes:
            bpy.data.meshes.remove(mesh)
        bpy.data.scenes.remove(staging)
        for obj, name in renamed:
            obj.name = name
