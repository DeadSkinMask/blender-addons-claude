"""Headless tests. Run with run_tests.sh (installs the built ZIP as an extension)."""
import math
import sys
import traceback
import types

import bpy
from mathutils import Matrix, Vector

zip_path = sys.argv[sys.argv.index("--") + 1]
bpy.ops.extensions.package_install_files(filepath=zip_path, repo="user_default", enable_on_install=True)
MOD = "bl_ext.user_default.inertial_bone"


def mod(name):
    import importlib
    return importlib.import_module(f"{MOD}.{name}")


def settings(**kw):
    """A stand-in for InertialBoneSettings with the Godot defaults."""
    d = dict(frequency=(2, 2, 2), damping_ratio=0.3, mass=1.0, damping_pos=(1, 1, 1), damping_neg=(1, 1, 1),
             gravity=(0, 0, -7), max_offset=0.06, max_factor_pos=(1, 1, 1), max_factor_neg=(1, 1, 1),
             progressive=3.0, coupling=20.0, gravity_compensation=0.0)
    d.update(kw)
    return types.SimpleNamespace(**d)


# character axes for a Blender character facing -Y: right = -X, up = +Z, back = +Y
B_NEG_Y = Matrix(((-1, 0, 0), (0, 0, 1), (0, 1, 0))).transposed()
DT = 1 / 60


def make_rig(rotate_z=0.0):
    bpy.ops.wm.read_factory_settings(use_empty=True)
    import addon_utils
    addon_utils.enable(MOD, default_set=False)
    bpy.ops.object.armature_add(location=(0, 0, 0))
    arm = bpy.context.object
    arm.rotation_euler.z = rotate_z
    bpy.ops.object.mode_set(mode='EDIT')
    eb = arm.data.edit_bones
    root = eb[0]
    root.name = "root"
    root.head, root.tail = (0, 0, 0), (0, 0, 0.3)
    spine = eb.new("spine")
    spine.head, spine.tail, spine.parent = (0, 0, 0.3), (0, 0, 0.6), root
    for name, x in (("Breast_L", 0.1), ("Breast_R", -0.1)):
        b = eb.new(name)
        b.head, b.tail, b.parent, b.roll = (x, -0.1, 0.55), (x, -0.2, 0.55), spine, 0.7
    bpy.ops.object.mode_set(mode='OBJECT')
    return arm


def animate_forward(arm, frames=30, accel=0.01):
    """Move the armature object toward -Y (its front) with constant acceleration."""
    for f in range(1, frames + 1):
        arm.location = (0, -accel * (f - 1) ** 2, 0)
        arm.keyframe_insert("location", frame=f)


def run_frames(scene, start, end, collect):
    for f in range(start, end + 1):
        scene.frame_set(f)
        collect(f)


# ------------------------------------------------------------------ pure simulation
def t_registered():
    make_rig()
    assert hasattr(bpy.types.PoseBone, "inertial") and hasattr(bpy.types.Scene, "inertial_bone")
    s = bpy.context.scene.inertial_bone
    assert s.preview is False


def t_gravity_sag_matches_analytic():
    sim = mod("sim").Sim()
    s = settings(progressive=0.0, max_offset=1.0)
    anchor = Vector((1, 2, 3))
    for _ in range(600):
        sim.step(s, DT, anchor, B_NEG_Y)
    expect = -7.0 / (2 * math.pi * 2.0) ** 2           # g / omega^2, downwards
    assert abs(sim.offset_world.z - expect) < 0.002, (sim.offset_world.z, expect)
    assert abs(sim.offset_world.x) < 1e-4 and abs(sim.offset_world.y) < 1e-4


def t_gravity_compensation_is_a_rest_offset():
    """Drawn offset at rest = (1 - comp) * real sag (progressive stiffness and limits included) and the
    simulated offset does not depend on the compensation."""
    m = mod("sim")
    for prog in (0.0, 3.0):
        for mass in (1.0, 2.0, 20.0):                   # 20: gravity beats the spring, rests on the limit
            sims = []
            for comp in (0.0, 0.5, 1.0):
                sim = m.Sim()
                s = settings(progressive=prog, mass=mass, gravity_compensation=comp)
                for _ in range(600):
                    sim.step(s, DT, Vector((1, 2, 3)), B_NEG_Y)
                shown = sim.offset_world + m.rest_compensation(s, B_NEG_Y)
                sag = sim.offset_world.z
                sims.append(sag)
                assert abs(shown.z - (1.0 - comp) * sag) < 0.001, (prog, mass, comp, sag, shown.z)
            assert max(sims) - min(sims) < 1e-9, (prog, mass, sims)


