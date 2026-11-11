# SPDX-License-Identifier: GPL-3.0-or-later
"""Maps that need the source geometry or its materials, baked with Cycles.

Everything that reads material values (albedo, roughness, metallic, IDs, thickness) is baked
as EMIT from a temporary emission wired to the wanted socket: the DIFFUSE bake excludes the
glossy component, which turns white metals black."""
import array
import os

import bpy

from .maps import MAP_INFO
from .scene_copy import id_color

_FILL = {'NORMAL': (0.5, 0.5, 1.0, 1.0), 'BENT_NORMAL': (0.5, 0.5, 1.0, 1.0), 'AO': (1.0, 1.0, 1.0, 1.0), 'THICKNESS': (1.0, 1.0, 1.0, 1.0)}
_NATIVE = {'NORMAL': 'NORMAL', 'AO': 'AO'}


def read_existing(path, res):
    """Pixels (RGBA floats) of an existing texture file with the right size, else None."""
    if not path or not os.path.exists(path):
        return None
    img = bpy.data.images.load(path, check_existing=False)
    try:
        if tuple(img.size) != (res, res):
            return None
        arr = array.array('f', bytes(4 * res * res * 4))
        img.pixels.foreach_get(arr)
        return arr
    finally:
        bpy.data.images.remove(img)


def new_image(name, res, key, float_buffer=False, alpha=False, init=None):
    """Bake target. alpha=True: cleared to alpha 0, Cycles sets alpha 1 on every texel it bakes
    (islands + margin). init: pixels of an existing texture to bake on top of."""
    img = bpy.data.images.new(name, res, res, alpha=alpha, float_buffer=float_buffer)
    fill = _FILL.get(key, (0.0, 0.0, 0.0, 1.0))
    img.generated_color = (fill[0], fill[1], fill[2], 0.0 if alpha else 1.0)
    img.colorspace_settings.name = 'sRGB' if MAP_INFO[key]['srgb'] else 'Non-Color'
    if alpha:
        img.alpha_mode = 'STRAIGHT'
    if init is not None:
        img.pixels.foreach_set(init)
    return img


def _find_principled(nt, out):
    """The Principled BSDF feeding the output (searching upstream), or any Principled."""
    seen, stack = set(), [out]
    while stack:
        n = stack.pop()
        if n in seen:
            continue
        seen.add(n)
        if n.type == 'BSDF_PRINCIPLED':
            return n
        for sock in n.inputs:
            for link in sock.links:
                stack.append(link.from_node)
    for n in nt.nodes:
        if n.type == 'BSDF_PRINCIPLED':
            return n
    return None


def _output(nt):
    out = nt.get_output_node('ALL')
    if out is None:
        out = nt.nodes.new('ShaderNodeOutputMaterial')
        out.is_active_output = True
    return out


def _emit_node(tb, mat):
    em = tb.emit_nodes.get(mat)
    if em is None:
        em = mat.node_tree.nodes.new('ShaderNodeEmission')
        tb.emit_nodes[mat] = em
    return em


def _drive(nt, em, sock, fallback):
    """Emission colour <- an input socket of the Principled (link or constant)."""
    for l in list(em.inputs['Color'].links):
        nt.links.remove(l)
    if sock is not None and sock.is_linked:
        nt.links.new(sock.links[0].from_socket, em.inputs['Color'])
        return
    v = sock.default_value if sock is not None else fallback
    if isinstance(v, float):
        v = (v, v, v, 1.0)
    em.inputs['Color'].default_value = (v[0], v[1], v[2], 1.0)


def _wire(tb, mat, key, element_index, p):
    nt = mat.node_tree
    out = _output(nt)
    em = _emit_node(tb, mat)
    pr = _find_principled(nt, out)
    for l in list(em.inputs['Strength'].links):
        nt.links.remove(l)
    em.inputs['Strength'].default_value = 1.0
    if key == 'EMISSION':
        _drive(nt, em, pr.inputs['Emission Color'] if pr else None, (0.0, 0.0, 0.0, 1.0))
        if pr is not None:
            st = pr.inputs['Emission Strength']
            if st.is_linked:
                nt.links.new(st.links[0].from_socket, em.inputs['Strength'])
            else:
                em.inputs['Strength'].default_value = st.default_value
    elif key == 'ALBEDO':
        _drive(nt, em, pr.inputs['Base Color'] if pr else None, tuple(mat.diffuse_color))
    elif key == 'ROUGHNESS':
        _drive(nt, em, pr.inputs['Roughness'] if pr else None, mat.roughness)
    elif key == 'WHITE':
        # plain white diffuse: only the lighting is baked (bent normal probes)
        for l in list(out.inputs['Surface'].links):
            nt.links.remove(l)
        df = nt.nodes.new('ShaderNodeBsdfDiffuse')
        df.inputs['Color'].default_value = (1.0, 1.0, 1.0, 1.0)
        nt.links.new(df.outputs['BSDF'], out.inputs['Surface'])
        return
    elif key == 'METALLIC':
        _drive(nt, em, pr.inputs['Metallic'] if pr else None, mat.metallic)
    elif key == 'MATERIAL_ID':
        _drive(nt, em, None, id_color(tb.material_index(mat)) + (1.0,))
    elif key == 'ELEMENT_ID':
        _drive(nt, em, None, id_color(element_index) + (1.0,))
    elif key == 'THICKNESS':
        for l in list(em.inputs['Color'].links):
            nt.links.remove(l)
        ao = nt.nodes.new('ShaderNodeAmbientOcclusion')
        ao.inside = True
        ao.samples = tb.gs.ao_samples
        ao.inputs['Distance'].default_value = p.thickness_distance
        nt.links.new(ao.outputs['AO'], em.inputs['Color'])
    for l in list(out.inputs['Surface'].links):
        nt.links.remove(l)
    nt.links.new(em.outputs['Emission'], out.inputs['Surface'])


