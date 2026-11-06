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
            issues.append(Issue(ERROR, obj.name, "placeholder sin escena (res://….tscn)"))
        return
    if not scene.startswith("res://") or not scene.endswith((".tscn", ".scn")):
        issues.append(Issue(ERROR, obj.name, "escena '{}' no es una ruta res://….tscn".format(scene)))
        return
    if not os.path.isfile(paths.from_res(project_root, scene)):
        issues.append(Issue(ERROR, obj.name, "no existe {} en el proyecto Godot".format(scene)))


def _check_props(obj, uid_of, issues):
    for key, value in entities.prop_items(obj):
        try:
            entities.prop_value(value, uid_of)
        except ValueError as e:
            issues.append(Issue(ERROR, obj.name, "propiedad '{}': {}".format(key, e)))


def check(entries, project_root, uid_of):
    """[Issue] for the collected (object, role) entries."""
    issues = []
    for obj, role in entries:
        kind = _scale_kind(obj)
        if role == 'STATIC':
            if obj.type not in GEOMETRY_TYPES:
                issues.append(Issue(WARNING, obj.name, "tipo {} sin geometría: se ignora".format(obj.type)))
            if kind == 'NEGATIVE':
                issues.append(Issue(WARNING, obj.name, "escala negativa: normales invertidas"))
            elif kind == 'NON_UNIFORM':
                issues.append(Issue(WARNING, obj.name, "escala no uniforme sin aplicar"))
            if obj.type == 'MESH' and len(obj.data.color_attributes) > 1 \
                    and obj.data.color_attributes.active_color is None:
                issues.append(Issue(WARNING, obj.name, "varios atributos de color y ninguno activo"))
        elif role == 'PLACEHOLDER':
            _check_scene(obj, project_root, issues, required=True)
            if kind in ('NEGATIVE', 'NON_UNIFORM'):
                issues.append(Issue(ERROR, obj.name, "escala no uniforme o negativa en un placeholder"))
            elif kind == 'UNIFORM':
                issues.append(Issue(WARNING, obj.name, "escala ≠ 1: la escena de Godot se escalará"))
            _check_props(obj, uid_of, issues)
        elif role == 'MARKER':
            if kind in ('NEGATIVE', 'NON_UNIFORM'):
                issues.append(Issue(WARNING, obj.name, "la escala del marcador se ignora"))
            _check_props(obj, uid_of, issues)
        elif role in entities.SHAPED_ROLES:
            g = obj.gbridge
            if kind == 'NEGATIVE':
                issues.append(Issue(ERROR, obj.name, "escala negativa en una forma de colisión"))
            if g.shape in ('CONVEX', 'TRIMESH') and obj.type != 'MESH':
                issues.append(Issue(ERROR, obj.name, "la forma {} necesita una malla".format(g.shape.lower())))
            lo, hi = entities.local_bounds(obj)
            scale = entities.world_scale(obj)
            dims = [(hi[i] - lo[i]) * abs(scale[i]) for i in range(3)]
            # Round shapes take the largest dimension: warn when the others differ.
            round_dims = dims if g.shape == 'SPHERE' else dims[:2]
            if g.shape in ('SPHERE', 'CAPSULE', 'CYLINDER') and max(round_dims) - min(round_dims) > EPS:
                issues.append(Issue(WARNING, obj.name, "forma redonda sobre un objeto no redondo: se usa la dimensión mayor"))
            if g.shape == 'BOX' and min(hi[i] - lo[i] for i in range(3)) < EPS:
                issues.append(Issue(ERROR, obj.name, "caja sin volumen (alguna dimensión es 0)"))
            if role == 'AREA':
                _check_scene(obj, project_root, issues, required=False)
                layer, mask = entities.layer_list(g.layer), entities.layer_list(g.mask)
                if not layer and not mask and not entities.default_layers(project_root, 'AREA')[1]:
                    issues.append(Issue(ERROR, obj.name, "área sin capa ni máscara"))
                _check_props(obj, uid_of, issues)
        elif role == 'INSTANCE':
            if obj.type != 'EMPTY' or obj.instance_type != 'COLLECTION' or obj.instance_collection is None:
                issues.append(Issue(ERROR, obj.name, "instancia sin colección (Objeto → Instancing → Collection)"))
            elif kind in ('NEGATIVE', 'NON_UNIFORM'):
                issues.append(Issue(WARNING, obj.name, "escala no uniforme o negativa: las colisiones del asset se deforman"))
            _check_props(obj, uid_of, issues)
        elif role == 'LIGHT':
            if obj.type != 'LIGHT':
                issues.append(Issue(ERROR, obj.name, "el rol luz necesita un objeto luz"))
            elif obj.data.type not in entities.LIGHT_TYPES:
                issues.append(Issue(WARNING, obj.name, "luz {}: solo se exportan punto y foco".format(obj.data.type.lower())))
            else:
                _check_props(obj, uid_of, issues)
        elif role == 'PATH':
            if obj.type != 'CURVE':
                issues.append(Issue(ERROR, obj.name, "un path necesita un objeto curva"))
            else:
                if any(s.type == 'NURBS' for s in obj.data.splines):
                    issues.append(Issue(INFO, obj.name, "spline NURBS: se exportan sus puntos de control"))
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
            if all(g.source is None or assets.asset_of(bpy.data.objects[g.source], candidates) is None
                   for g in groups):
                issues.append(Issue(INFO, host.name, "ninguna instancia es de una colección-asset: van como MultiMesh"))
        if policy == 'IGNORE':
            issues.append(Issue(INFO, host.name, "{} instancias ignoradas".format(count)))
        for group in groups:
            if group.type not in GEOMETRY_TYPES:
                issues.append(Issue(INFO, host.name, "instancias de {} ({}) sin geometría: se ignoran".format(
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
            issues.append(Issue(ERROR, col.name, "mismo nombre de asset '{}' que la colección {}".format(
                name, names[name].name)))
        names[name] = col
        lc = layer_colls.get(col)
        if root is not None and (col == root or col in root.children_recursive)                 and not (lc and lc.exclude) and not col.gbridge.skip:
            issues.append(Issue(WARNING, col.name, "colección-asset dentro de la raíz: su contenido "
                                "también se exporta como parte del nivel (exclúyela o muévela fuera)"))
        asset_entries = scope.collect_collection(col, in_asset=True)
        exported = {obj for obj, role in asset_entries if role in entities.ENTITY_ROLES}
        for issue in check(asset_entries, project_root,
                           lambda obj: obj.gbridge.uid or "pendiente" if obj in exported else None):
            issue.object = "{} ({})".format(issue.object, col.name)
            issues.append(issue)
    return issues


def has_errors(issues):
    return any(i.level == ERROR for i in issues)
