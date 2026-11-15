import sys, bpy, bmesh, traceback
zip_path = sys.argv[sys.argv.index("--") + 1]
bpy.ops.extensions.package_install_files(filepath=zip_path, repo="user_default", enable_on_install=True)
M = ".sculpt_mask"

def setup(n=1):
    bpy.ops.wm.read_factory_settings(use_empty=True)
    import addon_utils; addon_utils.enable('bl_ext.user_default.selection_to_mask', default_set=False)
    objs = []
    for i in range(n):
        bpy.ops.mesh.primitive_uv_sphere_add(location=(i * 3, 0, 0))
        objs.append(bpy.context.object)
    return objs

def select_top(o):
    bm = bmesh.from_edit_mesh(o.data)
    for v in bm.verts: v.select_set(v.co.z > 0)
    bm.select_flush_mode(); bmesh.update_edit_mesh(o.data)

def mask(o):
    bpy.ops.object.mode_set(mode='OBJECT')
    a = o.data.attributes.get(M)
    vals = [d.value for d in a.data]
    z = [v.co.z for v in o.data.vertices]
    return vals, z

def t_default_masks_unselected():
    o, = setup(); bpy.ops.object.mode_set(mode='EDIT'); select_top(o)
    bpy.ops.mask_selection.apply()
    assert bpy.context.object.mode == 'EDIT', "mode must not change"
    vals, z = mask(o)
    assert all((m == 0.0) == (zz > 0) for m, zz in zip(vals, z))

def t_selected_mode_and_keep():
    o, = setup(); bpy.ops.object.mode_set(mode='EDIT'); select_top(o)
    bpy.ops.mask_selection.apply(invert=False)
    bpy.ops.mask_selection.apply(invert=True, keep_existing=True)
    vals, _ = mask(o)
    assert all(v == 1.0 for v in vals)

def t_clear_and_multi():
    a, b = setup(2)
    bpy.ops.object.select_all(action='SELECT')
    bpy.ops.object.mode_set(mode='EDIT')
    bpy.ops.mesh.select_all(action='SELECT')
    bpy.ops.mask_selection.apply(invert=False)
    bpy.ops.object.mode_set(mode='OBJECT')
    assert a.data.attributes.get(M) and b.data.attributes.get(M)
    bpy.ops.object.mode_set(mode='EDIT')
    bpy.ops.mask_selection.clear()
    bpy.ops.object.mode_set(mode='OBJECT')
    assert not a.data.attributes.get(M) and not b.data.attributes.get(M)

def t_visible_in_sculpt():
    o, = setup(); bpy.ops.object.mode_set(mode='EDIT'); select_top(o)
    bpy.ops.mask_selection.apply()
    bpy.ops.object.mode_set(mode='SCULPT')
    assert o.data.attributes.get(M)

def t_poll_outside_edit():
    setup()
    assert not bpy.ops.mask_selection.apply.poll()

fails = 0
for f in [t_default_masks_unselected, t_selected_mode_and_keep, t_clear_and_multi, t_visible_in_sculpt, t_poll_outside_edit]:
    try: f(); print("PASS", f.__name__)
    except Exception: fails += 1; print("FAIL", f.__name__); traceback.print_exc()
sys.exit(1 if fails else 0)