def _attach_target(tb, img):
    for mat in tb.materials[tb.low]:
        node = tb.target_nodes.get(mat)
        if node is None:
            node = mat.node_tree.nodes.new('ShaderNodeTexImage')
            tb.target_nodes[mat] = node
        node.image = img
        mat.node_tree.nodes.active = node
        node.select = True


def _run_bake(tb, img, btype, samples, **extra):
    p, gs, sc, vl = tb.bset, tb.gs, tb.scene, tb.view_layer
    res = int(p.resolution)
    _attach_target(tb, img)
    sc.cycles.samples = samples
    # Hard edges for the flat maps (IDs, albedo...): no pixel filter blending between neighbours.
    sc.cycles.use_adaptive_sampling = False
    sc.cycles.pixel_filter_type = 'BOX'
    sc.cycles.filter_width = 0.01
    if hasattr(sc.cycles, 'use_denoising'):
        sc.cycles.use_denoising = False
    tb.world.light_settings.distance = p.ao_distance
    for o in list(vl.objects):
        o.select_set(False, view_layer=vl)
    if tb.projected:
        for s in tb.sources:
            s.select_set(True, view_layer=vl)
    tb.low.select_set(True, view_layer=vl)
    vl.objects.active = tb.low
    kw = dict(type=btype, width=res, height=res, margin=p.margin, margin_type='ADJACENT_FACES',
              use_selected_to_active=tb.projected, target='IMAGE_TEXTURES', use_clear=False,
              cage_extrusion=p.cage_extrusion, max_ray_distance=p.max_ray_distance)
    kw.update(extra)
    if tb.cage is not None and tb.projected:
        kw.update(use_cage=True, cage_object=tb.cage.name)
    selected = ([tb.low] + list(tb.sources)) if tb.projected else [tb.low]
    with bpy.context.temp_override(scene=sc, view_layer=vl, active_object=tb.low, object=tb.low,
                                   selected_objects=selected, selected_editable_objects=selected):
        r = bpy.ops.object.bake(**kw)
    if r != {'FINISHED'}:
        raise RuntimeError("Cycles bake ({}) failed".format(btype))


def bake_map(tb, key, name, init=None):
    """Bake one Cycles map into a new image and return it."""
    p, gs = tb.bset, tb.gs
    alpha = key == 'ALBEDO' and p.albedo_alpha
    img = tb.track_image(new_image(name, int(p.resolution), key, alpha=alpha, init=init))
    native = _NATIVE.get(key)
    if native is None:
        for i, src in enumerate(tb.sources):
            for mat in tb.materials[src]:
                _wire(tb, mat, key, i, p)
    extra = {}
    if key == 'NORMAL':
        extra = dict(normal_space='TANGENT', normal_r='POS_X',
                     normal_g='POS_Y' if gs.normal_y == 'OPENGL' else 'NEG_Y', normal_b='POS_Z')
    _run_bake(tb, img, native or 'EMIT', gs.ao_samples if key in ('AO', 'THICKNESS') else gs.samples, **extra)
    return img


def bake_bent_probes(tb, name):
    """Four lighting probes for the bent normal, baked as DIFFUSE DIRECT under a temporary world.

    E0 = cosine weighted visibility under a uniform white sky; E_a = the same under a sky whose
    radiance is 0.5 + 0.5 * dot(direction, axis a). Then 2*E_a/E0 - 1 is the cosine weighted mean
    direction of the open sky along that axis (the bent normal, as Godot wants it baked).
    Returns the images [E0, EX, EY, EZ] (float buffers)."""
    p, gs = tb.bset, tb.gs
    res = int(p.resolution)
    for src in tb.sources:
        for mat in tb.materials[src]:
            _wire(tb, mat, 'WHITE', 0, p)
    w = tb.world
    w.use_nodes = True
    nt = w.node_tree
    nt.nodes.clear()
    out = nt.nodes.new('ShaderNodeOutputWorld')
    bg = nt.nodes.new('ShaderNodeBackground')
    nt.links.new(bg.outputs['Background'], out.inputs['Surface'])
    tc = nt.nodes.new('ShaderNodeTexCoord')
    dot = nt.nodes.new('ShaderNodeVectorMath')
    dot.operation = 'DOT_PRODUCT'
    nt.links.new(tc.outputs['Generated'], dot.inputs[0])
    ma = nt.nodes.new('ShaderNodeMath')
    ma.operation = 'MULTIPLY_ADD'
    ma.inputs[1].default_value = 0.5
    ma.inputs[2].default_value = 0.5
    nt.links.new(dot.outputs['Value'], ma.inputs[0])
    imgs = []
    for label, axis in (("E0", None), ("EX", (1, 0, 0)), ("EY", (0, 1, 0)), ("EZ", (0, 0, 1))):
        for l in list(bg.inputs['Color'].links):
            nt.links.remove(l)
        if axis is None:
            bg.inputs['Color'].default_value = (1.0, 1.0, 1.0, 1.0)
        else:
            dot.inputs[1].default_value = axis
            nt.links.new(ma.outputs['Value'], bg.inputs['Color'])
        img = tb.track_image(new_image("{}_{}".format(name, label), res, 'BENT_NORMAL', float_buffer=True))
        img.generated_color = (0.0, 0.0, 0.0, 1.0)
        _run_bake(tb, img, 'DIFFUSE', gs.ao_samples, pass_filter={'DIRECT'})
        imgs.append(img)
    return imgs
