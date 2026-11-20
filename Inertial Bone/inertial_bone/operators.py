import bpy
from bpy.props import BoolProperty
from bpy.types import Operator

from . import runtime

_NOT_COPIED = {"rna_type", "enabled", "coupled_bone"}


def _pose_mode(context):
    return context.mode == 'POSE' and context.selected_pose_bones is not None


class INERTIALBONE_OT_reset(Operator):
    bl_idname = "inertial_bone.reset"
    bl_label = "Reset Simulation"
    bl_description = "Put every inertial mass back at rest (also happens when you scrub or jump frames)"

    def execute(self, context):
        runtime.reset(context.scene)
        return {'FINISHED'}


class INERTIALBONE_OT_enable_selected(Operator):
    bl_idname = "inertial_bone.enable_selected"
    bl_label = "Enable on Selected"
    bl_options = {'REGISTER', 'UNDO'}
    bl_description = "Enable or disable the inertial bone on the selected pose bones"

    enable: BoolProperty(name="Enable", default=True)

    @classmethod
    def poll(cls, context):
        return _pose_mode(context)

    def execute(self, context):
        for pb in context.selected_pose_bones:
            pb.inertial.enabled = self.enable
        return {'FINISHED'}


class INERTIALBONE_OT_couple_selected(Operator):
    bl_idname = "inertial_bone.couple_selected"
    bl_label = "Couple Selected Pair"
    bl_options = {'REGISTER', 'UNDO'}
    bl_description = "Couple the two selected pose bones with each other (e.g. left and right breast)"

    @classmethod
    def poll(cls, context):
        return _pose_mode(context)

    def execute(self, context):
        sel = list(context.selected_pose_bones)
        if len(sel) != 2:
            self.report({'ERROR'}, "Select exactly two pose bones")
            return {'CANCELLED'}
        a, b = sel
        a.inertial.coupled_bone, b.inertial.coupled_bone = b.name, a.name
        a.inertial.enabled = b.inertial.enabled = True
        return {'FINISHED'}


class INERTIALBONE_OT_copy_settings(Operator):
    bl_idname = "inertial_bone.copy_settings"
    bl_label = "Copy Settings to Selected"
    bl_options = {'REGISTER', 'UNDO'}
    bl_description = "Copy the active bone's settings to the other selected pose bones (keeps their coupling)"

    @classmethod
    def poll(cls, context):
        return _pose_mode(context) and context.active_pose_bone is not None

    def execute(self, context):
        src = context.active_pose_bone.inertial
        names = [p.identifier for p in src.bl_rna.properties if p.identifier not in _NOT_COPIED]
        count = 0
        for pb in context.selected_pose_bones:
            if pb == context.active_pose_bone:
                continue
            for n in names:
                v = getattr(src, n)
                setattr(pb.inertial, n, v[:] if hasattr(v, "__len__") and not isinstance(v, str) else v)
            count += 1
        self.report({'INFO'}, f"Copied to {count} bone(s)")
        return {'FINISHED'}


classes = (INERTIALBONE_OT_reset, INERTIALBONE_OT_enable_selected,
           INERTIALBONE_OT_couple_selected, INERTIALBONE_OT_copy_settings)


def register():
    for c in classes:
        bpy.utils.register_class(c)


def unregister():
    for c in reversed(classes):
        bpy.utils.unregister_class(c)
