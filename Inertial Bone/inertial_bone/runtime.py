"""Frame handler, anchor computation and writing the result back to the pose bone."""
import math

import bpy
from bpy.app.handlers import persistent
from mathutils import Matrix, Vector

from .sim import Sim, rest_compensation

STATE = {}      # (object name, bone name) -> Sim
ORIGINAL = {}   # (object name, bone name) -> pose values captured before the first write
_last_frame = {}  # scene name -> last processed frame


# ---------------------------------------------------------------- geometry
def base_matrix(pb):
    """Armature-space matrix of the bone with an identity pose basis (its anchor frame)."""
    if pb.parent:
        return pb.parent.matrix @ pb.parent.bone.matrix_local.inverted() @ pb.bone.matrix_local
    return pb.bone.matrix_local.copy()


def frame_basis(ref, front_axis):
    """Matrix whose columns are the world directions of character right, up and back."""
    r = ref.matrix_world.to_quaternion().to_matrix()
    x, y, z = r.col[0], r.col[1], r.col[2]
    if front_axis == 'NEG_Y':       # faces -Y: right = -X, back = +Y
        cols = (-x, z, y)
    else:                           # faces +Y: right = +X, back = -Y
        cols = (x, z, -y)
    return Matrix((cols[0], cols[1], cols[2])).transposed()


# ---------------------------------------------------------------- capture / restore
def _capture(key, pb):
    if key not in ORIGINAL:
        ORIGINAL[key] = (pb.location.copy(), pb.rotation_quaternion.copy(),
                         pb.rotation_euler.copy(), tuple(pb.rotation_axis_angle))


def _restore(key):
    obj = bpy.data.objects.get(key[0])
    saved = ORIGINAL.pop(key, None)
    STATE.pop(key, None)
    if obj is None or obj.pose is None or saved is None:
        return
    pb = obj.pose.bones.get(key[1])
    if pb is None:
        return
    pb.location, pb.rotation_quaternion, pb.rotation_euler, pb.rotation_axis_angle = saved


def sync_disabled():
    """Restore and forget every bone that is no longer enabled (or no longer exists)."""
    for key in list(STATE):
        obj = bpy.data.objects.get(key[0])
        pb = obj.pose.bones.get(key[1]) if obj and obj.pose else None
        if pb is None or not pb.inertial.enabled:
            _restore(key)


def clear_all():
    for key in list(STATE):
        _restore(key)
    ORIGINAL.clear()
    _last_frame.clear()


# ---------------------------------------------------------------- core
def _write_pose(obj, pb, s, b, off_world, base):
    arm_rot = obj.matrix_world.to_quaternion().to_matrix()
    arm_rot_inv = arm_rot.inverted()
    base3 = base.to_3x3()
    # translation: world offset -> armature space -> bone-local pose location
    pb.location = base3.inverted() @ (arm_rot_inv @ off_world)

    # tilt about the character axes, pivoting at the bone head (units: scene length -> cm)
    scale = bpy.context.scene.unit_settings.scale_length
    oc = (b.transposed() @ off_world) * (100.0 * scale)
    lim = s.max_tilt
    rx = max(-lim, min(lim, s.tilt_from_z * oc.z + s.tilt_from_y * oc.y))
    rz = max(-lim, min(lim, -s.tilt_from_x * oc.x))
    tilt = Matrix.Rotation(math.radians(rx), 3, 'X') @ Matrix.Rotation(math.radians(rz), 3, 'Z')
    world_rot = b @ tilt @ b.transposed()
    arm_space = arm_rot_inv @ world_rot @ arm_rot
    base_rot = base3.normalized()
    q = (base_rot.inverted() @ arm_space @ base_rot).to_quaternion()
    mode = pb.rotation_mode
    if mode == 'QUATERNION':
        pb.rotation_quaternion = q
    elif mode == 'AXIS_ANGLE':
        axis, angle = q.to_axis_angle()
        pb.rotation_axis_angle = (angle, axis.x, axis.y, axis.z)
    else:
        pb.rotation_euler = q.to_euler(mode, pb.rotation_euler)


def process_scene(scene, dt):
    """Simulate every enabled bone of every armature in ``scene``. ``dt`` None = (re)initialise."""
    sims = {}
    for obj in scene.objects:
        if obj.type != 'ARMATURE' or obj.pose is None:
            continue
        for pb in obj.pose.bones:
            s = pb.inertial
            if not s.enabled:
                continue
            key = (obj.name, pb.name)
            _capture(key, pb)
            base = base_matrix(pb)
            anchor = obj.matrix_world @ base.translation
            ref = s.axes_object or obj
            b = frame_basis(ref, s.front_axis)
            sim = STATE.get(key)
            if sim is None:
                sim = STATE[key] = Sim()
            partner = None
            if s.coupled_bone:
                other = STATE.get((obj.name, s.coupled_bone))
                if other is not None and other.ready:
                    partner = other.offset_world
            if dt is None:
                sim.reset(anchor)
                off = sim.offset_world
            else:
                off = sim.step(s, dt, anchor, b, partner)
            # drawn offset = simulated offset + rest offset (gravity compensation); the simulation itself
            # (and the offset the coupled bone reads) never includes the compensation
            _write_pose(obj, pb, s, b, off + rest_compensation(s, b), base)
            sims[key] = sim
    return len(sims)


def on_frame_change(scene, depsgraph=None):
    if not scene.inertial_bone.preview:
        return
    fps = scene.render.fps / scene.render.fps_base
    frame = scene.frame_current
    last = _last_frame.get(scene.name)
    _last_frame[scene.name] = frame
    if last is not None and frame == last:
        return
    # sequential forward playback (also frame step / a few dropped frames) simulates;
    # scrubbing, going backwards or jumping resets the masses to rest
    if last is not None and 1 <= frame - last <= 4:
        process_scene(scene, (frame - last) / fps)
    else:
        process_scene(scene, None)


# ---------------------------------------------------------------- on / off
def _register_handler():
    if on_frame_change not in bpy.app.handlers.frame_change_post:
        bpy.app.handlers.frame_change_post.append(on_frame_change)


def _unregister_handler():
    while on_frame_change in bpy.app.handlers.frame_change_post:
        bpy.app.handlers.frame_change_post.remove(on_frame_change)


def set_preview(scene, on):
    """Turn the live simulation on or off. Off removes the handler and restores the pose."""
    if on:
        _register_handler()
        _last_frame.pop(scene.name, None)
        process_scene(scene, None)
    else:
        if not any(s.inertial_bone.preview for s in bpy.data.scenes):
            _unregister_handler()
            clear_all()
        else:
            for key in list(STATE):
                _restore(key)
            _last_frame.pop(scene.name, None)
    bpy.context.view_layer.update()


def reset(scene):
    _last_frame.pop(scene.name, None)
    if scene.inertial_bone.preview:
        process_scene(scene, None)
        bpy.context.view_layer.update()


@persistent
def on_load_post(*_args):
    """Handlers that are not persistent are dropped on file load: re-arm the preview if the file saved it on."""
    STATE.clear()
    ORIGINAL.clear()
    _last_frame.clear()
    _unregister_handler()
    if any(s.inertial_bone.preview for s in bpy.data.scenes):
        _register_handler()


def register():
    if on_load_post not in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.append(on_load_post)


def unregister():
    for s in bpy.data.scenes:
        if hasattr(s, "inertial_bone"):
            s.inertial_bone.preview = False
    _unregister_handler()
    clear_all()
    while on_load_post in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.remove(on_load_post)
