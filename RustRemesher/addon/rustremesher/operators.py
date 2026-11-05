# SPDX-License-Identifier: GPL-3.0-or-later
import time

import bpy

from . import blender_io, exchange, guides, prefs, runner

_ACTIVE = {"job": None}


def _source_mesh(context, obj, use_modifiers):
    """(mesh, temporary): the mesh to read. With modifiers it is a temporary copy the caller removes."""
    if use_modifiers:
        eval_obj = obj.evaluated_get(context.evaluated_depsgraph_get())
        return bpy.data.meshes.new_from_object(eval_obj), True
    return obj.data, False


def start_job(context, obj):
    """Writes the input and starts the engine for `obj` with the scene's settings; returns the EngineJob."""
    scn = context.scene.rustremesher
    engine = runner.find_engine(bpy.path.abspath(prefs.get_prefs(context).engine_path))
    me, temporary = _source_mesh(context, obj, scn.use_modifiers)
    try:
        if len(me.polygons) == 0:
            raise runner.EngineError("El objeto '{}' no tiene caras.".format(obj.name))
        arrays = blender_io.read_mesh(me, scn.color_attribute if scn.use_vertex_colors else "",
                                      use_materials=True, use_sharp=scn.use_sharp_edges)
        if scn.use_vertex_colors and "colors" not in arrays:
            raise runner.EngineError("Falta el atributo de color '{}' en '{}'.".format(scn.color_attribute, obj.name))
    finally:
        if temporary:
            bpy.data.meshes.remove(me)
    data = exchange.encode_mesh(guides=guides.collect_guides(obj), **arrays)
    axes = "".join(a for a, on in zip("XYZ", scn.symmetry) if on)
    settings = {
        "TargetQuadCount": int(scn.target_quads),
        "CurvatureAdaptivness": float(scn.adaptivness),
        "ExactQuadCount": False,
        "UseVertexColorMap": bool(scn.use_vertex_colors),
        "UseMaterialIds": bool(scn.use_materials),
        "UseIndexedNormals": False,
        "AutoDetectHardEdges": bool(scn.auto_hard_edges),
        "SymLocal": True,
        "GuideStrength": float(scn.guide_strength),
        "Threads": int(prefs.get_prefs(context).threads),
    }
    if axes:
        settings["SymAxis"] = axes
    return runner.EngineJob(engine, data, settings)


def finish_job(context, obj, job):
    """Creates the result object from a finished job and cleans up. Returns the new object."""
    scn = context.scene.rustremesher
    try:
        decoded = job.result()
    finally:
        job.cleanup()
    me = blender_io.build_mesh(obj.name + "_remesh", decoded, list(obj.data.materials), scn.shade_smooth)
    new = bpy.data.objects.new(obj.name + "_remesh", me)
    new.matrix_world = obj.matrix_world.copy()
    for col in (obj.users_collection or [context.scene.collection]):
        col.objects.link(new)
    for o in context.selected_objects:
        o.select_set(False)
    new.select_set(True)
    context.view_layer.objects.active = new
    if scn.hide_original:
        obj.hide_set(True)
    return new


class RUSTREMESHER_OT_remesh(bpy.types.Operator):
    bl_idname = "rustremesher.remesh"
    bl_label = "Remallar"
    bl_description = "Remalla el objeto activo a cuads con el engine rustremesher (Esc cancela)"
    bl_options = {'REGISTER', 'UNDO'}

    blocking: bpy.props.BoolProperty(name="Esperar", description="Espera a que termine el engine en vez de trabajar en segundo plano",
                                     default=False, options={'SKIP_SAVE'})

    _timer = None
    _job = None
    _obj = None

    @classmethod
    def poll(cls, context):
        obj = context.active_object
        return obj is not None and obj.type == 'MESH' and context.mode == 'OBJECT' and _ACTIVE["job"] is None

    def _fail(self, message):
        self.report({'ERROR'}, message)
        raise RuntimeError(message)

    def execute(self, context):
        """Synchronous run (also the way tests and scripts use it)."""
        obj = context.active_object
        try:
            job = start_job(context, obj)
        except runner.EngineError as e:
            self.report({'ERROR'}, str(e))
            return {'CANCELLED'}
        while True:
            state, _, text = job.poll()
            if state == 'running':
                time.sleep(0.05)
                continue
            if state == 'error':
                job.cleanup()
                self.report({'ERROR'}, text)
                return {'CANCELLED'}
            break
        new = finish_job(context, obj, job)
        self.report({'INFO'}, "{}: {} caras".format(new.name, len(new.data.polygons)))
        return {'FINISHED'}

    def invoke(self, context, event):
        if self.blocking:
            return self.execute(context)
        obj = context.active_object
        try:
            self._job = start_job(context, obj)
        except runner.EngineError as e:
            self.report({'ERROR'}, str(e))
            return {'CANCELLED'}
        self._obj = obj
        _ACTIVE["job"] = self._job
        wm = context.window_manager
        wm.progress_begin(0, 1000)
        self._timer = wm.event_timer_add(0.3, window=context.window)
        wm.modal_handler_add(self)
        return {'RUNNING_MODAL'}

    def _end(self, context):
        wm = context.window_manager
        if self._timer is not None:
            wm.event_timer_remove(self._timer)
            self._timer = None
        wm.progress_end()
        if context.workspace:
            context.workspace.status_text_set(None)
        _ACTIVE["job"] = None

    def modal(self, context, event):
        if event.type == 'ESC':
            self._job.cancel()
            self._end(context)
            self.report({'WARNING'}, "Remallado cancelado")
            return {'CANCELLED'}
        if event.type != 'TIMER':
            return {'PASS_THROUGH'}
        state, value, text = self._job.poll()
        if state == 'running':
            context.window_manager.progress_update(int(value * 1000))
            if context.workspace:
                context.workspace.status_text_set("RustRemesher: {} {:.0f}%  (Esc cancela)".format(text, value * 100))
            return {'RUNNING_MODAL'}
        self._end(context)
        if state == 'error':
            self._job.cleanup()
            self.report({'ERROR'}, text)
            return {'CANCELLED'}
        try:
            new = finish_job(context, self._obj, self._job)
        except Exception as e:
            self.report({'ERROR'}, "No se pudo leer el resultado: {}".format(e))
            return {'CANCELLED'}
        self.report({'INFO'}, "{}: {} caras".format(new.name, len(new.data.polygons)))
        return {'FINISHED'}


class RUSTREMESHER_OT_check_engine(bpy.types.Operator):
    bl_idname = "rustremesher.check_engine"
    bl_label = "Comprobar engine"
    bl_description = "Ejecuta el engine con --version para comprobar la ruta"

    def execute(self, context):
        try:
            path = runner.find_engine(bpy.path.abspath(prefs.get_prefs(context).engine_path))
            self.report({'INFO'}, "{} ({})".format(runner.engine_version(path), path))
        except (runner.EngineError, OSError) as e:
            self.report({'ERROR'}, str(e))
            return {'CANCELLED'}
        return {'FINISHED'}


CLASSES = (RUSTREMESHER_OT_remesh, RUSTREMESHER_OT_check_engine)


def register():
    for cls in CLASSES:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)
