# SPDX-License-Identifier: GPL-3.0-or-later
import bpy

from . import guides


class VIEW3D_PT_rustremesher(bpy.types.Panel):
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "RustRemesher"
    bl_label = "RustRemesher"

    def draw(self, context):
        layout = self.layout
        scn = context.scene.rustremesher
        layout.prop(scn, "target_quads")
        layout.prop(scn, "adaptivness", slider=True)
        row = layout.row()
        row.scale_y = 1.5
        row.operator("rustremesher.remesh", icon='MOD_REMESH')


class VIEW3D_PT_rustremesher_options(bpy.types.Panel):
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "RustRemesher"
    bl_label = "Opciones"
    bl_parent_id = "VIEW3D_PT_rustremesher"

    def draw(self, context):
        layout = self.layout
        scn = context.scene.rustremesher
        layout.prop(scn, "auto_hard_edges")
        layout.prop(scn, "use_sharp_edges")
        layout.prop(scn, "use_materials")
        layout.prop(scn, "use_modifiers")
        layout.prop(scn, "shade_smooth")
        layout.prop(scn, "hide_original")
        row = layout.row(align=True)
        row.label(text="Simetría")
        row.prop(scn, "symmetry", index=0, text="X", toggle=True)
        row.prop(scn, "symmetry", index=1, text="Y", toggle=True)
        row.prop(scn, "symmetry", index=2, text="Z", toggle=True)
        layout.prop(scn, "use_vertex_colors")
        obj = context.active_object
        if scn.use_vertex_colors and obj is not None and obj.type == 'MESH':
            layout.prop_search(scn, "color_attribute", obj.data, "color_attributes", text="")


class VIEW3D_PT_rustremesher_guides(bpy.types.Panel):
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "RustRemesher"
    bl_label = "Guías"
    bl_parent_id = "VIEW3D_PT_rustremesher"
    bl_options = {'DEFAULT_CLOSED'}

    def draw(self, context):
        layout = self.layout
        col = guides.guide_collection()
        count = sum(1 for o in col.all_objects if o.type == 'CURVE') if col else 0
        layout.label(text="Curvas en RR_Guides: {}".format(count))
        layout.prop(context.scene.rustremesher, "guide_strength", slider=True)
        row = layout.row(align=True)
        row.operator("rustremesher.new_guide", icon='GREASEPENCIL')
        row.operator("rustremesher.clear_guides", icon='X')


CLASSES = (VIEW3D_PT_rustremesher, VIEW3D_PT_rustremesher_options, VIEW3D_PT_rustremesher_guides)


def register():
    for cls in CLASSES:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)
