"""Manifest entities built from Blender objects (FORMAT.md §4)."""

import secrets

import bpy
from mathutils import Vector

from . import coords, godot_project

ENTITY_ROLES = ('PLACEHOLDER', 'MARKER', 'COLLIDER', 'AREA', 'PATH')
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
                changes.append((obj.name, "uid duplicado de {} (copia): se asigna uno nuevo".format(keeper.name)))
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
            raise ValueError("apunta a {}, que no se exporta como entidad".format(value.name))
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
    raise ValueError("tipo de propiedad no soportado: {}".format(type(value).__name__))


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


def shape_fields(obj, depsgraph):
    """Shape dict (in Godot units, scale baked) and the shape's world matrix."""
    lo, hi = local_bounds(obj)
    scale = world_scale(obj)
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
    center_world = obj.matrix_world @ center_local
    m = coords.without_scale(obj.matrix_world)
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

def build(obj, role, depsgraph, project_root, uid_of):
    g = obj.gbridge
    entity = {"uid": g.uid, "type": role.lower(), "name": obj.name}
    if role == 'PLACEHOLDER':
        entity["scene"] = g.scene
        entity["props"] = props(obj, uid_of)
        entity["xform"] = coords.matrix(obj.matrix_world)
    elif role == 'MARKER':
        entity["props"] = props(obj, uid_of)
        entity["xform"] = coords.matrix(coords.without_scale(obj.matrix_world))
    elif role in SHAPED_ROLES:
        fields, m = shape_fields(obj, depsgraph)
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
        entity["splines"] = splines(obj)
        entity["props"] = props(obj, uid_of)
        entity["xform"] = coords.matrix(coords.without_scale(obj.matrix_world))
    return entity


def splines(obj):
    """Curve splines in Godot space, scale baked; Bezier handles relative to their point."""
    scale = world_scale(obj)

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
