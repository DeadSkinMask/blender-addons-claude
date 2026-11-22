import bpy
from bpy.props import (BoolProperty, EnumProperty, FloatProperty, FloatVectorProperty,
                       PointerProperty, StringProperty)
from bpy.types import PropertyGroup


def _on_bone_toggle(self, context):
    from . import runtime
    runtime.sync_disabled()


def _on_preview_toggle(self, context):
    from . import runtime
    runtime.set_preview(context.scene, self.preview)


class InertialBoneSettings(PropertyGroup):
    """Per pose bone. Same controls and defaults as ``InertialBone3D`` in Godot.
    Vectors are in character axes: X = right, Y = up, Z = back."""

    enabled: BoolProperty(
        name="Inertial Bone", default=False, update=_on_bone_toggle,
        description="Simulate this bone as an inertial mass while the preview is on")

    # Dynamics
    frequency: FloatVectorProperty(
        name="Frequency", size=3, default=(2.0, 2.0, 2.0), min=0.05, soft_max=10.0,
        description="Natural frequency in Hz per axis for mass 1. Higher = stiffer and faster")
    damping_ratio: FloatProperty(
        name="Damping Ratio", default=0.3, min=0.0, soft_max=1.5,
        description="0 bounces forever, 1 is critical damping. Acts on the velocity relative to the anchor")
    mass: FloatProperty(
        name="Mass", default=1.0, min=0.05, soft_max=20.0,
        description="Effective frequency = frequency / sqrt(mass): heavier is slower")
    damping_pos: FloatVectorProperty(
        name="Damping +", size=3, default=(1.0, 1.0, 1.0), min=0.0, soft_max=5.0,
        description="Damping multiplier when moving toward +X, +Y, +Z")
    damping_neg: FloatVectorProperty(
        name="Damping -", size=3, default=(1.0, 1.0, 1.0), min=0.0, soft_max=5.0,
        description="Damping multiplier when moving toward -X, -Y, -Z")
    gravity: FloatVectorProperty(
        name="Gravity", size=3, default=(0.0, 0.0, -7.0), subtype='ACCELERATION',
        description="World-space acceleration (Blender axes, Z up). Static sag is g / omega^2")

    gravity_compensation: FloatProperty(
        name="Gravity Compensation", default=0.0, min=0.0, soft_max=1.0,
        description="Rest offset: moves the bone UP by the amount it sags at rest, like raising the vertices "
                    "by hand. Not a force: the simulation is unchanged (same sag, bounce and limits). "
                    "0 = off, 1 = at rest the bone sits at the modelled pose")

    # Limits
    max_offset: FloatProperty(
        name="Max Offset", default=0.06, min=0.0, soft_max=0.5, subtype='DISTANCE',
        description="Master maximum offset. Each direction's limit = master * factor")
    max_factor_pos: FloatVectorProperty(
        name="Factor +", size=3, default=(1.0, 1.0, 1.0), min=0.0, soft_max=1.5,
        description="Max offset factor toward +X, +Y, +Z")
    max_factor_neg: FloatVectorProperty(
        name="Factor -", size=3, default=(1.0, 1.0, 1.0), min=0.0, soft_max=1.5,
        description="Max offset factor toward -X, -Y, -Z")
    progressive: FloatProperty(
        name="Progressive", default=3.0, min=0.0, max=20.0,
        description="0 = linear spring; higher stiffens it near the limit")
    limit_bounce: FloatProperty(
        name="Limit Bounce", default=0.0, min=0.0, max=1.0,
        description="Bounce when the mass reaches a limit: 0 = it stops there, 1 = it bounces back with all "
                    "its speed. Shows on hard stops (hitting a wall, landing), where the mass hits the limit at once")

    # Tilt from offset
    tilt_from_z: FloatProperty(
        name="From Z", default=2.0, soft_min=-6.0, soft_max=6.0,
        description="Degrees of pitch (about X) per cm of offset along Z")
    tilt_from_x: FloatProperty(
        name="From X", default=2.0, soft_min=-6.0, soft_max=6.0,
        description="Degrees of roll (about Z) per cm of offset along X")
    tilt_from_y: FloatProperty(
        name="From Y", default=1.0, soft_min=-6.0, soft_max=6.0,
        description="Degrees of pitch (about X) per cm of offset along Y")
    max_tilt: FloatProperty(
        name="Max Tilt", default=20.0, min=0.0, max=45.0,
        description="Maximum tilt per axis in degrees")

    # Coupling
    coupled_bone: StringProperty(
        name="Coupled With", description="Another bone of this armature to couple with (left <-> right)")
    coupling: FloatProperty(
        name="Coupling", default=20.0, min=0.0, soft_max=100.0,
        description="Strength of the spring between both offsets (omega^2 units, mass 1)")

    # Axes
    axes_object: PointerProperty(
        name="Axes Reference", type=bpy.types.Object,
        description="Object whose axes define X/Y/Z for offsets, limits and tilt. Empty = the armature object")
    front_axis: EnumProperty(
        name="Character Front", default='NEG_Y',
        items=[('NEG_Y', "-Y (Blender default)", "The character faces -Y"),
               ('POS_Y', "+Y", "The character faces +Y")],
        description="Which way the reference object's character faces")


class InertialBoneScene(PropertyGroup):
    preview: BoolProperty(
        name="Preview", default=False, update=_on_preview_toggle,
        description="Run the inertial bone simulation on frame change. Turn it off to stop all processing")


classes = (InertialBoneSettings, InertialBoneScene)


def register():
    for c in classes:
        bpy.utils.register_class(c)
    bpy.types.PoseBone.inertial = PointerProperty(type=InertialBoneSettings)
    bpy.types.Scene.inertial_bone = PointerProperty(type=InertialBoneScene)


def unregister():
    del bpy.types.Scene.inertial_bone
    del bpy.types.PoseBone.inertial
    for c in reversed(classes):
        bpy.utils.unregister_class(c)
