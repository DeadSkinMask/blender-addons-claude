# Run via ../../run_tests.sh. Installs the ZIP as an extension into a temporary repo, builds a
# scene (one low poly, two high poly objects with different materials), bakes every map and
# checks the pixels. Exit code is non-zero if any test fails.
import os
import sys
import traceback

import bpy

ZIP, OUT = sys.argv[sys.argv.index("--") + 1:][:2]
results = []
RES = 256


def check(name, fn):
    try:
        out = fn()
        if out and out[0] == "SKIP":
            results.append((name, "SKIP"))
            print("[SKIP]", name, out[1])
        else:
            results.append((name, "PASS"))
            print("[PASS]", name)
    except Exception:
        results.append((name, "FAIL"))
        print("[FAIL]", name)
        traceback.print_exc()


print("Blender", bpy.app.version_string, "| Python", sys.version.split()[0])
mod = None
modname = "bl_ext.user_default.bake_sets"
S = {}


def t_install():
    global mod
    r = bpy.ops.extensions.package_install_files(filepath=ZIP, repo="user_default", enable_on_install=True)
    assert r == {'FINISHED'}, r
    mod = sys.modules[modname]
    assert hasattr(bpy.context.scene, "bake_sets")


def _mat(name, base, metallic, rough):
    m = bpy.data.materials.new(name)
    m.use_nodes = True
    b = m.node_tree.nodes["Principled BSDF"]
    b.inputs["Base Color"].default_value = base
    b.inputs["Metallic"].default_value = metallic
    b.inputs["Roughness"].default_value = rough
    return m


def t_scene():
    bpy.ops.mesh.primitive_uv_sphere_add(segments=16, ring_count=8, radius=1.0)
    low = bpy.context.object
    low.name = "low"
    bpy.ops.object.shade_smooth()
    bpy.ops.mesh.primitive_uv_sphere_add(segments=64, ring_count=32, radius=1.02)
    h1 = bpy.context.object
    h1.name = "highA"
    bpy.ops.object.shade_smooth()
    h1.data.materials.append(_mat("white_metal", (1, 1, 1, 1), 1.0, 0.3))
    bpy.ops.mesh.primitive_cube_add(size=0.5, location=(0, 0, 1.0))
    h2 = bpy.context.object
    h2.name = "highB"
    red = _mat("red_paint", (0.8, 0.05, 0.05, 1), 0.0, 0.8)
    pb = red.node_tree.nodes["Principled BSDF"]
    pb.inputs["Emission Color"].default_value = (0, 1, 0, 1)
    pb.inputs["Emission Strength"].default_value = 1.0
    h2.data.materials.append(red)
    S.update(low=low, h1=h1, h2=h2)
    # untouched snapshot of what the addon must not change
    S["snap"] = snapshot()


def snapshot():
    return dict(
        objects=sorted(o.name for o in bpy.data.objects),
        meshes=sorted(m.name for m in bpy.data.meshes),
        materials=sorted(m.name for m in bpy.data.materials),
        scenes=sorted(s.name for s in bpy.data.scenes),
        worlds=sorted(w.name for w in bpy.data.worlds),
        nodes={m.name: len(m.node_tree.nodes) for m in bpy.data.materials if m.node_tree},
        slots={o.name: [s.link for s in o.material_slots] for o in bpy.data.objects},
        matrix={o.name: tuple(round(v, 5) for row in o.matrix_world for v in row) for o in bpy.data.objects},
    )


def configure(name="set", maps=(), highs=("h1", "h2"), folder=None):
    gs = bpy.context.scene.bake_sets
    gs.sets.clear()
    gs.device = 'CPU'
    gs.output_dir = folder or OUT
    s = gs.sets.add()
    s.name = name
    s.low = S["low"]
    for k in highs:
        s.highs.add().obj = S[k]
    s.maps = set(maps)
    s.resolution = str(RES)
    s.margin = 8
    gs.active = 0
    return s


def load(name):
    p = os.path.join(OUT, "set_{}.png".format(name))
    assert os.path.exists(p), p
    img = bpy.data.images["set_" + name]
    img.reload()
    return img


def px(img):
    return img.pixels[:]


def at(pix, x, y, c=None):
    i = (y * RES + x) * 4
    return pix[i:i + 3] if c is None else pix[i + c]


ALL = ('NORMAL', 'AO', 'THICKNESS', 'ALBEDO', 'EMISSION', 'ROUGHNESS', 'METALLIC', 'MATERIAL_ID', 'ELEMENT_ID',
       'CURVATURE', 'CAVITY', 'DUST', 'PAINT_BASE', 'ORM', 'BENT_NORMAL')


