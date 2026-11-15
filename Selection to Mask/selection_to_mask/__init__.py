# SPDX-License-Identifier: GPL-3.0-or-later
"""Selection to Mask: writes the Edit Mode selection into the sculpt mask.

By default the mask goes on what is NOT selected, so you select what you want
to sculpt and the rest is protected. The mode never changes: switch to Sculpt
Mode yourself. Works in Edit Mode through bmesh (no mode switching, no
bpy.ops), on every mesh being edited.
"""

import bpy
import bmesh

MASK = ".sculpt_mask"


def _mask_layer(bm):
    return bm.verts.layers.float.get(MASK) or bm.verts.layers.float.new(MASK)


class MASKSEL_OT_apply(bpy.types.Operator):
    bl_idname = "mask_selection.apply"
    bl_label = "Selection to Mask"
    bl_description = "Write the Edit Mode selection into the sculpt mask"
    bl_options = {'REGISTER', 'UNDO'}

    invert: bpy.props.BoolProperty(
        name="Mask Unselected",
        description="Mask what is not selected (so only the selection can be sculpted)",
        default=True,
    )
    keep_existing: bpy.props.BoolProperty(
        name="Keep Existing Mask",
        description="Combine with the current mask instead of replacing it",
        default=False,
    )

    @classmethod
    def poll(cls, context):
        return context.mode == 'EDIT_MESH'

    def execute(self, context):
        objs = list(context.objects_in_mode_unique_data)
        if not objs:
            self.report({'WARNING'}, "No mesh in Edit Mode")
            return {'CANCELLED'}
        for obj in objs:
            mesh = obj.data
            bm = bmesh.from_edit_mesh(mesh)
            lay = _mask_layer(bm)
            for v in bm.verts:
                # hidden vertices are never "selected": leave them as they are
                if v.hide:
                    continue
                value = 0.0 if v.select else 1.0
                if not self.invert:
                    value = 1.0 - value
                if self.keep_existing:
                    value = max(value, v[lay])
                v[lay] = value
            bmesh.update_edit_mesh(mesh, loop_triangles=False, destructive=False)
        return {'FINISHED'}


class MASKSEL_OT_clear(bpy.types.Operator):
    bl_idname = "mask_selection.clear"
    bl_label = "Clear Mask"
    bl_description = "Remove the sculpt mask of the meshes in Edit Mode"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return context.mode == 'EDIT_MESH'

    def execute(self, context):
        for obj in context.objects_in_mode_unique_data:
            mesh = obj.data
            bm = bmesh.from_edit_mesh(mesh)
            lay = bm.verts.layers.float.get(MASK)
            if lay is not None:
                bm.verts.layers.float.remove(lay)
                bmesh.update_edit_mesh(mesh, loop_triangles=False, destructive=False)
        return {'FINISHED'}


class MASKSEL_PT_panel(bpy.types.Panel):
    bl_label = "Selection to Mask"
    bl_idname = "MASKSEL_PT_panel"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "Edit"

    @classmethod
    def poll(cls, context):
        return context.mode == 'EDIT_MESH'

    def draw(self, context):
        col = self.layout.column(align=True)
        col.operator("mask_selection.apply", text="Mask Unselected").invert = True
        col.operator("mask_selection.apply", text="Mask Selected").invert = False
        self.layout.operator("mask_selection.clear")


classes = (MASKSEL_OT_apply, MASKSEL_OT_clear, MASKSEL_PT_panel)


def register():
    for c in classes:
        bpy.utils.register_class(c)


def unregister():
    for c in reversed(classes):
        bpy.utils.unregister_class(c)
