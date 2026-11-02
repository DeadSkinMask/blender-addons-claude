"""Checks run before every export. Errors block the export (FORMAT.md §3)."""

import os

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
            issues.append(Issue(INFO, host.name, "instancias como escenas llegan en la fase 4: se exportan como MultiMesh"))
        if policy == 'IGNORE':
            issues.append(Issue(INFO, host.name, "{} instancias ignoradas".format(count)))
        for group in groups:
            if group.type not in GEOMETRY_TYPES:
                issues.append(Issue(INFO, host.name, "instancias de {} ({}) sin geometría: se ignoran".format(
                    group.name, group.type)))
    return issues


def has_errors(issues):
    return any(i.level == ERROR for i in issues)
