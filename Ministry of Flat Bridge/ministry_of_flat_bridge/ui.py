# SPDX-License-Identifier: GPL-3.0-or-later
import bpy


class VIEW3D_PT_mof_bridge(bpy.types.Panel):
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "MoF"
    bl_label = "Ministry of Flat"

    def draw(self, context):
        layout = self.layout
        scn = context.scene.mof_bridge
        layout.prop(scn, "uv_map_name")
        layout.prop(scn, "resolution")
        layout.prop(scn, "combine")
        layout.prop(scn, "fit_to_tiles")
        layout.prop(scn, "mark_seams")
        row = layout.row()
        row.scale_y = 1.5
        row.operator("mof_bridge.unwrap", icon='UV')
        if scn.last_report:
            layout.label(text=scn.last_report, icon='INFO')


class VIEW3D_PT_mof_bridge_pack(bpy.types.Panel):
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "MoF"
    bl_label = "Pack"
    bl_parent_id = "VIEW3D_PT_mof_bridge"

    def draw_header(self, context):
        self.layout.prop(context.scene.mof_bridge, "pack_after", text="")

    def draw(self, context):
        layout = self.layout
        scn = context.scene.mof_bridge
        layout.active = scn.pack_after
        layout.prop(scn, "pack_gap_px")
        layout.prop(scn, "pack_rotation")
        layout.prop(scn, "pack_tile")
        layout.prop(scn, "pack_keep_overlap")


class VIEW3D_PT_mof_bridge_options(bpy.types.Panel):
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "MoF"
    bl_label = "Options"
    bl_parent_id = "VIEW3D_PT_mof_bridge"

    def draw(self, context):
        layout = self.layout
        scn = context.scene.mof_bridge
        layout.prop(scn, "separate_hard_edges")
        layout.prop(scn, "use_normals")
        layout.prop(scn, "overlap_identical")
        layout.prop(scn, "overlap_mirrored")
        layout.prop(scn, "aspect")
        layout.prop(scn, "udims")
        layout.prop(scn, "world_scale")
        sub = layout.row()
        sub.active = scn.world_scale
        sub.prop(scn, "density")


CLASSES = (VIEW3D_PT_mof_bridge, VIEW3D_PT_mof_bridge_pack, VIEW3D_PT_mof_bridge_options)


def register():
    for cls in CLASSES:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)
