"""Checks run before every export. Errors block the export (FORMAT.md §3)."""

import os

import bpy

from . import entities, paths

ERROR, WARNING, INFO = 'ERROR', 'WARNING', 'INFO'
GEOMETRY_TYPES = {'MESH', 'CURVE', 'SURFACE', 'FONT', 'META'}
EPS = 1e-4


class Issue:
    __slots__ = ("level", "object", "message")

    def __init__(self, level, obj_name, message):
        self.level, self.object, self.message = level, obj_name, message

    def __repr__(self):
        return "{} {}: {}".format(self.level, self.object, self.message)


def _scale_kind(obj):
    s = entities.world_scale(obj)
    if any(v < 0 for v in s):
        return 'NEGATIVE'
    if max(s) - min(s) > EPS:
        return 'NON_UNIFORM'
    if abs(s[0] - 1) > EPS:
        return 'UNIFORM'
    return None


def _check_scene(obj, project_root, issues, required):
    scene = obj.gbridge.scene
    if not scene:
        if required:
            issues.append(Issue(ERROR, obj.name, "placeholder without a scene (res://….tscn)"))
        return
    if not scene.startswith("res://") or not scene.endswith((".tscn", ".scn")):
        issues.append(Issue(ERROR, obj.name, "scene '{}' is not a res://….tscn path".format(scene)))
        return
    if not os.path.isfile(paths.from_res(project_root, scene)):
        issues.append(Issue(ERROR, obj.name, "{} does not exist in the Godot project".format(scene)))


def _check_props(obj, uid_of, issues):
    for key, value in entities.prop_items(obj):
        try:
            entities.prop_value(value, uid_of)
        except ValueError as e:
            issues.append(Issue(ERROR, obj.name, "property '{}': {}".format(key, e)))


def _check_catalog_props(obj, data, issues):
    """Props that are not @export of the object's catalog scene (or have another type)."""
    from . import catalog
    entry = catalog.entry_for(data, obj.gbridge.scene)
    if entry is None:
        return
    for message in catalog.check_props(obj, entry, entities.prop_items(obj)):
        issues.append(Issue(WARNING, obj.name, message))


def check(entries, project_root, uid_of):
    """[Issue] for the collected (object, role) entries."""
    from . import catalog
    issues = []
    data = catalog.try_load(project_root)
    for obj, role in entries:
        kind = _scale_kind(obj)
        if role == 'STATIC':
            if obj.type not in GEOMETRY_TYPES:
                issues.append(Issue(WARNING, obj.name, "type {} without geometry: ignored".format(obj.type)))
            if kind == 'NEGATIVE':
                issues.append(Issue(WARNING, obj.name, "negative scale: flipped normals"))
            elif kind == 'NON_UNIFORM':
                issues.append(Issue(WARNING, obj.name, "non-uniform scale not applied"))
            if obj.type == 'MESH' and len(obj.data.color_attributes) > 1 \
                    and obj.data.color_attributes.active_color is None:
                issues.append(Issue(WARNING, obj.name, "several color attributes and none active"))
        elif role == 'PLACEHOLDER':
            _check_scene(obj, project_root, issues, required=True)
            _check_catalog_props(obj, data, issues)
            if kind in ('NEGATIVE', 'NON_UNIFORM'):
                issues.append(Issue(ERROR, obj.name, "non-uniform or negative scale on a placeholder"))
            elif kind == 'UNIFORM':
                issues.append(Issue(WARNING, obj.name, "scale ≠ 1: the Godot scene will be scaled"))
            _check_props(obj, uid_of, issues)
        elif role == 'MARKER':
            if kind in ('NEGATIVE', 'NON_UNIFORM'):
                issues.append(Issue(WARNING, obj.name, "the marker's scale is ignored"))
            _check_props(obj, uid_of, issues)
        elif role in entities.SHAPED_ROLES:
            g = obj.gbridge
            if kind == 'NEGATIVE':
                issues.append(Issue(ERROR, obj.name, "negative scale on a collision shape"))
            if g.shape in ('CONVEX', 'TRIMESH') and obj.type != 'MESH':
                issues.append(Issue(ERROR, obj.name, "the {} shape needs a mesh".format(g.shape.lower())))
            lo, hi = entities.local_bounds(obj)
            scale = entities.world_scale(obj)
            dims = [(hi[i] - lo[i]) * abs(scale[i]) for i in range(3)]
            # Round shapes take the largest dimension: warn when the others differ.
            round_dims = dims if g.shape == 'SPHERE' else dims[:2]
            if g.shape in ('SPHERE', 'CAPSULE', 'CYLINDER') and max(round_dims) - min(round_dims) > EPS:
                issues.append(Issue(WARNING, obj.name, "round shape on a non-round object: the largest dimension is used"))
            if g.shape == 'BOX' and min(hi[i] - lo[i] for i in range(3)) < EPS:
                issues.append(Issue(ERROR, obj.name, "box without volume (a dimension is 0)"))
            if role == 'AREA':
                _check_scene(obj, project_root, issues, required=False)
                _check_catalog_props(obj, data, issues)
                layer, mask = entities.layer_list(g.layer), entities.layer_list(g.mask)
                if not layer and not mask and not entities.default_layers(project_root, 'AREA')[1]:
                    issues.append(Issue(ERROR, obj.name, "area without layer or mask"))
                _check_props(obj, uid_of, issues)
        elif role == 'INSTANCE':
            if obj.type != 'EMPTY' or obj.instance_type != 'COLLECTION' or obj.instance_collection is None:
                issues.append(Issue(ERROR, obj.name, "instance without a collection (Object → Instancing → Collection)"))
            elif kind in ('NEGATIVE', 'NON_UNIFORM'):
                issues.append(Issue(WARNING, obj.name, "non-uniform or negative scale: the asset's collisions get distorted"))
            _check_props(obj, uid_of, issues)
        elif role == 'LIGHT':
            if obj.type != 'LIGHT':
                issues.append(Issue(ERROR, obj.name, "the light role needs a light object"))
            elif obj.data.type not in entities.LIGHT_TYPES:
                issues.append(Issue(WARNING, obj.name, "{} light: only point and spot lights are exported".format(obj.data.type.lower())))
            else:
                _check_props(obj, uid_of, issues)
        elif role == 'PATH':
            if obj.type != 'CURVE':
                issues.append(Issue(ERROR, obj.name, "a path needs a curve object"))
            else:
                if any(s.type == 'NURBS' for s in obj.data.splines):
                    issues.append(Issue(INFO, obj.name, "NURBS spline: its control points are exported"))
                _check_props(obj, uid_of, issues)
    return issues


