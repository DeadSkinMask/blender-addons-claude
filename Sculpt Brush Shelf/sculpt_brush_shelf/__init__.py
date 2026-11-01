# Sculpt Brush Shelf: a small user-defined palette of Sculpt Mode brushes.
#
# In Blender 5.x the built-in brushes are assets, not entries of bpy.data.brushes
# (that collection is empty in a fresh session). They are activated with
# bpy.ops.brush.asset_activate(), which links the asset on demand. This addon only
# stores brush *names* and resolves them against the Essentials sculpt asset file.

import os

import bpy
from bpy.props import BoolProperty, CollectionProperty, EnumProperty, IntProperty, StringProperty
from bpy.types import AddonPreferences, Operator, Panel, PropertyGroup

ESSENTIALS_FILE = "essentials_brushes-mesh_sculpt.blend"

DEFAULT_BRUSHES = (
    "Clay Strips",
    "Inflate/Deflate",
    "Crease Sharp",
    "Draw Sharp",
    "Pinch/Magnify",
    "Smooth",
    "Flatten/Contrast",
    "Elastic Grab",
    "Grab",
    "Relax Slide",
    "Relax Pinch",
    "Scene Project",
)

_available_cache = None
# Dynamic enum items must stay referenced or Blender may show garbage strings.
_enum_items_ref = []


def available_brushes():
    """Names of the Essentials sculpt brush assets, read once from the asset file."""
    global _available_cache
    if _available_cache is None:
        path = os.path.join(
            bpy.utils.system_resource('DATAFILES'), "assets", "brushes", ESSENTIALS_FILE
        )
        names = []
        if os.path.isfile(path):
            with bpy.data.libraries.load(path, assets_only=True) as (data_from, _data_to):
                names = [n.strip() for n in data_from.brushes]
        _available_cache = sorted(names, key=str.lower)
    return _available_cache


def asset_identifier(name):
    """relative_asset_identifier of an Essentials brush, or None if it does not exist."""
    for real in available_brushes():
        if real == name.strip():
            return "brushes/{}/Brush/{}".format(ESSENTIALS_FILE, real)
    return None


class SCULPTSHELF_BrushItem(PropertyGroup):
    name: StringProperty(name="Pincel")


def get_prefs(context=None):
    context = context or bpy.context
    addon = context.preferences.addons.get(__package__)
    return addon.preferences if addon else None


def shelf_names(prefs):
    """Configured brush names; the defaults apply until the user edits the list."""
    if prefs is None:
        return []
    return [b.name for b in prefs.brushes] if prefs.customized else list(DEFAULT_BRUSHES)


def materialize(prefs):
    """Copy the defaults into the stored list the first time the user edits it."""
    if not prefs.customized:
        prefs.brushes.clear()
        for name in DEFAULT_BRUSHES:
            prefs.brushes.add().name = name
        prefs.customized = True


def reset_defaults(prefs):
    prefs.brushes.clear()
    prefs.customized = False


class SCULPTSHELF_OT_activate(Operator):
    bl_idname = "sculpt_brush_shelf.activate"
    bl_label = "Activar pincel"
    bl_description = "Activa este pincel de escultura"
    bl_options = {'INTERNAL'}

    name: StringProperty()

    @classmethod
    def poll(cls, context):
        return context.mode == 'SCULPT'

    def execute(self, context):
        identifier = asset_identifier(self.name)
        if identifier is None:
            self.report({'ERROR'}, "El pincel '{}' no existe en esta versión de Blender".format(self.name))
            return {'CANCELLED'}
        return bpy.ops.brush.asset_activate(
            asset_library_type='ESSENTIALS', relative_asset_identifier=identifier
        )


class SCULPTSHELF_OT_add(Operator):
    bl_idname = "sculpt_brush_shelf.add"
    bl_label = "Añadir pincel"
    bl_description = "Busca y añade un pincel a la paleta"
    bl_property = "brush"

    def _items(self, context):
        global _enum_items_ref
        prefs = get_prefs(context)
        used = set(shelf_names(prefs))
        _enum_items_ref = [(n, n, "") for n in available_brushes() if n not in used]
        return _enum_items_ref

    brush: EnumProperty(name="Pincel", items=_items)

    def invoke(self, context, event):
        context.window_manager.invoke_search_popup(self)
        return {'FINISHED'}

    def execute(self, context):
        prefs = get_prefs(context)
        if prefs is None or not self.brush:
            return {'CANCELLED'}
        materialize(prefs)
        prefs.brushes.add().name = self.brush
        return {'FINISHED'}


