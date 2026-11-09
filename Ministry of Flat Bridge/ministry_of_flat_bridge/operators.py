# SPDX-License-Identifier: GPL-3.0-or-later
import time

import bpy
import numpy as np

from . import obj_exchange, prefs, runner

_ACTIVE = {"running": False}


def _mesh_arrays(obj, matrix=None, normals=False):
    me = obj.data
    nv, npoly, nloop = len(me.vertices), len(me.polygons), len(me.loops)
    verts = np.empty(nv * 3, dtype=np.float32)
    me.vertices.foreach_get("co", verts)
    verts = verts.reshape(-1, 3).astype(np.float64)
    starts = np.empty(npoly, dtype=np.int32)
    totals = np.empty(npoly, dtype=np.int32)
    me.polygons.foreach_get("loop_start", starts)
    me.polygons.foreach_get("loop_total", totals)
    lv = np.empty(nloop, dtype=np.int32)
    me.loops.foreach_get("vertex_index", lv)
    part = {"verts": verts, "loop_vertex": lv, "loop_start": starts, "loop_total": totals}
    nrm = None
    if normals:
        nrm = np.empty(nloop * 3, dtype=np.float32)
        me.corner_normals.foreach_get("vector", nrm)
        nrm = nrm.reshape(-1, 3).astype(np.float64)
    if matrix is not None:
        m = np.array(matrix, dtype=np.float64)
        part["verts"] = verts @ m[:3, :3].T + m[:3, 3]
        if nrm is not None:
            n3 = np.linalg.inv(m[:3, :3]).T
            nrm = nrm @ n3.T
            nrm /= np.maximum(np.linalg.norm(nrm, axis=1, keepdims=True), 1e-12)
    if nrm is not None:
        part["loop_normals"] = nrm
    return part


class Task:
    """One tool run: the objects it covers (names, mesh sizes) and the job."""

    def __init__(self, job, entries, global_lv, global_starts, global_totals):
        self.job, self.entries = job, entries
        self.global_lv, self.global_starts, self.global_totals = global_lv, global_starts, global_totals


def _unique_mesh_objects(objs):
    seen, out = set(), []
    for o in objs:
        if o.type == 'MESH' and o.data.name not in seen:
            seen.add(o.data.name)
            out.append(o)
    return out


def plan_groups(context):
    objs = _unique_mesh_objects(context.selected_objects or ([context.active_object] if context.active_object else []))
    if not objs:
        raise runner.EngineError("Select at least one mesh object.")
    for o in objs:
        if len(o.data.polygons) == 0:
            raise runner.EngineError("Object '{}' has no faces.".format(o.name))
    return [objs] if (context.scene.mof_bridge.combine and len(objs) > 1) else [[o] for o in objs]


def tool_settings(scn):
    values = {
        "resolution": int(scn.resolution),
        "aspect": float(scn.aspect),
        "separate": bool(scn.separate_hard_edges),
        "normals": bool(scn.use_normals),
        "udims": int(scn.udims),
        "overlap": bool(scn.overlap_identical),
        "mirror": bool(scn.overlap_mirrored),
        "worldscale": bool(scn.world_scale),
    }
    if scn.world_scale:
        values["density"] = float(scn.density)
    return values


def start_group(context, objs):
    scn = context.scene.mof_bridge
    exe = runner.find_exe(bpy.path.abspath(prefs.get_prefs(context).exe_path))
    wine = runner.find_wine(bpy.path.abspath(prefs.get_prefs(context).wine_path))
    combine = len(objs) > 1
    parts, entries = [], []
    voff = poff = loff = 0
    for o in objs:
        part = _mesh_arrays(o, o.matrix_world if combine else None, scn.use_normals)
        parts.append(part)
        entries.append({"name": o.name, "mesh": o.data.name, "poly": poff, "polys": len(part["loop_start"]),
                        "loop": loff, "loops": len(part["loop_vertex"]), "vert": voff})
        voff += len(part["verts"])
        poff += entries[-1]["polys"]
        loff += entries[-1]["loops"]
    g_lv = np.concatenate([p["loop_vertex"] + e["vert"] for p, e in zip(parts, entries)])
    g_starts = np.concatenate([p["loop_start"] + e["loop"] for p, e in zip(parts, entries)])
    g_totals = np.concatenate([p["loop_total"] for p in parts])
    job = runner.EngineJob(exe, wine, tool_settings(scn), prefs.get_prefs(context).timeout)
    try:
        obj_exchange.write_obj(job.input_path, parts, scn.use_normals)
        job.start()
    except Exception:
        job.cleanup()
        raise
    return Task(job, entries, g_lv, g_starts, g_totals)


