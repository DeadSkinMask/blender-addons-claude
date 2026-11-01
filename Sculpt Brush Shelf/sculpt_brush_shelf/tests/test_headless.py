# Run via ../../run_tests.sh. Installs the ZIP as an extension into a temporary
# repo, then exercises it. Exit code is non-zero if any mandatory test fails.
import sys
import traceback

import bpy

ZIP = sys.argv[sys.argv.index("--") + 1]
results = []


def record(name, status, detail=""):
    results.append((name, status))
    print("[{}] {} {}".format(status, name, detail))


def check(name, fn):
    try:
        out = fn()
        if out and out[0] == "SKIP":
            record(name, "SKIP", out[1])
        else:
            record(name, "PASS")
    except Exception:
        record(name, "FAIL")
        traceback.print_exc()


print("Blender", bpy.app.version_string, "| Python", sys.version.split()[0])

mod = None
module_name = None


def t_install():
    global mod, module_name
    r = bpy.ops.extensions.package_install_files(
        filepath=ZIP, repo="user_default", enable_on_install=True)
    assert r == {'FINISHED'}, r
    module_name = "bl_ext.user_default.sculpt_brush_shelf"
    mod = sys.modules[module_name]
    assert module_name in bpy.context.preferences.addons


def t_available():
    names = mod.available_brushes()
    print("   available sculpt brushes:", len(names))
    assert len(names) > 20
    assert len(bpy.data.brushes) == 0, "bpy.data.brushes is expected to be empty at startup"


def t_defaults():
    prefs = mod.get_prefs()
    assert prefs is not None
    assert mod.shelf_names(prefs) == list(mod.DEFAULT_BRUSHES)
    missing = [n for n in mod.DEFAULT_BRUSHES if mod.asset_identifier(n) is None]
    assert not missing, "default brushes missing in this Blender: {}".format(missing)


def t_scene():
    bpy.ops.mesh.primitive_cube_add()
    assert bpy.context.object is not None


def t_activate_all():
    bpy.ops.object.mode_set(mode='SCULPT')
    assert bpy.context.mode == 'SCULPT'
    ts = bpy.context.tool_settings.sculpt
    for name in mod.DEFAULT_BRUSHES:
        r = bpy.ops.sculpt_brush_shelf.activate(name=name)
        assert r == {'FINISHED'}, (name, r)
        assert ts.brush is not None and ts.brush.name == name, (name, ts.brush and ts.brush.name)
    assert bpy.context.mode == 'SCULPT' and bpy.context.object.type == 'MESH'


def t_missing_brush():
    try:
        bpy.ops.sculpt_brush_shelf.activate(name="Nope Brush")
    except RuntimeError:
        return
    raise AssertionError("expected an error for an unknown brush")


def t_poll_object_mode():
    bpy.ops.object.mode_set(mode='OBJECT')
    assert not bpy.ops.sculpt_brush_shelf.activate.poll()
    bpy.ops.object.mode_set(mode='SCULPT')


def t_edit_list():
    prefs = mod.get_prefs()
    n = len(mod.DEFAULT_BRUSHES)
    assert bpy.ops.sculpt_brush_shelf.remove(index=0) == {'FINISHED'}
    assert mod.shelf_names(prefs)[0] == mod.DEFAULT_BRUSHES[1] and len(mod.shelf_names(prefs)) == n - 1
    assert bpy.ops.sculpt_brush_shelf.move(index=0, direction=1) == {'FINISHED'}
    assert mod.shelf_names(prefs)[1] == mod.DEFAULT_BRUSHES[1]
    assert bpy.ops.sculpt_brush_shelf.add(brush="Draw") == {'FINISHED'}
    assert mod.shelf_names(prefs)[-1] == "Draw"
    prefs.brushes.add().name = "Ghost"
    assert mod.asset_identifier("Ghost") is None
    assert bpy.ops.sculpt_brush_shelf.reset() == {'FINISHED'}
    assert mod.shelf_names(prefs) == list(mod.DEFAULT_BRUSHES)


def t_panel_poll():
    # Panel drawing needs a UI; only the poll logic is checked here.
    assert mod.SCULPTSHELF_PT_panel.poll(bpy.context)
    bpy.ops.object.mode_set(mode='OBJECT')
    assert not mod.SCULPTSHELF_PT_panel.poll(bpy.context)


def t_unregister():
    r = bpy.ops.preferences.addon_disable(module=module_name)
    assert r == {'FINISHED'}, r
    for cls in mod.classes:
        assert not hasattr(bpy.types, cls.__name__), cls.__name__
    assert "activate" not in dir(bpy.ops.sculpt_brush_shelf)
    assert module_name not in bpy.context.preferences.addons


for name, fn in [
    ("install ZIP as extension + register", t_install),
    ("enumerate Essentials sculpt brush assets", t_available),
    ("default configuration resolves", t_defaults),
    ("test scene (mesh + active object)", t_scene),
    ("activate every default brush in Sculpt Mode", t_activate_all),
    ("unknown brush is rejected", t_missing_brush),
    ("operator poll fails outside Sculpt Mode", t_poll_object_mode),
    ("add/remove/move/reset list", t_edit_list),
    ("panel poll (draw needs GUI)", t_panel_poll),
    ("unregister leaves no classes", t_unregister),
]:
    check(name, fn)
    if name.startswith("install") and results[-1][1] == "FAIL":
        break

record("panel drawing / thumbnails / persistence across restarts", "SKIP", "requires GUI; see README manual checklist")

failed = [n for n, s in results if s == "FAIL"]
print("\nSUMMARY: {} PASS, {} FAIL, {} SKIP".format(
    sum(s == "PASS" for _, s in results), len(failed), sum(s == "SKIP" for _, s in results)))
sys.exit(1 if failed else 0)