def check_instances(found, scene):
    """Info about Geometry Nodes instances that will not be exported as asked."""
    from . import instances
    issues = []
    for host, groups in found.items():
        policy = instances.policy(host, scene)
        count = sum(len(g.matrices) for g in groups)
        if policy == 'SCENES':
            from . import assets
            candidates = assets.candidate_collections()
            in_assets = [g for g in groups if g.source is not None
                         and assets.asset_of(bpy.data.objects[g.source], candidates) is not None]
            if not in_assets:
                issues.append(Issue(INFO, host.name, "no instance is of an asset collection: exported as MultiMesh"))
            groups = [g for g in groups if g not in in_assets]  # those travel inside the asset scene
        if policy == 'IGNORE':
            issues.append(Issue(INFO, host.name, "{} instances ignored".format(count)))
        for group in groups:
            if group.type not in GEOMETRY_TYPES:
                issues.append(Issue(INFO, host.name, "instances of {} ({}) without geometry: ignored".format(
                    group.name, group.type)))
    return issues


def _asset_collections(entries):
    """Collections instanced by the level's instance empties, nested ones included."""
    from . import scope
    found, stack = [], [obj.instance_collection for obj, role in entries
                        if role == 'INSTANCE' and obj.instance_collection]
    while stack:
        col = stack.pop()
        if col in found:
            continue
        found.append(col)
        stack += [obj.instance_collection for obj, role in scope.collect_collection(col, in_asset=True)
                  if role == 'INSTANCE' and obj.instance_collection]
    return found


def check_assets(entries, scene, view_layer, project_root):
    """Issues of the assets the level instances (their content and placement)."""
    from . import scope
    issues = []
    root = scene.gbridge.root
    layer_colls = scope._layer_collections(view_layer)
    names = {}
    for col in _asset_collections(entries):
        name = entities.asset_name(col)
        if name in names and names[name] is not col:
            issues.append(Issue(ERROR, col.name, "same asset name '{}' as collection {}".format(
                name, names[name].name)))
        names[name] = col
        lc = layer_colls.get(col)
        if root is not None and (col == root or col in root.children_recursive)                 and not (lc and lc.exclude) and not col.gbridge.skip:
            issues.append(Issue(WARNING, col.name, "asset collection inside the root: its content is "
                                "also exported as part of the level (exclude it or move it out)"))
        asset_entries = scope.collect_collection(col, in_asset=True)
        exported = {obj for obj, role in asset_entries if role in entities.ENTITY_ROLES}
        for issue in check(asset_entries, project_root,
                           lambda obj: obj.gbridge.uid or "pending" if obj in exported else None):
            issue.object = "{} ({})".format(issue.object, col.name)
            issues.append(issue)
    return issues


def has_errors(issues):
    return any(i.level == ERROR for i in issues)
