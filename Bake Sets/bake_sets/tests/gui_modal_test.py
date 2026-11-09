# Opens a real Blender window (needs $DISPLAY): drives the modal Bake operator, records the progress
# labels, then starts a second bake and cancels it with a real Esc key press (xdotool).
import os, subprocess, sys, tempfile, time
import bpy

ZIP, = sys.argv[sys.argv.index("--") + 1:][:1]
OUT = tempfile.mkdtemp()
log = []
print("GUI TEST start")


def fail(msg):
    print("GUI TEST FAIL:", msg)
    bpy.ops.wm.quit_blender()


def setup():
    r = bpy.ops.extensions.package_install_files(filepath=ZIP, repo="user_default", enable_on_install=True)
    mod = sys.modules["bl_ext.user_default.bake_sets"]
    ops = sys.modules["bl_ext.user_default.bake_sets.operators"]
    bpy.ops.mesh.primitive_uv_sphere_add(segments=16, ring_count=8)
    low = bpy.context.active_object
    bpy.ops.mesh.primitive_uv_sphere_add(segments=48, ring_count=24, radius=1.02)
    hi = bpy.context.active_object
    gs = bpy.context.scene.bake_sets
    gs.output_dir, gs.device = OUT, 'CPU'
    s = gs.sets.add(); s.name = "g"; s.low = low; s.highs.add().obj = hi
    s.maps = {'NORMAL', 'AO', 'ALBEDO', 'ORM'}
    s.resolution = '512'
    orig = ops.BAKESETS_OT_bake._status
    def spy(self, context, f):
        log.append((self._label, round(f, 2)))
        return orig(self, context, f)
    ops.BAKESETS_OT_bake._status = spy
    return ops


ops = None
phase = [0]
t0 = [time.time()]


def tick():
    global ops
    if phase[0] == 0:
        ops = setup(); phase[0] = 1
        bpy.ops.bakesets.bake('INVOKE_DEFAULT')
        t0[0] = time.time()
        return 0.2
    if phase[0] == 1:
        if ops.BAKESETS_OT_bake._running:
            if time.time() - t0[0] > 120:
                fail("timeout"); return None
            return 0.2
        files = sorted(os.listdir(OUT))
        print("labels:", [l for l, _ in log])
        print("fractions:", [f for _, f in log])
        fr = [f for _, f in log]
        if fr != sorted(fr) or len(log) < 6: return fail("progress not monotone/too short")
        if files != ["g_ao.png", "g_albedo.png", "g_orm.png", "g_normal.png"] and len(files) != 4:
            return fail("files %s" % files)
        print("modal bake finished OK:", files)
        # second run: cancel with a real Esc
        bpy.context.scene.bake_sets.sets[0].resolution = '2048'
        bpy.context.scene.bake_sets.ao_samples = 128
        log.clear(); phase[0] = 2
        bpy.ops.bakesets.bake('INVOKE_DEFAULT')
        t0[0] = time.time()
        return 1.5
    if phase[0] == 2:
        subprocess.run(["xdotool", "key", "Escape"])
        phase[0] = 3
        return 3.0
    if phase[0] == 3:
        if ops.BAKESETS_OT_bake._running:
            return 1.0
        left = [s.name for s in bpy.data.scenes if s.name.startswith("_bakesets")]
        print("after Esc: running=%s leftover scenes=%s" % (ops.BAKESETS_OT_bake._running, left))
        if left: return fail("temporary scene left after cancelling")
        print("GUI TEST PASS")
        bpy.ops.wm.quit_blender()
        return None


bpy.app.timers.register(tick, first_interval=1.0)
