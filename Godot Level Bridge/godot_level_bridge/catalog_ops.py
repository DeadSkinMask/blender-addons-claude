"""Catalog operators (place an entity, pick a scene, sync props, reload) and the
viewport overlay that draws each catalog scene's volume around its placeholder."""

import bpy
import gpu
from gpu_extras.batch import batch_for_shader

from . import catalog, paths

# Dynamic enum items must stay referenced while Blender shows them.
_enum_items_ref = []


def _project_root(context):
    try:
        return paths.project_root_from_prefs(context.preferences.addons[__package__].preferences)
    except paths.PathError:
        return None


def _scene_items(_self, context):
    data = catalog.try_load(_project_root(context))
    items = [(e["scene"], catalog.label(e), "{} ({})".format(e["scene"], e["kind"]))
             for e in (data["scenes"] if data else [])]
    _enum_items_ref[:] = items or [("", "(no catalog)", "")]
    return _enum_items_ref


def _require_entry(op, context, scene_path):
    root = _project_root(context)
    if root is None:
        op.report({'ERROR'}, "Set the Godot project folder in the add-on preferences")
        return None
    try:
        data = catalog.load(root)
    except catalog.CatalogError as e:
        op.report({'ERROR'}, str(e))
        return None
    if data is None:
        op.report({'ERROR'}, "No {} in the Godot project: Project → Tools → Level Bridge: Export catalog".format(
            catalog.FILENAME))
        return None
    entry = catalog.entry_for(data, scene_path)
    if entry is None:
        op.report({'ERROR'}, "{} is not in the catalog".format(scene_path))
    return entry


def place(context, entry, collection=None, location=None):
    """Create the empty for a catalog entry (Placeholder or Area) with its default
    props. Returns the object."""
    obj = bpy.data.objects.new(entry["name"], None)
    g = obj.gbridge
    g.scene = entry["scene"]
    if entry.get("kind") == "area":
        g.role = 'AREA'
        obj.empty_display_type = 'CUBE'
        obj.empty_display_size = 0.5  # ±0.5: the area's size is the object's scale
        box = catalog.blender_box(entry)
        if box:
            obj.scale = box[1] - box[0]
    else:
        g.role = 'PLACEHOLDER'
        obj.empty_display_type = 'PLAIN_AXES'
        obj.empty_display_size = 0.3
    obj.location = location if location is not None else context.scene.cursor.location
    (collection or context.collection).objects.link(obj)
    context.view_layer.update()  # so the view layer lists the new object (selection, active)
    catalog.apply_props(obj, entry)
    return obj


class GBRIDGE_OT_place_entity(bpy.types.Operator):
    bl_idname = "gbridge.place_entity"
    bl_label = "Place entity"
    bl_description = ("Add an entity from the Godot scene catalog at the 3D cursor, in the active collection, "
                      "with its role, scene and default props")
    bl_options = {'REGISTER', 'UNDO'}
    bl_property = "scene_item"

    scene_item: bpy.props.EnumProperty(name="Scene", items=_scene_items)

    def invoke(self, context, event):
        context.window_manager.invoke_search_popup(self)
        return {'RUNNING_MODAL'}

    def execute(self, context):
        entry = _require_entry(self, context, self.scene_item)
        if entry is None:
            return {'CANCELLED'}
        for other in context.selected_objects:
            other.select_set(False)
        obj = place(context, entry)
        if obj.name in context.view_layer.objects:
            obj.select_set(True)
            context.view_layer.objects.active = obj
            self.report({'INFO'}, "Placed {} ({})".format(obj.name, catalog.label(entry)))
        else:
            self.report({'WARNING'}, "Placed {} in {}, which is excluded from the view layer".format(
                obj.name, context.collection.name))
        return {'FINISHED'}