def t_static_sag_linear_case_matches_analytic():
    m = mod("sim")
    s = settings(progressive=0.0, max_offset=1.0)
    sag = m.static_sag_local(s, B_NEG_Y.transposed())
    assert abs(sag.y - (-7.0 / (2 * math.pi * 2.0) ** 2)) < 1e-7, sag      # character Y = up (float32 vectors)


def t_gravity_compensation_keeps_motion_identical():
    """Compensation is output only: the simulated trajectory is bit-for-bit the same."""
    m = mod("sim")
    runs = []
    for comp in (0.0, 1.0):
        sim = m.Sim()
        s = settings(gravity_compensation=comp)
        for _ in range(300):
            sim.step(s, DT, Vector(), B_NEG_Y)
        tr = []
        for i in range(60):
            sim.step(s, DT, Vector((0, -0.5 * 8.0 * (i * DT) ** 2, 0.3 * math.sin(i * 0.2))), B_NEG_Y)
            tr.append(sim.offset_world.copy())
        runs.append(tr)
    assert all((a - b).length < 1e-12 for a, b in zip(*runs))


def t_gravity_compensation_written_to_pose():
    rt = mod("runtime")
    sm = mod("sim")
    arm = make_rig()
    animate_forward(arm)
    s = arm.pose.bones["Breast_L"].inertial
    s.enabled = True
    s.gravity_compensation = 1.0
    scn = bpy.context.scene
    scn.frame_set(1)
    scn.inertial_bone.preview = True
    pb = arm.pose.bones["Breast_L"]
    worst = 0.0
    for f in range(2, 20):
        scn.frame_set(f)
        sim = rt.STATE[(arm.name, "Breast_L")]
        b = rt.frame_basis(arm, s.front_axis)
        expect = sim.offset_world + sm.rest_compensation(s, b)
        worst = max(worst, (head_minus_anchor_world(arm, pb, rt) - expect).length)
    assert worst < 1e-4, worst
    scn.inertial_bone.preview = False


def t_constant_speed_leaves_no_offset():
    sim = mod("sim").Sim()
    s = settings(gravity=(0, 0, 0))
    a = Vector((0, 0, 0))
    for i in range(300):
        a = Vector((0, -2.0 * i * DT, 0))
        sim.step(s, DT, a, B_NEG_Y)
    assert sim.offset_world.length < 1e-3, sim.offset_world


def t_constant_speed_no_drift_with_substeps():
    """Regression: anchor-at-end-of-substep vs mass-at-start left a constant offset of v*h while walking
    (chest sticking out when going forward, sinking when going backwards)."""
    m = mod("sim")
    for dt in (1 / 24, 1 / 30, 1 / 60, 1 / 144):
        for speed in (1.7, 3.0, -3.0):
            sim = m.Sim()
            s = settings(gravity=(0, 0, 0))
            for i in range(int(6 / dt)):
                sim.step(s, dt, Vector((0, speed * i * dt, 0)), B_NEG_Y)
            assert sim.offset_world.length < 1e-4, (dt, speed, sim.offset_world)


def t_forward_acceleration_lags_toward_back():
    sim = mod("sim").Sim()
    s = settings(gravity=(0, 0, 0), max_offset=1.0)
    peak = 0.0
    for i in range(60):
        a = Vector((0, -0.5 * 8.0 * (i * DT) ** 2, 0))
        sim.step(s, DT, a, B_NEG_Y)
        peak = max(peak, sim.offset_world.y)
    assert peak > 0.005, peak                            # +Y = back = character Z+
    assert sim.offset_world.y > -0.2


def t_asymmetric_limits_and_master():
    m = mod("sim")
    s = settings(gravity=(0, 0, 0), max_offset=0.06, max_factor_pos=(1, 1, 0.2))
    sim = m.Sim()
    peak_back = peak_front = 0.0
    for i in range(240):
        t = i * DT
        y = -3.0 * math.sin(t * 6.0) * 1.0              # violent back-and-forth along the character's front axis
        sim.step(s, DT, Vector((0, y, 0)), B_NEG_Y)
        loc = B_NEG_Y.transposed() @ sim.offset_world    # character coords
        peak_back = max(peak_back, loc.z)
        peak_front = max(peak_front, -loc.z)
    assert peak_back <= 0.06 * 0.2 + 1e-4, peak_back     # +Z (back) limited by master * 0.2
    assert 0.03 < peak_front <= 0.06 + 1e-4, peak_front  # -Z (front) uses the full master
    s.max_offset = 0.03                                  # halving the master halves every limit
    sim = m.Sim()
    peak_front = 0.0
    for i in range(240):
        sim.step(s, DT, Vector((0, -3.0 * math.sin(i * DT * 6.0), 0)), B_NEG_Y)
        peak_front = max(peak_front, -(B_NEG_Y.transposed() @ sim.offset_world).z)
    assert peak_front <= 0.03 + 1e-4, peak_front