def t_errors():
    gs = bpy.context.scene.bake_sets
    s = configure(maps=('NORMAL',))
    s.low = None
    try:
        bpy.ops.bakesets.bake()
        raise AssertionError("expected an error for a missing low poly")
    except RuntimeError as e:
        assert "no low poly" in str(e), e
    s.low = S["low"]
    gs.output_dir = "//x/"
    try:
        bpy.ops.bakesets.bake()
        raise AssertionError("expected an error for the relative folder of an unsaved file")
    except RuntimeError as e:
        assert "output folder" in str(e), e
    gs.output_dir = OUT
    s.maps = set()
    try:
        bpy.ops.bakesets.bake()
        raise AssertionError("expected an error for no maps")
    except RuntimeError as e:
        assert "no maps" in str(e), e


def t_bake_all():
    configure(maps=ALL)
    r = bpy.ops.bakesets.bake()
    assert r == {'FINISHED'}, r
    for k in ('normal', 'ao', 'thickness', 'albedo', 'emission', 'roughness', 'metallic', 'matid', 'elementid',
              'curvature', 'cavity', 'dust', 'paintbase', 'orm', 'bentnormal'):
        assert os.path.exists(os.path.join(OUT, "set_{}.png".format(k))), k


def t_albedo_metal_not_black():
    p = px(load("albedo"))
    n = RES * RES
    white = sum(1 for i in range(n) if min(p[4 * i:4 * i + 3]) > 0.9)
    red = sum(1 for i in range(n) if p[4 * i] > 0.8 and p[4 * i + 1] < 0.5)
    black = sum(1 for i in range(n) if max(p[4 * i:4 * i + 3]) < 0.05)
    print("   white %.0f%% red %.1f%% black %.1f%%" % (100 * white / n, 100 * red / n, 100 * black / n))
    assert white > 0.3 * n, "white metal must stay white"
    assert red > 0.002 * n, "the cube's red must be there (second high poly)"
    assert black < 0.05 * n, "no black metal"


def _distinct(name):
    """Colours covering at least 0.1% of the image (a few edge pixels are blended, which is fine)."""
    import collections
    p = px(load(name))
    c = collections.Counter(tuple(round(v, 1) for v in p[4 * i:4 * i + 3]) for i in range(RES * RES))
    return {k for k, n in c.items() if n >= 0.001 * RES * RES}


def t_emission():
    p = px(load("emission"))
    n = RES * RES
    green = sum(1 for i in range(n) if p[4 * i + 1] > 0.8 and p[4 * i] < 0.3)
    black = sum(1 for i in range(n) if max(p[4 * i:4 * i + 3]) < 0.05)
    print("   green %.1f%% black %.0f%%" % (100 * green / n, 100 * black / n))
    assert green > 0.002 * n, "the cube emits green"
    assert black > 0.9 * n, "the non-emissive white metal must be black"


