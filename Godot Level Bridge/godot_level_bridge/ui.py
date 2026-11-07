"""Sidebar panels (3D View → N → "Godot Level") and add-on preferences."""

import bpy
from bpy.props import StringProperty

from . import entities, export, godot_project, paths, scope

ISSUE_ICONS = {'ERROR': 'ERROR', 'WARNING': 'ERROR', 'INFO': 'INFO'}


class GBRIDGE_Preferences(bpy.types.AddonPreferences):
    bl_idname = __package__

    godot_project_root: StringProperty(
        name="Godot project", subtype='DIR_PATH',
        description="Folder that contains project.godot")

    def draw(self, context):
        self.layout.prop(self, "godot_project_root")


def _project_root(context):
    try:
        return paths.project_root_from_prefs(context.preferences.addons[__package__].preferences)
    except paths.PathError:
        return None


class GBRIDGE_PT_level(bpy.types.Panel):
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "Godot Level"
    bl_label = "Level"

    def draw(self, context):
        layout = self.layout
        props = context.scene.gbridge
        prefs = context.preferences.addons[__package__].preferences

        try:
            root = paths.project_root_from_prefs(prefs)
            layout.label(text=root, icon='FILE_FOLDER')
        except paths.PathError as e:
            root = None
            layout.label(text=str(e), icon='ERROR')

        layout.prop(props, "root")
        layout.prop(props, "level_name", placeholder="(.blend file name)")
        # Blender paints a path field red while the folder does not exist yet;
        # the line below says where it resolves and whether export will create it.
        for attr in ("output_dir", "assets_dir"):
            layout.prop(props, attr)
            if root:
                text, icon = paths.describe_dir(root, getattr(props, attr))
                layout.label(text=text, icon=icon)

        col = layout.column(heading="Defaults")
        col.prop(props, "modifiers")
        col.prop(props, "instances")
        col.prop(props, "vertex_colors")
        col.prop(props, "multimesh_chunk")

        layout.operator("gbridge.show_scope", icon='RESTRICT_SELECT_OFF')
        row = layout.row(align=True)
        row.operator("gbridge.validate", icon='CHECKMARK')
        row.operator("gbridge.export_level", icon='EXPORT')


def _draw_layers(layout, props, attr, names):
    """One checkbox per named Godot layer (the bit index is the layer number - 1)."""
    box = layout.box()
    box.label(text="Layer" if attr == "layer" else "Mask")
    if not names:
        box.label(text="No [layer_names] in project.godot", icon='INFO')
        return
    grid = box.grid_flow(columns=2, even_columns=True)
    for number in sorted(names):
        grid.prop(props, attr, index=number - 1, text="{} {}".format(number, names[number]))


class GBRIDGE_PT_object(bpy.types.Panel):
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "Godot Level"
    bl_label = "Object"

    @classmethod
    def poll(cls, context):
        return context.object is not None

    def draw(self, context):
        layout = self.layout
        obj = context.object
        props = obj.gbridge
        layout.prop(props, "role")
        role = dict(scope.collect(context.scene, context.view_layer)).get(obj)
        layout.label(text="Exported as: {}".format(role.lower().replace("_", " ") if role else "(not exported)"),
                     icon='EXPORT' if role else 'CANCEL')
        layout.prop(props, "skip")

        if role in ('PLACEHOLDER', 'AREA'):
            layout.prop(props, "scene")
        if role in ('COLLIDER', 'AREA'):
            layout.prop(props, "shape")
            if role == 'COLLIDER':
                layout.prop(props, "body")
                layout.prop(props, "body_group")
            root = _project_root(context)
            names = godot_project.layer_names(root) if root else {}
            _draw_layers(layout, props, "layer", names)
            _draw_layers(layout, props, "mask", names)
            layout.label(text="None checked = default (collider: world; area: detects player)", icon='INFO')
        if role == 'STATIC':
            layout.prop(props, "modifiers")
            layout.prop(props, "instances")
            layout.prop(props, "vertex_colors")
        if role == 'INSTANCE' and obj.instance_collection:
            layout.label(text="Asset: {}".format(entities.asset_name(obj.instance_collection)),
                         icon='OUTLINER_COLLECTION')
            layout.operator("gbridge.export_asset", icon='EXPORT')
        if role in ('PLACEHOLDER', 'MARKER', 'AREA', 'PATH', 'INSTANCE', 'LIGHT'):
            layout.label(text="Props: the object's Custom Properties", icon='PROPERTIES')

        row = layout.row()
        row.enabled = False
        row.prop(props, "uid")


class GBRIDGE_PT_collection(bpy.types.Panel):
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "Godot Level"
    bl_label = "Active collection"

    @classmethod
    def poll(cls, context):
        return context.collection is not None

    def draw(self, context):
        layout = self.layout
        layout.label(text=context.collection.name, icon='OUTLINER_COLLECTION')
        layout.prop(context.collection.gbridge, "role")
        layout.prop(context.collection.gbridge, "skip")


class GBRIDGE_PT_validation(bpy.types.Panel):
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "Godot Level"
    bl_label = "Validation"

    def draw(self, context):
        layout = self.layout
        if not export.last_issues:
            layout.label(text="No issues (or not validated yet)", icon='CHECKMARK')
            return
        for issue in export.last_issues:
            row = layout.row(align=True)
            op = row.operator("gbridge.select_object", text="", icon='RESTRICT_SELECT_OFF', emboss=False)
            op.name = issue.object
            row.label(text="{}: {}".format(issue.object, issue.message), icon=ISSUE_ICONS[issue.level])


CLASSES = (GBRIDGE_Preferences, GBRIDGE_PT_level, GBRIDGE_PT_object, GBRIDGE_PT_collection,
           GBRIDGE_PT_validation)


def register():
    for cls in CLASSES:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)