class GBRIDGE_OT_pick_scene(bpy.types.Operator):
    bl_idname = "gbridge.pick_scene"
    bl_label = "Pick scene"
    bl_description = "Choose the active object's scene from the Godot catalog (adds its missing props)"
    bl_options = {'REGISTER', 'UNDO'}
    bl_property = "scene_item"

    scene_item: bpy.props.EnumProperty(name="Scene", items=_scene_items)

    @classmethod
    def poll(cls, context):
        return context.object is not None

    def invoke(self, context, event):
        context.window_manager.invoke_search_popup(self)
        return {'RUNNING_MODAL'}

    def execute(self, context):
        entry = _require_entry(self, context, self.scene_item)
        if entry is None:
            return {'CANCELLED'}
        obj = context.object
        obj.gbridge.scene = entry["scene"]
        if obj.gbridge.role == 'AUTO':
            obj.gbridge.role = 'AREA' if entry.get("kind") == "area" else 'PLACEHOLDER'
        added = catalog.apply_props(obj, entry)
        self.report({'INFO'}, "{} → {} ({} props added)".format(obj.name, catalog.label(entry), len(added)))
        return {'FINISHED'}


class GBRIDGE_OT_sync_props(bpy.types.Operator):
    bl_idname = "gbridge.sync_props"
    bl_label = "Add missing props"
    bl_description = ("Add the @export props of their catalog scene that the selected objects lack, "
                      "with Godot's default values (existing values are kept)")
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        data = catalog.try_load(_project_root(context))
        if data is None:
            self.report({'ERROR'}, "No usable {} in the Godot project".format(catalog.FILENAME))
            return {'CANCELLED'}
        objects = set(context.selected_objects) | ({context.object} if context.object else set())
        total = 0
        for obj in objects:
            entry = catalog.entry_for(data, obj.gbridge.scene)
            if entry:
                total += len(catalog.apply_props(obj, entry))
        self.report({'INFO'}, "{} props added".format(total))
        return {'FINISHED'}


class GBRIDGE_OT_reload_catalog(bpy.types.Operator):
    bl_idname = "gbridge.reload_catalog"
    bl_label = "Reload catalog"
    bl_description = "Read bridge_catalog.json again from the Godot project"

    def execute(self, context):
        catalog.clear_cache()
        root = _project_root(context)
        try:
            data = catalog.load(root) if root else None
        except catalog.CatalogError as e:
            self.report({'ERROR'}, str(e))
            return {'CANCELLED'}
        self.report({'INFO'}, "{} scenes in the catalog".format(len(data["scenes"])) if data
                    else "No catalog in the Godot project")
        return {'FINISHED'}


# --- Viewport overlay ---

_handle = None


def volume_lines(context, data):
    """{color: [points]} of the volume boxes to draw (world space)."""
    by_color = {}
    for obj in context.visible_objects:
        if obj.type != 'EMPTY' or not obj.gbridge.scene or obj.gbridge.role == 'AREA':
            continue
        entry = catalog.entry_for(data, obj.gbridge.scene)
        box = catalog.blender_box(entry) if entry else None
        if box is None:
            continue
        m = obj.matrix_world
        by_color.setdefault(catalog.category_color(entry.get("category", "")), []).extend(
            m @ p for p in catalog.box_edges(*box))
    return by_color


def _draw():
    context = bpy.context
    data = catalog.try_load(_project_root(context))
    if not data:
        return
    shader = gpu.shader.from_builtin('UNIFORM_COLOR')
    gpu.state.line_width_set(2.0)
    gpu.state.depth_test_set('LESS_EQUAL')
    for color, points in volume_lines(context, data).items():
        batch = batch_for_shader(shader, 'LINES', {"pos": [tuple(p) for p in points]})
        shader.uniform_float("color", color)
        batch.draw(shader)
    gpu.state.depth_test_set('NONE')
    gpu.state.line_width_set(1.0)


CLASSES = (GBRIDGE_OT_place_entity, GBRIDGE_OT_pick_scene, GBRIDGE_OT_sync_props, GBRIDGE_OT_reload_catalog)


def register():
    global _handle
    for cls in CLASSES:
        bpy.utils.register_class(cls)
    if not bpy.app.background:
        _handle = bpy.types.SpaceView3D.draw_handler_add(_draw, (), 'WINDOW', 'POST_VIEW')


def unregister():
    global _handle
    if _handle is not None:
        bpy.types.SpaceView3D.draw_handler_remove(_handle, 'WINDOW')
        _handle = None
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)