def finish_group(context, task):
    """Reads the output and writes the UVs. Returns a list of (object name, faces). Cleans the job up."""
    scn = context.scene.mof_bridge
    try:
        uvs, faces = obj_exchange.read_faces(task.job.output_path)
        corner = obj_exchange.corner_uvs(uvs, faces, task.global_lv, task.global_starts, task.global_totals)
        if scn.fit_to_tiles and not scn.world_scale:
            corner = obj_exchange.fit_to_tiles(corner, scn.udims)
    except ValueError as e:
        raise runner.EngineError("Unexpected unwrapper output: {}".format(e))
    finally:
        task.job.cleanup()
    done = []
    for e in task.entries:
        obj = bpy.data.objects.get(e["name"])
        if obj is None or obj.data.name != e["mesh"] or len(obj.data.loops) != e["loops"]:
            raise runner.EngineError("Object '{}' changed while the unwrapper was running.".format(e["name"]))
        me = obj.data
        flat = np.ascontiguousarray(corner[e["loop"]:e["loop"] + e["loops"]], dtype=np.float32).ravel()
        layer = me.uv_layers.get(scn.uv_map_name)
        if layer is None:
            if len(me.uv_layers) >= 8:
                raise runner.EngineError("'{}' already has 8 UV maps.".format(obj.name))
            layer = me.uv_layers.new(name=scn.uv_map_name)
        layer.uv.foreach_set("vector", flat)
        layer.active = True
        layer.active_render = True
        if scn.mark_seams:
            _mark_seams(me, flat.reshape(-1, 2).astype(np.float64))
        me.update()
        done.append((obj.name, e["polys"]))
    return done


def _mark_seams(me, uv):
    starts = np.empty(len(me.polygons), dtype=np.int32)
    totals = np.empty(len(me.polygons), dtype=np.int32)
    me.polygons.foreach_get("loop_start", starts)
    me.polygons.foreach_get("loop_total", totals)
    lv = np.empty(len(me.loops), dtype=np.int32)
    me.loops.foreach_get("vertex_index", lv)
    keys, base = obj_exchange.seam_edge_keys(lv, starts, totals, uv, eps=1e-5)
    ev = np.empty(len(me.edges) * 2, dtype=np.int32)
    me.edges.foreach_get("vertices", ev)
    ev = ev.reshape(-1, 2)
    ekey = np.minimum(ev[:, 0], ev[:, 1]).astype(np.int64) * base + np.maximum(ev[:, 0], ev[:, 1])
    me.edges.foreach_set("use_seam", np.isin(ekey, keys))


def _report_done(op, done):
    op.report({'INFO'}, "Unwrapped " + ", ".join("{} ({} faces)".format(n, f) for n, f in done))