def t_coupling_pulls_offsets_together():
    m = mod("sim")
    s0 = settings(gravity=(0, 0, 0), coupling=0.0)
    s1 = settings(gravity=(0, 0, 0), coupling=80.0)
    res = []
    for s in (s0, s1):
        sim = m.Sim()
        sim.step(s, DT, Vector(), B_NEG_Y)               # init
        sim.pos = Vector((0, 0.03, 0))                   # displaced to the back
        partner = Vector()                               # partner stays at rest
        peak = 0.0
        for _ in range(20):
            sim.step(s, DT, Vector(), B_NEG_Y, partner)
            peak = max(peak, abs(sim.offset_world.y))
        res.append(sim.offset_world.length)
    assert res[1] < res[0], res


def t_teleport_resets():
    sim = mod("sim").Sim()
    s = settings()
    sim.step(s, DT, Vector(), B_NEG_Y)
    sim.step(s, DT, Vector((5, 0, 0)), B_NEG_Y)
    assert sim.offset_world.length == 0.0


# ------------------------------------------------------------------ Blender integration
def enable_bones(arm, *names):
    for n in names:
        arm.pose.bones[n].inertial.enabled = True


def head_minus_anchor_world(arm, pb, runtime):
    bpy.context.view_layer.update()
    head = arm.matrix_world @ pb.matrix.translation
    anchor = arm.matrix_world @ runtime.base_matrix(pb).translation
    return head - anchor


def check_writes_match(rotate_z):
    rt = mod("runtime")
    arm = make_rig(rotate_z)
    animate_forward(arm)
    enable_bones(arm, "Breast_L")
    scn = bpy.context.scene
    scn.frame_set(1)
    scn.inertial_bone.preview = True
    pb = arm.pose.bones["Breast_L"]
    worst, peak = 0.0, 0.0

    def collect(f):
        nonlocal worst, peak
        sim = rt.STATE[(arm.name, "Breast_L")]
        real = head_minus_anchor_world(arm, pb, rt)
        worst = max(worst, (real - sim.offset_world).length)
        peak = max(peak, sim.offset_world.length)

    run_frames(scn, 2, 30, collect)
    assert peak > 0.01, f"no motion (peak {peak})"
    assert worst < 1e-4, f"pose offset differs from simulated offset by {worst}"
    scn.inertial_bone.preview = False


def t_pose_matches_simulation_plain():
    check_writes_match(0.0)


def t_pose_matches_simulation_rotated_armature():
    check_writes_match(math.radians(90))


def t_forward_motion_pushes_bone_back_in_world():
    rt = mod("runtime")
    arm = make_rig()
    animate_forward(arm)
    enable_bones(arm, "Breast_L")
    scn = bpy.context.scene
    scn.frame_set(1)
    scn.inertial_bone.preview = True
    ys = []
    run_frames(scn, 2, 12, lambda f: ys.append(rt.STATE[(arm.name, "Breast_L")].offset_world.y))
    assert max(ys) > 0.005, ys                           # character walks toward -Y, mass lags toward +Y
    scn.inertial_bone.preview = False


def t_tilt_is_written_and_capped():
    arm = make_rig()
    animate_forward(arm)
    s = arm.pose.bones["Breast_L"].inertial
    s.max_tilt = 5.0
    s.tilt_from_z = 6.0
    s.tilt_from_y = 6.0
    enable_bones(arm, "Breast_L")
    scn = bpy.context.scene
    scn.frame_set(1)
    scn.inertial_bone.preview = True
    angles = []
    run_frames(scn, 2, 20, lambda f: angles.append(
        math.degrees(arm.pose.bones["Breast_L"].rotation_quaternion.to_axis_angle()[1])))
    assert max(angles) > 0.5, angles
    assert max(angles) <= 5.0 * math.sqrt(2) + 0.1, angles   # two axes, each capped at 5 deg
    scn.inertial_bone.preview = False


