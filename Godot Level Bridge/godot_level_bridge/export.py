"""Level export: scope → uids → validation → static GLB → entities → manifest."""

import os

import bpy

from . import entities, instances, manifest, paths, scope, static_export, validate

# Issues of the last validation/export, shown in the "Validación" panel.
last_issues = []


class ExportError(Exception):
    """Validation found errors; the issues are in `last_issues`."""


def get_prefs(context):
    return context.preferences.addons[__package__].preferences


def level_name(scene):
    if scene.gbridge.level_name:
        return bpy.path.clean_name(scene.gbridge.level_name)
    if bpy.data.filepath:
        return bpy.path.clean_name(os.path.splitext(os.path.basename(bpy.data.filepath))[0])
    return bpy.path.clean_name(scene.name)


def _uid_lookup(entries):
    exported = {obj for obj, role in entries if role in entities.ENTITY_ROLES}
    return lambda obj: obj.gbridge.uid if obj in exported and obj.gbridge.uid else None


def run_validation(context):
    """Collect, assign uids and validate. Returns (entries, issues, project_root)."""
    scene = context.scene
    if scene.gbridge.root is None:
        raise paths.PathError("Elige la colección raíz del nivel")
    project_root = paths.project_root_from_prefs(get_prefs(context))
    context.view_layer.update()  # matrix_world must reflect pending transform edits
    entries = scope.collect(scene, context.view_layer)
    found = instances.collect(context.evaluated_depsgraph_get(), scope.by_role(entries, 'STATIC'))
    entity_objs = [obj for obj, role in entries if role in entities.ENTITY_ROLES]
    entity_objs += [host for host in found if instances.policy(host, scene) in ('MULTIMESH', 'SCENES')]
    issues = [validate.Issue(validate.INFO, name, msg) for name, msg in entities.ensure_uids(entity_objs)]
    issues += validate.check(entries, project_root, _uid_lookup(entries))
    issues += validate.check_instances(found, scene)
    last_issues[:] = issues
    return entries, issues, project_root


def export_level(context):
    """Export the scene's level. Returns a dict with the manifest path and what was written.

    Raises paths.PathError for configuration problems and ExportError when the
    validation finds errors.
    """
    scene = context.scene
    entries, issues, project_root = run_validation(context)
    output_dir = paths.resolve_dir(scene.gbridge.output_dir)
    paths.to_res(project_root, output_dir)  # must be inside the project
    if validate.has_errors(issues):
        raise ExportError("{} errores de validación".format(
            sum(i.level == validate.ERROR for i in issues)))

    name = level_name(scene)
    data = manifest.new_manifest(name)
    depsgraph = context.evaluated_depsgraph_get()
    static_objs = scope.by_role(entries, 'STATIC')
    found = instances.collect(depsgraph, static_objs, with_meshes=True)
    try:
        by_policy = {}
        for host, groups in found.items():
            policy = instances.policy(host, scene)
            by_policy.setdefault('MULTIMESH' if policy == 'SCENES' else policy, {})[host] = groups

        # A MultiMesh host only contributes its instances: its own geometry (base or
        # evaluated, e.g. the guide path of a scatter) is not exported.
        multimesh_hosts = set(by_policy.get('MULTIMESH', {}))
        static_objs = [obj for obj in static_objs if obj not in multimesh_hosts]
        static_path = os.path.join(output_dir, manifest.static_filename(name))
        static_written = None
        if static_objs:
            static_written = static_export.export(context, static_objs, static_path, name,
                                                  extra=instances.realized(by_policy.get('REALIZE', {}), scene))
        if static_written is None:
            if os.path.isfile(static_path):
                os.remove(static_path)  # stale output of a previous export
        else:
            data["static_scene"] = manifest.static_filename(name)

        uid_of = _uid_lookup(entries)
        data["entities"] = [
            entities.build(obj, role, depsgraph, project_root, uid_of)
            for obj, role in entries if role in entities.ENTITY_ROLES
        ]
        multimesh_files = set()
        data["entities"] += instances.export_multimesh(
            context, by_policy.get('MULTIMESH', {}), output_dir, name, multimesh_files)
        instances.remove_stale(output_dir, name, multimesh_files)
    finally:
        instances.free_meshes(found)

    path = os.path.join(output_dir, manifest.manifest_filename(name))
    written = manifest.write_if_changed(path, manifest.dumps(data))
    return {
        "path": path,
        "written": written,
        "static_written": bool(static_written),
        "static": len(static_objs),
        "entities": len(data["entities"]),
        "issues": issues,
    }


class GBRIDGE_OT_export_level(bpy.types.Operator):
    bl_idname = "gbridge.export_level"
    bl_label = "Exportar nivel"
    bl_description = "Exportar la colección raíz del nivel al proyecto Godot"

    def execute(self, context):
        try:
            result = export_level(context)
        except paths.PathError as e:
            self.report({'ERROR'}, str(e))
            return {'CANCELLED'}
        except ExportError as e:
            self.report({'ERROR'}, "{}: revisa el panel Validación".format(e))
            return {'CANCELLED'}
        changed = result["written"] or result["static_written"]
        self.report({'INFO'}, "Nivel {} ({} mallas estáticas, {} entidades): {}".format(
            "exportado" if changed else "sin cambios",
            result["static"], result["entities"], result["path"]))
        return {'FINISHED'}


class GBRIDGE_OT_validate(bpy.types.Operator):
    bl_idname = "gbridge.validate"
    bl_label = "Validar"
    bl_description = "Revisar el nivel sin exportar (asigna uids a las entidades nuevas)"

    def execute(self, context):
        try:
            _entries, issues, _root = run_validation(context)
        except paths.PathError as e:
            self.report({'ERROR'}, str(e))
            return {'CANCELLED'}
        errors = sum(i.level == validate.ERROR for i in issues)
        self.report({'WARNING' if errors else 'INFO'},
                    "{} errores, {} avisos".format(errors, sum(i.level == validate.WARNING for i in issues)))
        return {'FINISHED'}


class GBRIDGE_OT_select_object(bpy.types.Operator):
    bl_idname = "gbridge.select_object"
    bl_label = "Seleccionar"
    bl_description = "Seleccionar el objeto de este problema"
    bl_options = {'INTERNAL'}

    name: bpy.props.StringProperty()

    def execute(self, context):
        obj = bpy.data.objects.get(self.name)
        if obj is None or obj.name not in context.view_layer.objects:
            return {'CANCELLED'}
        for other in context.selected_objects:
            other.select_set(False)
        obj.select_set(True)
        context.view_layer.objects.active = obj
        return {'FINISHED'}


class GBRIDGE_OT_show_scope(bpy.types.Operator):
    bl_idname = "gbridge.show_scope"
    bl_label = "Mostrar qué se exporta"
    bl_description = "Seleccionar todo lo que entraría en el export"

    def execute(self, context):
        entries = scope.collect(context.scene, context.view_layer)
        for obj in context.selected_objects:
            obj.select_set(False)
        counts = {}
        for obj, role in entries:
            if obj.name in context.view_layer.objects:
                obj.select_set(True)
            counts[role] = counts.get(role, 0) + 1
        summary = ", ".join("{} {}".format(n, role.lower()) for role, n in sorted(counts.items()))
        self.report({'INFO'}, summary or "Nada que exportar")
        return {'FINISHED'}


CLASSES = (GBRIDGE_OT_export_level, GBRIDGE_OT_validate, GBRIDGE_OT_select_object, GBRIDGE_OT_show_scope)


def register():
    for cls in CLASSES:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)