def t_bent_normal():
    p = px(load("bentnormal"))
    n = RES * RES
    # only look at pixels away from the cube (rows around the equator): open sky -> bent ~ normal
    mid = [i for i in range(n) if abs(i // RES - RES // 2) < RES // 8]
    bz = sum(p[4 * i + 2] for i in mid) / len(mid)
    lens = []
    for i in mid[::7]:
        v = [p[4 * i + c] * 2 - 1 for c in range(3)]
        lens.append(sum(x * x for x in v) ** 0.5)
    dev = max(abs(p[4 * i] - 0.5) for i in range(n))
    print("   bent: mean blue at the equator %.2f, vector length %.2f, max |R-0.5| %.2f" % (bz, sum(lens) / len(lens), dev))
    assert bz > 0.95, "open sky: the bent normal must follow the surface normal"
    assert 0.9 < sum(lens) / len(lens) < 1.1, "bent normals are normalized"
    assert dev > 0.02, "the cube must bend the normals somewhere"


def t_bent_normal_leans_away_from_a_wall():
    # low plane facing +Z with its UV along +X; a tall wall right after its +X edge blocks that side
    # of the sky, so the bent normal must lean towards -X (tangent R < 0.5), more so near the wall.
    try:
        bpy.ops.mesh.primitive_plane_add(size=2.0)
        lowp = bpy.context.object
        lowp.name = "plane_low"
        bpy.ops.mesh.primitive_plane_add(size=2.0)
        hp = bpy.context.object
        hp.name = "plane_high"
        bpy.ops.mesh.primitive_cube_add(size=1.0, location=(1.1, 0, 0.5))
        wall = bpy.context.object
        wall.name = "wall_high"
        wall.scale = (0.2, 2.0, 1.0)
        gs = bpy.context.scene.bake_sets
        gs.sets.clear()
        gs.device = 'CPU'
        gs.output_dir = os.path.join(OUT, "wall")
        st = gs.sets.add()
        st.name = "wall"
        st.low = lowp
        st.highs.add().obj = hp
        st.highs.add().obj = wall
        st.maps = {'BENT_NORMAL'}
        st.resolution = str(RES)
        st.margin = 4
        assert bpy.ops.bakesets.bake() == {'FINISHED'}
        img = bpy.data.images["wall_bentnormal"]
        img.reload()
        p = px(img)
        def mean(c, x0, x1):
            ys = range(RES // 3, 2 * RES // 3)
            xs = range(int(x0 * RES), int(x1 * RES))
            return sum(p[4 * (y * RES + x) + c] for y in ys for x in xs) / (len(ys) * len(xs))
        near, far = mean(0, 0.85, 0.95), mean(0, 0.05, 0.15)
        gmid = mean(1, 0.4, 0.6)
        print("   wall: R near wall %.2f, far %.2f, G in the middle %.2f" % (near, far, gmid))
        assert near < 0.45, "must lean away from the wall"
        assert near < far - 0.03, "leans more near the wall"
        assert 0.45 < gmid < 0.55, "no tilt along the wall"
    finally:
        for o in (lowp, hp, wall):
            bpy.data.objects.remove(o)
        for m in [m for m in bpy.data.meshes if m.users == 0]:
            bpy.data.meshes.remove(m)
        for i in [i for i in bpy.data.images if i.name.startswith("wall_")]:
            bpy.data.images.remove(i)


def t_ids():
    m = _distinct("matid")
    e = _distinct("elementid")
    print("   material colours", len(m), "element colours", len(e))
    assert len(m) == 2, m
    assert len(e) == 2, e


def t_normal():
    p = px(load("normal"))
    n = RES * RES
    bz = sum(p[4 * i + 2] for i in range(n)) / n
    dev = max(abs(p[4 * i] - 0.5) for i in range(n))
    print("   mean blue %.2f max |R-0.5| %.2f" % (bz, dev))
    assert bz > 0.6
    assert dev > 0.1, "the cube must change the normals"


def t_orm_packs_channels():
    o, ao, ro, me = px(load("orm")), px(load("ao")), px(load("roughness")), px(load("metallic"))
    # ORM is built on the GPU from the baked maps: compare in 8-bit steps.
    for i in range(0, RES * RES, 37):
        assert abs(o[4 * i] - ao[4 * i]) < 0.02, ("R", i)
        assert abs(o[4 * i + 1] - ro[4 * i]) < 0.02, ("G", i)
        assert abs(o[4 * i + 2] - me[4 * i]) < 0.02, ("B", i)
    mean = lambda q: sum(q[4 * i] for i in range(RES * RES)) / (RES * RES)
    print("   mean roughness %.2f metallic %.2f ao %.2f" % (mean(ro), mean(me), mean(ao)))
    assert mean(me) > 0.5 and mean(ro) < 0.7


def t_dust_faces_up():
    p = px(load("dust"))
    top = sum(at(p, x, int(RES * 0.9), 0) for x in range(RES)) / RES
    bottom = sum(at(p, x, int(RES * 0.1), 0) for x in range(RES)) / RES
    print("   dust top %.2f bottom %.2f" % (top, bottom))
    assert top > 0.8 and bottom < 0.2


def t_curvature_cavity():
    c, v = px(load("curvature")), px(load("cavity"))
    n = RES * RES
    dev = max(abs(c[4 * i] - 0.5) for i in range(n))
    low = min(v[4 * i] for i in range(n))
    print("   curvature max dev %.2f cavity min %.2f" % (dev, low))
    assert dev > 0.02
    assert low < 0.95


def t_paintbase_between():
    p = px(load("paintbase"))
    top = sum(at(p, x, int(RES * 0.9), 0) for x in range(RES)) / RES
    bottom = sum(at(p, x, int(RES * 0.1), 0) for x in range(RES)) / RES
    print("   paint base top %.2f bottom %.2f" % (top, bottom))
    assert top > bottom


def t_thickness():
    p = px(load("thickness"))
    mid = sum(at(p, x, RES // 2, 0) for x in range(RES)) / RES
    print("   thickness at the equator %.2f (solid sphere, white = thick)" % mid)
    assert mid > 0.8


def t_only_requested_saved():
    folder = os.path.join(OUT, "only_orm")
    configure(name="x", maps=('ORM',), folder=folder)
    assert bpy.ops.bakesets.bake() == {'FINISHED'}
    assert os.listdir(folder) == ["x_orm.png"], os.listdir(folder)


def t_no_leaks_and_user_data_untouched():
    now = snapshot()
    before = S["snap"]
    for k in before:
        assert now[k] == before[k], "{} changed: {} -> {}".format(k, before[k], now[k])
    assert not [i.name for i in bpy.data.images if i.name.startswith("_bs_")], "temporary images left"


def t_self_bake_without_highs():
    S["low"].data.materials.append(_mat("low_metal", (1, 1, 1, 1), 1.0, 0.5))
    configure(name="self", maps=('ALBEDO', 'DUST'), highs=())
    assert bpy.ops.bakesets.bake() == {'FINISHED'}
    p = px(bpy.data.images["self_albedo"])
    n = RES * RES
    white = sum(1 for i in range(n) if min(p[4 * i:4 * i + 3]) > 0.9)
    assert white > 0.5 * n, white
    S["low"].data.materials.clear()
    S["snap"] = snapshot()


def t_second_bake_replaces_image():
    configure(name="again", maps=('AO',))
    bpy.ops.bakesets.bake()
    bpy.ops.bakesets.bake()
    assert len([i for i in bpy.data.images if i.name.startswith("again_ao")]) == 1


def t_progress_steps_and_clean_cancel():
    pipeline = sys.modules[modname + ".pipeline"]
    gs = bpy.context.scene.bake_sets
    st = configure(name="steps", maps=('ORM', 'NORMAL'), folder=os.path.join(OUT, "steps"))
    total = pipeline.total_cost(st, gs)
    gen = pipeline.bake_set_steps(bpy.context, st, gs)
    seen = []
    try:
        while True:
            seen.append(next(gen))
    except StopIteration as e:
        paths = e.value
    labels = [l for l, _ in seen]
    costs = [c for _, c in seen]
    print("   steps:", labels)
    assert costs == sorted(costs) and costs[0] == 0.0
    assert abs(costs[-1] + pipeline.step_cost('SAVE', st, gs) - total) < 1e-6, (costs[-1], total)
    assert labels[0].startswith("Preparing") and any("saving ORM" in l for l in labels)
    assert len(paths) == 2
    # closing the generator half way (what Esc does) must remove every temporary datablock
    before = snapshot()
    nimg = len(bpy.data.images)
    gen = pipeline.bake_set_steps(bpy.context, st, gs)
    next(gen)
    next(gen)   # scene exists now
    assert any(sc.name.startswith("_bakesets") for sc in bpy.data.scenes)
    gen.close()
    assert snapshot() == before
    assert len(bpy.data.images) == nimg, "temporary images left after cancelling"


def t_unregister():
    r = bpy.ops.preferences.addon_disable(module=modname)
    assert r == {'FINISHED'}, r
    assert not hasattr(bpy.types.Scene, "bake_sets")
    assert not hasattr(bpy.types, "BAKESETS_PT_main")
    assert "bake" not in dir(bpy.ops.bakesets)


for name, fn in [
    ("install ZIP as extension + register", t_install),
    ("test scene (low + two high poly)", t_scene),
    ("validation errors are raised", t_errors),
    ("bake every map from two high poly objects", t_bake_all),
    ("albedo: white metal stays white, second high poly present", t_albedo_metal_not_black),
    ("emission: only the emissive high poly is lit", t_emission),
    ("bent normal: follows the normal in the open, bends near the cube", t_bent_normal),
    ("bent normal leans away from a wall (direction check)", t_bent_normal_leans_away_from_a_wall),
    ("material ID / element ID: one colour per material / object", t_ids),
    ("tangent normal reflects the high poly", t_normal),
    ("ORM packs AO/roughness/metallic on the GPU", t_orm_packs_channels),
    ("dust faces up", t_dust_faces_up),
    ("curvature and cavity find the cube's edges", t_curvature_cavity),
    ("paint base follows dust", t_paintbase_between),
    ("thickness is white inside a solid", t_thickness),
    ("only requested maps are saved (dependencies are not)", t_only_requested_saved),
    ("no temporary data left, user data untouched", t_no_leaks_and_user_data_untouched),
    ("bake without high poly (self bake)", t_self_bake_without_highs),
    ("rebaking replaces the image datablock", t_second_bake_replaces_image),
    ("progress steps are monotone; cancelling mid-way leaves nothing", t_progress_steps_and_clean_cancel),
    ("unregister leaves nothing", t_unregister),
]:
    check(name, fn)
    if name.startswith("install") and results[-1][1] == "FAIL":
        break

results.append(("panel drawing", "SKIP"))
print("[SKIP] panel drawing / UI lists: requires GUI; see README manual checklist")
failed = [n for n, s in results if s == "FAIL"]
print("\nSUMMARY: {} PASS, {} FAIL, {} SKIP".format(
    sum(s == "PASS" for _, s in results), len(failed), sum(s == "SKIP" for _, s in results)))
sys.exit(1 if failed else 0)