def t_scrub_and_backwards_reset():
    rt = mod("runtime")
    arm = make_rig()
    animate_forward(arm)
    enable_bones(arm, "Breast_L")
    scn = bpy.context.scene
    scn.frame_set(1)
    scn.inertial_bone.preview = True
    run_frames(scn, 2, 10, lambda f: None)
    sim = rt.STATE[(arm.name, "Breast_L")]
    assert sim.offset_world.length > 0.005
    scn.frame_set(25)                                     # jump forward
    assert sim.offset_world.length == 0.0, sim.offset_world
    run_frames(scn, 26, 28, lambda f: None)
    assert sim.offset_world.length > 0.0
    scn.frame_set(27)                                     # backwards
    assert sim.offset_world.length == 0.0
    scn.inertial_bone.preview = False


def t_preview_off_removes_handler_and_restores():
    rt = mod("runtime")
    arm = make_rig()
    animate_forward(arm)
    enable_bones(arm, "Breast_L", "Breast_R")
    pb = arm.pose.bones["Breast_L"]
    pb.location = (0.0, 0.0, 0.0)
    scn = bpy.context.scene
    scn.frame_set(1)
    assert rt.on_frame_change not in bpy.app.handlers.frame_change_post
    scn.inertial_bone.preview = True
    assert rt.on_frame_change in bpy.app.handlers.frame_change_post
    run_frames(scn, 2, 12, lambda f: None)
    assert pb.location.length > 0.001, "bone should be displaced while previewing"
    scn.inertial_bone.preview = False
    assert rt.on_frame_change not in bpy.app.handlers.frame_change_post
    assert pb.location.length < 1e-9, pb.location
    assert abs(pb.rotation_quaternion.w - 1.0) < 1e-9
    assert not rt.STATE and not rt.ORIGINAL


def t_disabling_one_bone_restores_it():
    rt = mod("runtime")
    arm = make_rig()
    animate_forward(arm)
    enable_bones(arm, "Breast_L", "Breast_R")
    scn = bpy.context.scene
    scn.frame_set(1)
    scn.inertial_bone.preview = True
    run_frames(scn, 2, 12, lambda f: None)
    assert arm.pose.bones["Breast_R"].location.length > 0.001
    arm.pose.bones["Breast_R"].inertial.enabled = False
    assert arm.pose.bones["Breast_R"].location.length < 1e-9
    assert (arm.name, "Breast_R") not in rt.STATE and (arm.name, "Breast_L") in rt.STATE
    scn.inertial_bone.preview = False


def t_disabled_bones_untouched_while_previewing():
    arm = make_rig()
    animate_forward(arm)
    scn = bpy.context.scene
    scn.frame_set(1)
    scn.inertial_bone.preview = True                      # on, but no bone enabled
    run_frames(scn, 2, 12, lambda f: None)
    assert all(pb.location.length == 0.0 for pb in arm.pose.bones)
    scn.inertial_bone.preview = False


def t_operators():
    arm = make_rig()
    bpy.context.view_layer.objects.active = arm
    bpy.ops.object.mode_set(mode='POSE')
    for pb in arm.pose.bones:
        pb.select = pb.name in ("Breast_L", "Breast_R")
    arm.data.bones.active = arm.data.bones["Breast_L"]
    bpy.ops.inertial_bone.enable_selected(enable=True)
    assert arm.pose.bones["Breast_L"].inertial.enabled and arm.pose.bones["Breast_R"].inertial.enabled
    assert not arm.pose.bones["spine"].inertial.enabled
    bpy.ops.inertial_bone.couple_selected()
    assert arm.pose.bones["Breast_L"].inertial.coupled_bone == "Breast_R"
    assert arm.pose.bones["Breast_R"].inertial.coupled_bone == "Breast_L"
    src = arm.pose.bones["Breast_L"].inertial
    src.frequency = (3, 4, 5)
    src.max_factor_pos = (1, 1, 0.25)
    src.axes_object = arm
    bpy.ops.inertial_bone.copy_settings()
    dst = arm.pose.bones["Breast_R"].inertial
    assert tuple(round(v, 4) for v in dst.frequency) == (3, 4, 5)
    assert abs(dst.max_factor_pos[2] - 0.25) < 1e-6 and dst.axes_object == arm
    assert dst.coupled_bone == "Breast_L", "coupling must be kept"
    bpy.ops.inertial_bone.enable_selected(enable=False)
    assert not src.enabled and not dst.enabled
    bpy.ops.object.mode_set(mode='OBJECT')


