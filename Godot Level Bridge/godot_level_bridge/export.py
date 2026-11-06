"""Level export: scope → uids → validation → static GLB → entities → manifest."""

import os

import bpy

from . import assets, coords, entities, instances, manifest, paths, scope, static_export, validate

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
    issues += validate.check_assets(entries, scene, context.view_layer, project_root)
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
    exporter = None

    def asset_entry(collection):
        nonlocal exporter
        if exporter is None:
            assets_dir = paths.resolve_dir(scene.gbridge.assets_dir)
            paths.to_res(project_root, assets_dir)  # must be inside the project
            exporter = assets.AssetExporter(context, assets_dir, project_root)
        try:
            return exporter.entry(collection)
        except ValueError as e:
            issues.append(validate.Issue(validate.ERROR, collection.name, str(e)))
            raise ExportError("1 error de validación")

    try:
        by_policy = {}
        for host, groups in found.items():
            by_policy.setdefault(instances.policy(host, scene), {})[host] = groups
        # "Scenes": instances of asset collections become instance entities; the
        # host keeps its own geometry and any other instances go as MultiMesh.
        scene_hosts = by_policy.pop('SCENES', {})
        candidates = assets.candidate_collections() if scene_hosts else set()
        scene_items = instances.scene_instances(depsgraph, scene_hosts, candidates)
        for host, groups in scene_hosts.items():
            rest = instances.without_asset_groups(groups, candidates)
            if rest:
                by_policy.setdefault('MULTIMESH_KEEP_HOST', {})[host] = rest

        # A MultiMesh host only contributes its instances: its own geometry (base or
        # evaluated, e.g. the guide path of a scatter) is not exported.
        multimesh_hosts = set(by_policy.get('MULTIMESH', {}))
        by_policy.setdefault('MULTIMESH', {}).update(by_policy.pop('MULTIMESH_KEEP_HOST', {}))
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
        data["entities"] = []
        for obj, role in entries:
            if role not in entities.ENTITY_ROLES:
                continue
            entity = entities.build(obj, role, depsgraph, project_root, uid_of)
            if entity is None:
                continue
            if role == 'INSTANCE':
                data["assets"][entity["asset"]] = asset_entry(obj.instance_collection)
            data["entities"].append(entity)
        for host, items in scene_items.items():
            for i, (collection, matrix) in enumerate(items):
                asset = entities.asset_name(collection)
                data["assets"][asset] = asset_entry(collection)
                data["entities"].append({
                    "uid": "{}:{}:{}".format(host.gbridge.uid, asset, i),
                    "type": "instance",
                    "name": bpy.path.clean_name("{}_{}_{}".format(host.name, asset, i)),
                    "asset": asset,
                    "props": {},
                    "xform": coords.matrix(matrix),
                })
        data["assets"] = dict(sorted(data["assets"].items()))
        if exporter is not None:
            issues += [validate.Issue(validate.INFO, n, m) for n, m in exporter.issues]
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
        "assets": sorted(data["assets"]),
        "assets_written": list(exporter.written) if exporter else [],
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


def export_asset(context, obj):
    """Export only the asset instanced by `obj` (and its nested assets). Returns the exporter."""
    if obj is None or obj.instance_type != 'COLLECTION' or obj.instance_collection is None:
        raise paths.PathError("El objeto activo no instancia una colección")
    project_root = paths.project_root_from_prefs(get_prefs(context))
    assets_dir = paths.resolve_dir(context.scene.gbridge.assets_dir)
    paths.to_res(project_root, assets_dir)
    context.view_layer.update()
    exporter = assets.AssetExporter(context, assets_dir, project_root)
    try:
        exporter.entry(obj.instance_collection)
    except ValueError as e:
        raise paths.PathError(str(e))
    return exporter


class GBRIDGE_OT_export_asset(bpy.types.Operator):
    bl_idname = "gbridge.export_asset"
    bl_label = "Exportar asset"
    bl_description = ("Exportar solo el asset (colección) que instancia el objeto activo; "
                      "en Godot, Rebuild de las salas que lo usan lo actualiza")

    @classmethod
    def poll(cls, context):
        obj = context.object
        return obj is not None and obj.instance_type == 'COLLECTION' and obj.instance_collection is not None

    def execute(self, context):
        try:
            exporter = export_asset(context, context.object)
        except paths.PathError as e:
            self.report({'ERROR'}, str(e))
            return {'CANCELLED'}
        self.report({'INFO'}, "Assets {}: {}".format(
            "exportados" if exporter.written else "sin cambios", ", ".join(sorted(exporter.results))))
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


CLASSES = (GBRIDGE_OT_export_level, GBRIDGE_OT_export_asset, GBRIDGE_OT_validate, GBRIDGE_OT_select_object, GBRIDGE_OT_show_scope)


def register():
    for cls in CLASSES:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)
