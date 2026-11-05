# SPDX-License-Identifier: GPL-3.0-or-later
"""Guide curves: curve objects in the `RR_Guides` collection. The edge flow of the result follows them (softly)."""

import bpy
import numpy as np
from mathutils import geometry

GUIDE_COLLECTION = "RR_Guides"


def guide_collection(create=False, scene=None):
    col = bpy.data.collections.get(GUIDE_COLLECTION)
    if col is None and create:
        col = bpy.data.collections.new(GUIDE_COLLECTION)
        (scene or bpy.context.scene).collection.children.link(col)
    return col


def spline_points(spline, default_resolution=12):
    """The spline as a list of world-space-agnostic (local to the curve) points, and whether it is closed."""
    pts = []
    if spline.type == 'BEZIER':
        knots = spline.bezier_points
        n = len(knots)
        count = n if spline.use_cyclic_u else n - 1
        res = max(2, spline.resolution_u or default_resolution)
        for i in range(count):
            a, b = knots[i], knots[(i + 1) % n]
            seg = geometry.interpolate_bezier(a.co, a.handle_right, b.handle_left, b.co, res)
            pts.extend(seg[:-1])
        if not spline.use_cyclic_u:
            pts.append(knots[-1].co.copy())
    else:  # poly / NURBS: the control polygon
        pts = [p.co.to_3d() for p in spline.points]
    return pts, bool(spline.use_cyclic_u)


def collect_guides(target):
    """[(closed, stroke, (N, 3) points in `target`'s local space)] for every curve in the guide collection."""
    col = guide_collection()
    if col is None:
        return []
    inverse = target.matrix_world.inverted()
    out = []
    for obj in col.all_objects:
        if obj.type != 'CURVE':
            continue
        to_local = inverse @ obj.matrix_world
        for spline in obj.data.splines:
            pts, closed = spline_points(spline)
            if len(pts) < 2:
                continue
            arr = np.array([tuple(to_local @ p) for p in pts], dtype=np.float32)
            out.append((closed, len(out), arr))
    return out


class RUSTREMESHER_OT_new_guide(bpy.types.Operator):
    bl_idname = "rustremesher.new_guide"
    bl_label = "Nueva guía"
    bl_description = "Crea una curva guía y activa el dibujo sobre la superficie del objeto"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return context.mode == 'OBJECT'

    def execute(self, context):
        col = guide_collection(create=True, scene=context.scene)
        data = bpy.data.curves.new("RR_Guide", 'CURVE')
        data.dimensions = '3D'
        obj = bpy.data.objects.new("RR_Guide", data)
        col.objects.link(obj)
        for o in context.selected_objects:
            o.select_set(False)
        obj.select_set(True)
        context.view_layer.objects.active = obj
        bpy.ops.object.mode_set(mode='EDIT')
        try:
            paint = context.scene.tool_settings.curve_paint_settings
            paint.curve_type = 'BEZIER'
            paint.surface_plane = 'SURFACE'
            bpy.ops.wm.tool_set_by_id(name="builtin.draw")
        except Exception:  # headless or a workspace without the tool: the curve is still there to edit by hand
            pass
        self.report({'INFO'}, "Dibuja la guía sobre la superficie y vuelve al modo objeto (Tab)")
        return {'FINISHED'}


class RUSTREMESHER_OT_clear_guides(bpy.types.Operator):
    bl_idname = "rustremesher.clear_guides"
    bl_label = "Quitar guías"
    bl_description = "Borra las curvas de la colección RR_Guides"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return context.mode == 'OBJECT' and guide_collection() is not None

    def execute(self, context):
        col = guide_collection()
        for obj in list(col.objects):
            data = obj.data
            bpy.data.objects.remove(obj)
            if data is not None and data.users == 0:
                bpy.data.curves.remove(data)
        bpy.data.collections.remove(col)
        return {'FINISHED'}


CLASSES = (RUSTREMESHER_OT_new_guide, RUSTREMESHER_OT_clear_guides)


def register():
    for cls in CLASSES:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)
