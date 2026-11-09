# SPDX-License-Identifier: GPL-3.0-or-later
import time

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
    bl_description = "Bake the maps of the active set (or of every set). Esc cancels between steps"

    all_sets: BoolProperty(name="All Sets", default=False)

    _running = False

    @classmethod
    def poll(cls, context):
        return context.mode == 'OBJECT' and len(_gs(context).sets) > 0 and not BAKESETS_OT_bake._running

    def _sets(self, context):
        gs = _gs(context)
        return list(gs.sets) if self.all_sets else [s for s in [_active(context)] if s]

    def _validate(self, context, sets):
        gs = _gs(context)
        for s in sets:
            errs = pipeline.validate(s, gs)
            if errs:
                self.report({'ERROR'}, "; ".join(errs))
                return False
        return True

    # -- synchronous (Python calls, tests) -------------------------------------------
    def execute(self, context):
        sets = self._sets(context)
        if not self._validate(context, sets):
            return {'CANCELLED'}
        gs = _gs(context)
        written = []
        try:
            for s in sets:
                written += pipeline.bake_set(context, s, gs)
        except Exception as e:
            self.report({'ERROR'}, "Bake failed: {}".format(e))
            return {'CANCELLED'}
        self.report({'INFO'}, "Baked {} map(s) into {}".format(len(written), pipeline.output_dir(gs)))
        return {'FINISHED'}

    # -- modal (button in the panel): redraws between steps, shows progress and ETA -----
    def invoke(self, context, event):
        sets = self._sets(context)
        if not self._validate(context, sets):
            return {'CANCELLED'}
        gs = _gs(context)
        self._jobs = [(s, pipeline.total_cost(s, gs)) for s in sets]
        self._grand = sum(c for _, c in self._jobs)
        self._base = 0.0          # cost of the finished sets
        self._index = 0
        self._gen = None
        self._written = []
        self._label = "Starting"
        self._t0 = time.time()
        BAKESETS_OT_bake._running = True
        wm = context.window_manager
        wm.progress_begin(0, 100)
        self._timer = wm.event_timer_add(0.05, window=context.window)
        wm.modal_handler_add(self)
        self._status(context, 0.0)
        return {'RUNNING_MODAL'}

    def _status(self, context, fraction):
        elapsed = time.time() - self._t0
        eta = ""
        if fraction > 0.03 and elapsed > 2.0:
            left = elapsed * (1.0 - fraction) / fraction
            eta = " - about {} left".format(_fmt(left))
        context.window_manager.progress_update(int(fraction * 100))
        if context.workspace:
            context.workspace.status_text_set("Bake Sets: {} ({}%{}) - Esc to cancel".format(
                self._label, int(fraction * 100), eta))

    def _end(self, context):
        wm = context.window_manager
        if self._gen is not None:
            self._gen.close()      # removes the temporary scene if a set was in progress
            self._gen = None
        wm.event_timer_remove(self._timer)
        wm.progress_end()
        if context.workspace:
            context.workspace.status_text_set(None)
        BAKESETS_OT_bake._running = False

    def modal(self, context, event):
        if event.type == 'ESC':
            self._end(context)
            self.report({'WARNING'}, "Bake cancelled")
            return {'CANCELLED'}
        if event.type != 'TIMER':
            return {'RUNNING_MODAL'}
        gs = _gs(context)
        try:
            while True:
                if self._gen is None:
                    if self._index >= len(self._jobs):
                        self._end(context)
                        self.report({'INFO'}, "Baked {} map(s) into {} in {}".format(
                            len(self._written), pipeline.output_dir(gs), _fmt(time.time() - self._t0)))
                        return {'FINISHED'}
                    self._gen = pipeline.bake_set_steps(context, self._jobs[self._index][0], gs)
                try:
                    # Runs the previous step, then announces the next one (shown before it runs).
                    self._label, done = next(self._gen)
                except StopIteration as e:
                    self._written += e.value or []
                    self._base += self._jobs[self._index][1]
                    self._index += 1
                    self._gen = None
                    continue
                self._status(context, min(0.99, (self._base + done) / max(self._grand, 1e-6)))
                return {'RUNNING_MODAL'}
        except Exception as e:
            self._end(context)
            self.report({'ERROR'}, "Bake failed: {}".format(e))
            return {'CANCELLED'}

    def cancel(self, context):
        if BAKESETS_OT_bake._running:
            self._end(context)


def _fmt(seconds):
    seconds = int(seconds + 0.5)
    return "{}:{:02d} min".format(seconds // 60, seconds % 60) if seconds >= 60 else "{} s".format(seconds)


classes = (BAKESETS_OT_set_add, BAKESETS_OT_set_remove, BAKESETS_OT_high_add,
           BAKESETS_OT_high_remove, BAKESETS_OT_high_clear, BAKESETS_OT_bake)
