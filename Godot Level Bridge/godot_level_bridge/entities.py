"""Manifest entities built from Blender objects (FORMAT.md §4)."""

import math
import secrets

import bpy
from mathutils import Vector

from . import coords, godot_project

ENTITY_ROLES = ('PLACEHOLDER', 'MARKER', 'COLLIDER', 'AREA', 'PATH', 'INSTANCE', 'LIGHT')
# Blender light → Godot energy (100 W → 1.0) and range without Custom Distance.
LIGHT_WATTS_PER_ENERGY = 100.0
DEFAULT_LIGHT_RANGE = 10.0
LIGHT_TYPES = {'POINT': "omni", 'SPOT': "spot"}
SHAPED_ROLES = ('COLLIDER', 'AREA')
# Custom properties that belong to Blender or other add-ons, never entity props.
IGNORED_PROPS = {"cycles", "cycles_visibility", "gbridge"}


# --- uid ---

def new_uid(taken):
    while True:
        uid = secrets.token_hex(4)
        if uid not in taken:
            return uid


def ensure_uids(objects):
    """Give every object a unique uid. Returns [(object name, message)] for what changed.

    Duplicating an object copies its uid; the copy is the one whose name differs
    from the name recorded when the uid was assigned (uid_owner).
    """
    changes = []
    by_uid = {}
    for obj in objects:
        if obj.gbridge.uid:
            by_uid.setdefault(obj.gbridge.uid, []).append(obj)
    taken = set(by_uid)
    for uid, owners in by_uid.items():
        if len(owners) < 2:
            continue
        keeper = next((o for o in owners if o.name == o.gbridge.uid_owner), owners[0])
        for obj in owners:
            if obj is not keeper:
                obj.gbridge.uid = ""
                changes.append((obj.name, "uid duplicated from {} (a copy): a new one is assigned".format(keeper.name)))
    for obj in objects:
        if not obj.gbridge.uid:
            obj.gbridge.uid = new_uid(taken)
            obj.gbridge.uid_owner = obj.name
            taken.add(obj.gbridge.uid)
    return changes


# --- props ---

def prop_items(obj):
    """Entity props: the object's custom properties, minus Blender/add-on ones."""
    for key in sorted(obj.keys()):
        if key.startswith("_") or key in IGNORED_PROPS:
            continue
        yield key, obj[key]


def prop_value(value, uid_of):
    """JSON value for a custom property; object pointers become {"$ref": uid}."""
    if isinstance(value, bpy.types.Object):
        uid = uid_of(value)
        if uid is None:
            raise ValueError("points to {}, which is not exported as an entity".format(value.name))
        return {"$ref": uid}
    if isinstance(value, bool):
        return value
    if isinstance(value, float):
        return coords.num(value)
    if isinstance(value, (int, str)):
        return value
    if hasattr(value, "to_dict"):
        return {k: prop_value(v, uid_of) for k, v in value.to_dict().items()}
    if hasattr(value, "to_list"):
        return [prop_value(v, uid_of) for v in value.to_list()]
    if isinstance(value, (list, tuple)):
        return [prop_value(v, uid_of) for v in value]
    raise ValueError("unsupported property type: {}".format(type(value).__name__))


def props(obj, uid_of):
    return {key: prop_value(value, uid_of) for key, value in prop_items(obj)}


# --- shapes ---

def local_bounds(obj):
    """(min, max) of the object in its local space; empties use their display size."""
    if obj.type == 'EMPTY':
        s = obj.empty_display_size
        return Vector((-s, -s, -s)), Vector((s, s, s))
    corners = [Vector(c) for c in obj.bound_box]
    lo = Vector((min(c[i] for c in corners) for i in range(3)))
    hi = Vector((max(c[i] for c in corners) for i in range(3)))
    return lo, hi


def world_scale(obj):
    return obj.matrix_world.to_scale()


def asset_name(collection):
    """Name of the asset made from an instanced collection (FORMAT.md §4c)."""
    return bpy.path.clean_name(collection.name)


