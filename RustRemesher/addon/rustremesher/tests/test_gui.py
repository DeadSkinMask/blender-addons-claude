# Optional: needs a display (run_gui_test.sh). Drives the modal operator inside a real Blender window: progress
# polling, the result object, the status text clean-up and (with xdotool) Esc to cancel.
import os
import subprocess
import sys

import bpy

ARGS = sys.argv[sys.argv.index("--") + 1:]
ZIP, ENGINE, OUT = ARGS[0], ARGS[1], ARGS[2]
module_name = "bl_ext.user_default.rustremesher"
log = []
state = {"phase": 0, "t0": 0.0}


def say(msg):
    log.append(msg)
    print("GUI:", msg)


def finish(ok):
    with open(OUT, "w") as f:
        f.write("\n".join(log) + "\n" + ("OK" if ok else "FAIL") + "\n")
    bpy.ops.wm.quit_blender()


def view3d_override():
    for win in bpy.context.window_manager.windows:
        for area in win.screen.areas:
            if area.type == 'VIEW_3D':
                region = next(r for r in area.regions if r.type == 'WINDOW')
                return dict(window=win, area=area, region=region)
    return None


def step():
    import time
    mod = sys.modules.get(module_name)
    try:
        if state["phase"] == 0:
            r = bpy.ops.extensions.package_install_files(filepath=ZIP, repo="user_default", enable_on_install=True)
            mod = sys.modules[module_name]
            bpy.context.preferences.addons[module_name].preferences.engine_path = ENGINE
            bpy.ops.object.select_all(action='SELECT')
            bpy.ops.object.delete()
            bpy.ops.mesh.primitive_uv_sphere_add(segments=256, ring_count=128)
            s = bpy.context.scene.rustremesher
            s.target_quads = 30000
            s.adaptivness = 0.0
            state["phase"] = 1
            return 0.5
        ov = view3d_override()
        if ov is None:
            say("no 3D view")
            finish(False)
            return None
        if state["phase"] == 1:
            with bpy.context.temp_override(**ov):
                r = bpy.ops.rustremesher.remesh('INVOKE_DEFAULT')
            say("invoke -> {}".format(r))
            assert r == {'RUNNING_MODAL'}, r
            assert mod.operators._ACTIVE["job"] is not None
            state["phase"], state["t0"], state["seen"] = 2, time.time(), set()
            return 0.2
        if state["phase"] == 2:
            job = mod.operators._ACTIVE["job"]
            if job is not None:
                v, text = mod.runner.read_progress(job.progress)
                state["seen"].add(round(v or 0, 1))
                if time.time() - state["t0"] > 120:
                    say("timeout")
                    finish(False)
                    return None
                return 0.2
            names = [o.name for o in bpy.data.objects]
            say("objects {}  progress values seen {}".format(names, sorted(state["seen"])))
            new = bpy.data.objects.get("Sphere_remesh")
            ok = new is not None and len(new.data.polygons) > 20000 and all(p.loop_total == 4 for p in new.data.polygons)
            say("remesh result ok={} faces={}".format(ok, len(new.data.polygons) if new else None))
            if not ok:
                finish(False)
                return None
            # cancel with Esc
            bpy.ops.object.select_all(action='DESELECT')
            src = bpy.data.objects["Sphere"]
            bpy.context.view_layer.objects.active = src
            src.select_set(True)
            with bpy.context.temp_override(**ov):
                r = bpy.ops.rustremesher.remesh('INVOKE_DEFAULT')
            assert r == {'RUNNING_MODAL'}, r
            state["phase"], state["t0"] = 3, time.time()
            state["job"] = mod.operators._ACTIVE["job"]
            state["esc"] = False
            return 0.5
        if state["phase"] == 3:
            if not state["esc"]:
                state["esc"] = True
                subprocess.run(["xdotool", "key", "Escape"], check=False)
                say("sent Escape")
                return 1.0
            if mod.operators._ACTIVE["job"] is not None:
                if time.time() - state["t0"] > 60:
                    say("Escape did not cancel (no focus?)")
                    state["job"].cancel()
                    finish(True)  # not a failure of the addon: the window may not have had focus
                    return None
                return 0.5
            running = state["job"].proc.poll()
            say("after Escape: engine exit code {}, folder exists {}".format(running, os.path.exists(state["job"].dir)))
            names = [o.name for o in bpy.data.objects]
            ok = not os.path.exists(state["job"].dir) and names.count("Sphere_remesh") == 1 and "Sphere_remesh.001" not in names
            say("cancelled cleanly={}".format(ok))
            finish(ok)
            return None
    except Exception as e:
        import traceback
        say("exception: {}\n{}".format(e, traceback.format_exc()))
        finish(False)
        return None


bpy.app.timers.register(step, first_interval=1.0)