def t_coupled_bones_in_blender():
    rt = mod("runtime")
    arm = make_rig()
    animate_forward(arm)
    for n, o in (("Breast_L", "Breast_R"), ("Breast_R", "Breast_L")):
        arm.pose.bones[n].inertial.enabled = True
        arm.pose.bones[n].inertial.coupled_bone = o
    scn = bpy.context.scene
    scn.frame_set(1)
    scn.inertial_bone.preview = True
    run_frames(scn, 2, 15, lambda f: None)
    l = rt.STATE[(arm.name, "Breast_L")].offset_world
    r = rt.STATE[(arm.name, "Breast_R")].offset_world
    assert l.length > 0.001 and r.length > 0.001
    scn.inertial_bone.preview = False


def t_explicit_axes_object_and_front_axis():
    rt = mod("runtime")
    arm = make_rig()
    ref = bpy.data.objects.new("ref", None)
    bpy.context.scene.collection.objects.link(ref)
    ref.rotation_euler.z = math.radians(180)
    bpy.context.view_layer.update()                       # matrix_world is stale until then
    b = rt.frame_basis(ref, 'NEG_Y')
    # a reference rotated 180 deg about Z faces +Y, so "back" points to -Y
    assert (b.col[2] - Vector((0, -1, 0))).length < 1e-6, b
    b2 = rt.frame_basis(arm, 'POS_Y')
    assert (b2.col[0] - Vector((1, 0, 0))).length < 1e-6 and (b2.col[2] - Vector((0, -1, 0))).length < 1e-6
    assert abs(b.determinant() - 1.0) < 1e-6 and abs(b2.determinant() - 1.0) < 1e-6, "must be a proper rotation"


def t_file_roundtrip_rearms_handler():
    import os
    import tempfile
    rt = mod("runtime")
    arm = make_rig()
    enable_bones(arm, "Breast_L")
    bpy.context.scene.inertial_bone.preview = True
    path = os.path.join(tempfile.mkdtemp(), "t.blend")
    bpy.ops.wm.save_as_mainfile(filepath=path)
    bpy.ops.wm.open_mainfile(filepath=path)
    assert bpy.context.scene.inertial_bone.preview is True
    assert rt.on_frame_change in bpy.app.handlers.frame_change_post, "handler must be re-armed after load"
    bpy.context.scene.inertial_bone.preview = False
    bpy.ops.wm.read_factory_settings(use_empty=True)
    assert rt.on_frame_change not in bpy.app.handlers.frame_change_post, "idle file must not keep the handler"


def t_unregister_cleans_up():
    import addon_utils
    rt = mod("runtime")
    arm = make_rig()
    enable_bones(arm, "Breast_L")
    bpy.context.scene.inertial_bone.preview = True
    addon_utils.disable(MOD, default_set=False)
    assert rt.on_frame_change not in bpy.app.handlers.frame_change_post
    assert rt.on_load_post not in bpy.app.handlers.load_post
    assert not hasattr(bpy.types.PoseBone, "inertial")
    addon_utils.enable(MOD, default_set=False)


tests = [t_registered, t_gravity_sag_matches_analytic, t_gravity_compensation_is_a_rest_offset,
         t_static_sag_linear_case_matches_analytic, t_gravity_compensation_keeps_motion_identical,
         t_gravity_compensation_written_to_pose, t_constant_speed_leaves_no_offset,
         t_constant_speed_no_drift_with_substeps, t_forward_acceleration_lags_toward_back, t_asymmetric_limits_and_master,
         t_coupling_pulls_offsets_together, t_teleport_resets,
         t_pose_matches_simulation_plain, t_pose_matches_simulation_rotated_armature,
         t_forward_motion_pushes_bone_back_in_world, t_tilt_is_written_and_capped,
         t_scrub_and_backwards_reset, t_preview_off_removes_handler_and_restores,
         t_disabling_one_bone_restores_it, t_disabled_bones_untouched_while_previewing,
         t_operators, t_coupled_bones_in_blender, t_explicit_axes_object_and_front_axis,
         t_file_roundtrip_rearms_handler, t_unregister_cleans_up]

failed = 0
for t in tests:
    try:
        t()
        print("PASS", t.__name__)
    except Exception:
        failed += 1
        print("FAIL", t.__name__)
        traceback.print_exc()
print(f"{len(tests) - failed}/{len(tests)} passed")
sys.exit(1 if failed else 0)