def shape_fields(obj, depsgraph, world=None):
    """Shape dict (in Godot units, scale baked) and the shape's world matrix.

    `world` replaces obj.matrix_world (an asset's objects use asset space)."""
    world = obj.matrix_world if world is None else world
    lo, hi = local_bounds(obj)
    scale = world.to_scale()
    center_local = (lo + hi) / 2
    dims = Vector(((hi[i] - lo[i]) * abs(scale[i]) for i in range(3)))
    shape = obj.gbridge.shape
    fields = {"shape": shape.lower()}
    if shape == 'BOX':
        fields["size"] = coords.size(dims)
    elif shape == 'SPHERE':
        fields["radius"] = coords.num(max(dims) / 2)
    elif shape in ('CAPSULE', 'CYLINDER'):
        radius = max(dims[0], dims[1]) / 2
        height = dims[2] if shape == 'CYLINDER' else max(dims[2], 2 * radius)
        fields["radius"] = coords.num(radius)
        fields["height"] = coords.num(height)
    elif shape in ('CONVEX', 'TRIMESH'):
        mesh = obj.evaluated_get(depsgraph).to_mesh()
        try:
            def point(co):
                v = Vector(((co[i] - center_local[i]) * scale[i] for i in range(3)))
                return coords.vec(v)
            if shape == 'CONVEX':
                fields["points"] = [point(v.co) for v in mesh.vertices]
            else:
                mesh.calc_loop_triangles()
                fields["faces"] = [point(mesh.vertices[i].co)
                                   for tri in mesh.loop_triangles for i in tri.vertices]
        finally:
            obj.evaluated_get(depsgraph).to_mesh_clear()
    center_world = world @ center_local
    m = coords.without_scale(world)
    m.translation = center_world
    return fields, m


def layer_list(bits):
    return [i + 1 for i, on in enumerate(bits) if on]


def default_layers(project_root, role):
    """(layer, mask) when the object sets none: colliders → world; areas → detect player."""
    if role == 'COLLIDER':
        world = godot_project.layer_number(project_root, "world")
        return [world or 1], []
    player = godot_project.layer_number(project_root, "player")
    return [], [player] if player else []


# --- entities ---

def build(obj, role, depsgraph, project_root, uid_of, space=None):
    """Manifest entity, or None for an object that cannot be exported (validation
    reports it). `space` maps world matrices to the file's space (asset origin)."""
    g = obj.gbridge
    world = space @ obj.matrix_world if space is not None else obj.matrix_world
    entity = {"uid": g.uid, "type": role.lower(), "name": obj.name}
    if role == 'PLACEHOLDER':
        entity["scene"] = g.scene
        entity["props"] = props(obj, uid_of)
        entity["xform"] = coords.matrix(world)
    elif role == 'INSTANCE':
        if obj.instance_collection is None:
            return None
        entity["asset"] = asset_name(obj.instance_collection)
        entity["props"] = props(obj, uid_of)
        entity["xform"] = coords.matrix(world)
    elif role == 'LIGHT':
        if obj.type != 'LIGHT' or obj.data.type not in LIGHT_TYPES:
            return None
        light = obj.data
        entity["light"] = LIGHT_TYPES[light.type]
        entity["color"] = [coords.num(c) for c in light.color]
        entity["energy"] = coords.num(light.energy / LIGHT_WATTS_PER_ENERGY)
        entity["range"] = coords.num(light.cutoff_distance if light.use_custom_distance else DEFAULT_LIGHT_RANGE)
        entity["shadow"] = bool(light.use_shadow)
        if light.type == 'SPOT':
            entity["spot_angle"] = coords.num(math.degrees(light.spot_size) / 2)
        entity["props"] = props(obj, uid_of)
        entity["xform"] = coords.matrix(coords.without_scale(world))
    elif role == 'MARKER':
        entity["props"] = props(obj, uid_of)
        entity["xform"] = coords.matrix(coords.without_scale(world))
    elif role in SHAPED_ROLES:
        fields, m = shape_fields(obj, depsgraph, world)
        entity.update(fields)
        layer, mask = layer_list(g.layer), layer_list(g.mask)
        if not layer and not mask:
            layer, mask = default_layers(project_root, role)
        entity["layer"] = layer
        entity["mask"] = mask
        if role == 'COLLIDER':
            entity["body"] = g.body.lower()
            entity["group"] = g.body_group
        else:
            entity["scene"] = g.scene
            entity["props"] = props(obj, uid_of)
        entity["xform"] = coords.matrix(m)
    elif role == 'PATH':
        entity["splines"] = splines(obj, world)
        entity["props"] = props(obj, uid_of)
        entity["xform"] = coords.matrix(coords.without_scale(world))
    return entity


def splines(obj, world=None):
    """Curve splines in Godot space, scale baked; Bezier handles relative to their point."""
    scale = (obj.matrix_world if world is None else world).to_scale()

    def scaled(v):
        return coords.vec(Vector((v[0] * scale[0], v[1] * scale[1], v[2] * scale[2])))

    result = []
    for spline in obj.data.splines:
        points = []
        if spline.type == 'BEZIER':
            for bp in spline.bezier_points:
                points.append({
                    "p": scaled(bp.co),
                    "in": scaled(bp.handle_left - bp.co),
                    "out": scaled(bp.handle_right - bp.co),
                    "tilt": coords.num(bp.tilt),
                })
        else:  # POLY / NURBS: control points, no handles
            for p in spline.points:
                points.append({"p": scaled(p.co.xyz), "in": [0.0, 0.0, 0.0],
                               "out": [0.0, 0.0, 0.0], "tilt": coords.num(p.tilt)})
        result.append({"closed": spline.use_cyclic_u, "points": points})
    return result
