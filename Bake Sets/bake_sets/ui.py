# SPDX-License-Identifier: GPL-3.0-or-later
import bpy

from . import props
from .maps import MAP_ITEMS


class BAKESETS_UL_sets(bpy.types.UIList):
    def draw_item(self, context, layout, data, item, icon, active_data, active_prop, index):
        layout.prop(item, "name", text="", emboss=False, icon='RENDERLAYERS')


class BAKESETS_UL_highs(bpy.types.UIList):
    def draw_item(self, context, layout, data, item, icon, active_data, active_prop, index):
        layout.prop(item, "obj", text="")


class BAKESETS_PT_main(bpy.types.Panel):
    bl_label = "Bake Sets"
    bl_idname = "BAKESETS_PT_main"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "Bake"

    def draw(self, context):
        gs = context.scene.bake_sets
        lay = self.layout
        row = lay.row()
        row.template_list("BAKESETS_UL_sets", "", gs, "sets", gs, "active", rows=3)
        col = row.column(align=True)
        col.operator("bakesets.set_add", icon='ADD', text="")
        col.operator("bakesets.set_remove", icon='REMOVE', text="")
        s = gs.sets[gs.active] if 0 <= gs.active < len(gs.sets) else None
        if s is None:
            lay.label(text="Add a set to start", icon='INFO')
            return
        lay.prop(s, "low")
        lay.prop(s, "cage")
        box = lay.box()
        box.label(text="High poly objects ({})".format(len(s.highs) or "none: bakes the low poly itself"))
        row = box.row()
        row.template_list("BAKESETS_UL_highs", "", s, "highs", s, "high_index", rows=3)
        col = row.column(align=True)
        col.operator("bakesets.high_add", icon='ADD', text="")
        col.operator("bakesets.high_remove", icon='REMOVE', text="")
        col.operator("bakesets.high_clear", icon='TRASH', text="")
        row = lay.row(align=True)
        row.operator("bakesets.bake", icon='RENDER_STILL', text="Bake Set").all_sets = False
        row.operator("bakesets.bake", text="Bake All").all_sets = True


class BAKESETS_PT_maps(bpy.types.Panel):
    bl_label = "Maps"
    bl_parent_id = "BAKESETS_PT_main"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "Bake"

    @classmethod
    def poll(cls, context):
        gs = context.scene.bake_sets
        return 0 <= gs.active < len(gs.sets)

    def draw(self, context):
        gs = context.scene.bake_sets
        s = gs.sets[gs.active]
        col = self.layout.column(align=True)
        col.prop(s, "maps", expand=True)
        wants = s.maps
        if wants & {'NORMAL', 'BENT_NORMAL'}:
            box = self.layout.box()
            uv = s.low.data.uv_layers.active if s.low and s.low.type == 'MESH' else None
            box.label(text="Baking to UV: {}".format(uv.name if uv else "(none)"), icon='UV')
            col = box.column(align=True)
            col.scale_y = 0.8
            col.label(text="Tangent space maps need that same UV:")
            col.label(text="set it in the Normal Map node's")
            col.label(text="UV Map field (or make it the render UV).")
        self.layout.separator()
        c = self.layout.column()
        c.prop(s, "resolution")
        c.prop(s, "margin")
        c.prop(s, "output_name", placeholder=s.name)
        n = len(props._others(s))
        if n:
            c.label(text="Shares the texture and these settings with {} other set(s)".format(n), icon='LINKED')
        c.prop(s, "clear_before_bake")
        if 'ALBEDO' in wants:
            c.prop(s, "albedo_alpha")
        if wants & {'NORMAL', 'AO', 'THICKNESS', 'ALBEDO', 'ROUGHNESS', 'METALLIC',
                    'MATERIAL_ID', 'ELEMENT_ID', 'ORM', 'PAINT_BASE'} or True:
            c.prop(s, "cage_extrusion")
            c.prop(s, "max_ray_distance")
        if wants & {'AO', 'ORM', 'PAINT_BASE'}:
            c.prop(s, "ao_distance")
        if 'THICKNESS' in wants:
            c.prop(s, "thickness_distance")
        if 'CURVATURE' in wants:
            c.prop(s, "curvature_radius")
            c.prop(s, "curvature_strength")
        if 'CAVITY' in wants:
            c.prop(s, "cavity_radius")
            c.prop(s, "cavity_strength")
        if 'DUST' in wants:
            c.prop(s, "dust_threshold")
            c.prop(s, "dust_softness")
        if 'PAINT_BASE' in wants:
            c.prop(s, "paint_ground")
            c.prop(s, "paint_ao_strength")


class BAKESETS_PT_output(bpy.types.Panel):
    bl_label = "Output"
    bl_parent_id = "BAKESETS_PT_main"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "Bake"

    def draw(self, context):
        gs = context.scene.bake_sets
        c = self.layout.column()
        c.prop(gs, "output_dir")
        c.prop(gs, "device")
        c.prop(gs, "samples")
        c.prop(gs, "ao_samples")
        c.prop(gs, "normal_y")


classes = (BAKESETS_UL_sets, BAKESETS_UL_highs, BAKESETS_PT_main, BAKESETS_PT_maps, BAKESETS_PT_output)
