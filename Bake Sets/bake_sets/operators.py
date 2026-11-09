# SPDX-License-Identifier: GPL-3.0-or-later
import bpy
from bpy.props import BoolProperty, IntProperty

from . import pipeline


def _gs(context):
    return context.scene.bake_sets


def _active(context):
    gs = _gs(context)
    return gs.sets[gs.active] if 0 <= gs.active < len(gs.sets) else None


class BAKESETS_OT_set_add(bpy.types.Operator):
    bl_idname = "bakesets.set_add"
    bl_label = "Add Bake Set"
    bl_description = "Add a bake set; the active object becomes its low poly"

    def execute(self, context):
        gs = _gs(context)
        s = gs.sets.add()
        s.name = "Set{}".format(len(gs.sets))
        if context.active_object and context.active_object.type == 'MESH':
            s.low = context.active_object
            s.name = context.active_object.name
        gs.active = len(gs.sets) - 1
        return {'FINISHED'}


class BAKESETS_OT_set_remove(bpy.types.Operator):
    bl_idname = "bakesets.set_remove"
    bl_label = "Remove Bake Set"

    @classmethod
    def poll(cls, context):
        return _active(context) is not None

    def execute(self, context):
        gs = _gs(context)
        gs.sets.remove(gs.active)
        gs.active = max(0, min(gs.active, len(gs.sets) - 1))
        return {'FINISHED'}


class BAKESETS_OT_high_add(bpy.types.Operator):
    bl_idname = "bakesets.high_add"
    bl_label = "Add Selected as High Poly"
    bl_description = "Add the selected mesh objects (except the low poly) to the high poly list"

    @classmethod
    def poll(cls, context):
        return _active(context) is not None

    def execute(self, context):
        s = _active(context)
        have = {h.obj for h in s.highs}
        n = 0
        for o in context.selected_objects:
            if o.type == 'MESH' and o != s.low and o not in have:
                s.highs.add().obj = o
                n += 1
        self.report({'INFO'}, "Added {} object(s)".format(n))
        return {'FINISHED'}


class BAKESETS_OT_high_remove(bpy.types.Operator):
    bl_idname = "bakesets.high_remove"
    bl_label = "Remove High Poly"

    @classmethod
    def poll(cls, context):
        s = _active(context)
        return s is not None and 0 <= s.high_index < len(s.highs)

    def execute(self, context):
        s = _active(context)
        s.highs.remove(s.high_index)
        s.high_index = max(0, min(s.high_index, len(s.highs) - 1))
        return {'FINISHED'}


class BAKESETS_OT_high_clear(bpy.types.Operator):
    bl_idname = "bakesets.high_clear"
    bl_label = "Clear High Poly List"

    @classmethod
    def poll(cls, context):
        s = _active(context)
        return s is not None and len(s.highs) > 0

    def execute(self, context):
        _active(context).highs.clear()
        return {'FINISHED'}


class BAKESETS_OT_bake(bpy.types.Operator):
    bl_idname = "bakesets.bake"
    bl_label = "Bake"
    bl_description = "Bake the maps of the active set (or of every set)"

    all_sets: BoolProperty(name="All Sets", default=False)

    @classmethod
    def poll(cls, context):
        return context.mode == 'OBJECT' and len(_gs(context).sets) > 0

    def execute(self, context):
        gs = _gs(context)
        sets = list(gs.sets) if self.all_sets else [s for s in [_active(context)] if s]
        for s in sets:
            errs = pipeline.validate(s, gs)
            if errs:
                self.report({'ERROR'}, "; ".join(errs))
                return {'CANCELLED'}
        wm = context.window_manager
        wm.progress_begin(0, len(sets))
        written = []
        try:
            for i, s in enumerate(sets):
                written += pipeline.bake_set(context, s, gs)
                wm.progress_update(i + 1)
        except Exception as e:
            self.report({'ERROR'}, "Bake failed: {}".format(e))
            return {'CANCELLED'}
        finally:
            wm.progress_end()
        self.report({'INFO'}, "Baked {} map(s) into {}".format(len(written), pipeline.output_dir(gs)))
        return {'FINISHED'}


classes = (BAKESETS_OT_set_add, BAKESETS_OT_set_remove, BAKESETS_OT_high_add,
           BAKESETS_OT_high_remove, BAKESETS_OT_high_clear, BAKESETS_OT_bake)