class SCULPTSHELF_OT_remove(Operator):
    bl_idname = "sculpt_brush_shelf.remove"
    bl_label = "Quitar pincel"
    bl_options = {'INTERNAL'}

    index: IntProperty()

    def execute(self, context):
        prefs = get_prefs(context)
        if prefs is None or not 0 <= self.index < len(shelf_names(prefs)):
            return {'CANCELLED'}
        materialize(prefs)
        prefs.brushes.remove(self.index)
        return {'FINISHED'}


class SCULPTSHELF_OT_move(Operator):
    bl_idname = "sculpt_brush_shelf.move"
    bl_label = "Mover pincel"
    bl_options = {'INTERNAL'}

    index: IntProperty()
    direction: IntProperty(default=-1)

    def execute(self, context):
        prefs = get_prefs(context)
        target = self.index + self.direction
        count = len(shelf_names(prefs))
        if prefs is None or not (0 <= self.index < count and 0 <= target < count):
            return {'CANCELLED'}
        materialize(prefs)
        prefs.brushes.move(self.index, target)
        return {'FINISHED'}


class SCULPTSHELF_OT_reset(Operator):
    bl_idname = "sculpt_brush_shelf.reset"
    bl_label = "Restaurar lista predeterminada"
    bl_options = {'INTERNAL'}

    def execute(self, context):
        prefs = get_prefs(context)
        if prefs is None:
            return {'CANCELLED'}
        reset_defaults(prefs)
        return {'FINISHED'}


class SCULPTSHELF_Preferences(AddonPreferences):
    bl_idname = __package__

    brushes: CollectionProperty(type=SCULPTSHELF_BrushItem)
    customized: BoolProperty(default=False)

    def draw(self, context):
        layout = self.layout
        col = layout.column(align=True)
        names = shelf_names(self)
        last = len(names) - 1
        for i, name in enumerate(names):
            row = col.row(align=True)
            missing = asset_identifier(name) is None
            row.label(text=name, icon='ERROR' if missing else 'BRUSH_DATA')
            if missing:
                row.label(text="No existe en esta versión")
            sub = row.row(align=True)
            sub.enabled = i > 0
            op = sub.operator("sculpt_brush_shelf.move", text="", icon='TRIA_UP')
            op.index, op.direction = i, -1
            sub = row.row(align=True)
            sub.enabled = i < last
            op = sub.operator("sculpt_brush_shelf.move", text="", icon='TRIA_DOWN')
            op.index, op.direction = i, 1
            row.operator("sculpt_brush_shelf.remove", text="", icon='X').index = i
        row = layout.row()
        row.operator("sculpt_brush_shelf.add", icon='ADD')
        row.operator("sculpt_brush_shelf.reset", icon='FILE_REFRESH')


class SCULPTSHELF_PT_panel(Panel):
    bl_label = "Sculpt Brush Shelf"
    bl_idname = "SCULPTSHELF_PT_panel"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "Pinceles"

    @classmethod
    def poll(cls, context):
        return context.mode == 'SCULPT'

    def draw(self, context):
        layout = self.layout
        prefs = get_prefs(context)
        names = shelf_names(prefs)
        if not names:
            layout.label(text="Paleta vacía (ver preferencias)")
            return
        brush = context.tool_settings.sculpt.brush
        active = brush.name if brush else ""
        col = layout.column(align=True)
        for name in names:
            if asset_identifier(name) is None:
                col.label(text="{} (no existe)".format(name), icon='ERROR')
                continue
            op = col.operator(
                "sculpt_brush_shelf.activate", text=name,
                icon='BRUSH_DATA', depress=(name == active),
            )
            op.name = name


classes = (
    SCULPTSHELF_BrushItem,
    SCULPTSHELF_OT_activate,
    SCULPTSHELF_OT_add,
    SCULPTSHELF_OT_remove,
    SCULPTSHELF_OT_move,
    SCULPTSHELF_OT_reset,
    SCULPTSHELF_Preferences,
    SCULPTSHELF_PT_panel,
)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