class MOF_OT_unwrap(bpy.types.Operator):
    bl_idname = "mof_bridge.unwrap"
    bl_label = "Unwrap with Ministry of Flat"
    bl_description = "Unwrap the selected mesh objects with the Ministry of Flat console tool (Esc cancels)"
    bl_options = {'REGISTER', 'UNDO'}

    blocking: bpy.props.BoolProperty(name="Wait", default=False, options={'SKIP_SAVE'},
                                     description="Wait for the tool instead of working in the background")

    _timer = None
    _task = None
    _queue = None
    _done = None

    @classmethod
    def poll(cls, context):
        return context.mode == 'OBJECT' and not _ACTIVE["running"] and any(o.type == 'MESH' for o in context.selected_objects)

    def execute(self, context):
        """Synchronous run (also what tests and scripts use)."""
        done = []
        try:
            for objs in plan_groups(context):
                task = start_group(context, objs)
                while True:
                    state, text = task.job.poll()
                    if state == 'running':
                        time.sleep(0.05)
                        continue
                    break
                if state == 'error':
                    task.job.cleanup()
                    raise runner.EngineError(text)
                done += finish_group(context, task)
        except runner.EngineError as e:
            self.report({'ERROR'}, str(e))
            return {'CANCELLED'}
        _report_done(self, done)
        return {'FINISHED'}

    def invoke(self, context, event):
        if self.blocking:
            return self.execute(context)
        try:
            self._queue = plan_groups(context)
            self._task = start_group(context, self._queue.pop(0))
        except runner.EngineError as e:
            self.report({'ERROR'}, str(e))
            return {'CANCELLED'}
        self._done = []
        _ACTIVE["running"] = True
        wm = context.window_manager
        self._timer = wm.event_timer_add(0.3, window=context.window)
        wm.modal_handler_add(self)
        return {'RUNNING_MODAL'}

    def _end(self, context):
        if self._timer is not None:
            context.window_manager.event_timer_remove(self._timer)
            self._timer = None
        if context.workspace:
            context.workspace.status_text_set(None)
        _ACTIVE["running"] = False

    def modal(self, context, event):
        if event.type == 'ESC':
            self._task.job.cancel()
            self._end(context)
            self.report({'WARNING'}, "Unwrap cancelled")
            return {'CANCELLED'}
        if event.type != 'TIMER':
            return {'PASS_THROUGH'}
        state, text = self._task.job.poll()
        if state == 'running':
            if context.workspace:
                context.workspace.status_text_set("Ministry of Flat: unwrapping... {:.0f} s  (Esc cancels)".format(self._task.job.elapsed()))
            return {'RUNNING_MODAL'}
        try:
            if state == 'error':
                self._task.job.cleanup()
                raise runner.EngineError(text)
            self._done += finish_group(context, self._task)
            if self._queue:
                self._task = start_group(context, self._queue.pop(0))
                return {'RUNNING_MODAL'}
        except runner.EngineError as e:
            self._end(context)
            self.report({'ERROR'}, str(e))
            return {'CANCELLED'}
        self._end(context)
        _report_done(self, self._done)
        return {'FINISHED'}


class MOF_OT_check_engine(bpy.types.Operator):
    bl_idname = "mof_bridge.check_engine"
    bl_label = "Check Unwrapper"
    bl_description = "Run the tool on a tiny mesh to check the paths"

    def execute(self, context):
        try:
            exe = runner.find_exe(bpy.path.abspath(prefs.get_prefs(context).exe_path))
            wine = runner.find_wine(bpy.path.abspath(prefs.get_prefs(context).wine_path))
            job = runner.EngineJob(exe, wine, {"resolution": 256})
            try:
                cube = {"verts": np.array([[x, y, z] for z in (0, 1) for y in (0, 1) for x in (0, 1)], dtype=np.float64),
                        "loop_vertex": np.array([0, 2, 3, 1, 4, 5, 7, 6, 0, 1, 5, 4, 1, 3, 7, 5, 3, 2, 6, 7, 2, 0, 4, 6]),
                        "loop_start": np.arange(0, 24, 4), "loop_total": np.full(6, 4)}
                obj_exchange.write_obj(job.input_path, [cube])
                job.start()
                while job.poll()[0] == 'running':
                    time.sleep(0.05)
                state, text = job.poll()
                if state == 'error':
                    raise runner.EngineError(text)
            finally:
                job.cleanup()
        except runner.EngineError as e:
            self.report({'ERROR'}, str(e))
            return {'CANCELLED'}
        self.report({'INFO'}, "Unwrapper works ({})".format(exe))
        return {'FINISHED'}


CLASSES = (MOF_OT_unwrap, MOF_OT_check_engine)


def register():
    for cls in CLASSES:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)
