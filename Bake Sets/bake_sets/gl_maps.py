# SPDX-License-Identifier: GPL-3.0-or-later
"""Maps derived on the GPU (module gpu) from maps already baked, rendered in UV space.

Curvature, cavity and ORM are image-space passes over a full-screen quad; dust and paint base
draw the low poly in UV space so each texel knows its tangent frame (and therefore the world
normal decoded from the tangent space normal map)."""
import array

import bpy
import gpu
from gpu_extras.batch import batch_for_shader

_counter = [0]


def init():
    # Headless Blender needs this before any GPU call; harmless in the GUI.
    if bpy.app.background and hasattr(gpu, 'init'):
        gpu.init()


def _shader(vsrc, fsrc, attrs, ifaces=(), flats=(), pushes=(), samplers=()):
    info = gpu.types.GPUShaderCreateInfo()
    for i, (t, n) in enumerate(attrs):
        info.vertex_in(i, t, n)
    if ifaces or flats:
        _counter[0] += 1
        iface = gpu.types.GPUStageInterfaceInfo("bs_iface%d" % _counter[0])
        for t, n in ifaces:
            iface.smooth(t, n)
        for t, n in flats:
            iface.flat(t, n)
        info.vertex_out(iface)
    info.fragment_out(0, 'VEC4', 'fragColor')
    for t, n in pushes:
        info.push_constant(t, n)
    for i, n in enumerate(samplers):
        info.sampler(i, 'FLOAT_2D', n)
    info.vertex_source(vsrc)
    info.fragment_source(fsrc)
    return gpu.shader.create_from_info(info)


_QUAD_V = "void main(){ gl_Position = vec4(pos, 0.0, 1.0); }"

_ORM_F = """
void main(){
  ivec2 p = ivec2(gl_FragCoord.xy);
  fragColor = vec4(texelFetch(ao,p,0).r, texelFetch(rough,p,0).r, texelFetch(metal,p,0).r, 1.0);
}"""

_CURV_F = """
vec3 nrm(ivec2 p){
  ivec2 s = textureSize(nmap, 0);
  vec3 n = texelFetch(nmap, clamp(p, ivec2(0), s - 1), 0).rgb * 2.0 - 1.0;
  n.y *= flip_y;
  return n;
}
void main(){
  ivec2 p = ivec2(gl_FragCoord.xy);
  int r = int(radius);
  float k = ((nrm(p + ivec2(r,0)).x - nrm(p - ivec2(r,0)).x) +
             (nrm(p + ivec2(0,r)).y - nrm(p - ivec2(0,r)).y)) * 0.5;
  float v = (mode < 0.5) ? clamp(0.5 + 0.5 * k * strength, 0.0, 1.0)
                         : 1.0 - clamp(-k * strength, 0.0, 1.0);
  fragColor = vec4(vec3(v), 1.0);
}"""

_MESH_V = """
void main(){
  v_n = nrm; v_t = tan.xyz; v_s = tan.w;
  gl_Position = vec4(pos * 2.0 - 1.0, 0.0, 1.0);
}"""

_MESH_F = """
void main(){
  ivec2 p = ivec2(gl_FragCoord.xy);
  vec3 N = normalize(v_n);
  vec3 T = normalize(v_t - N * dot(N, v_t));
  vec3 B = cross(N, T) * v_s;
  vec3 ts = texelFetch(nmap, p, 0).rgb * 2.0 - 1.0;
  ts.y *= flip_y;
  vec3 ln = normalize(T * ts.x + B * ts.y + N * ts.z);
  vec3 wn = normalize(mat3(normal_matrix) * ln);
  float dust = smoothstep(threshold - softness, threshold + softness, wn.z);
  float v = dust;
  if (mode > 0.5) v = mix(texelFetch(ao, p, 0).r, dust, weight);
  fragColor = vec4(vec3(v), 1.0);
}"""

_BENT_F = """
void main(){
  ivec2 p = ivec2(gl_FragCoord.xy);
  vec3 N = normalize(v_n);
  vec3 T = normalize(v_t - N * dot(N, v_t));
  vec3 B = cross(N, T) * v_s;
  float e0 = texelFetch(e0map, p, 0).r;
  vec3 ts = vec3(0.0, 0.0, 1.0);
  if (e0 > 1e-5) {
    vec3 wb = vec3(2.0 * texelFetch(exmap, p, 0).r / e0 - 1.0,
                   2.0 * texelFetch(eymap, p, 0).r / e0 - 1.0,
                   2.0 * texelFetch(ezmap, p, 0).r / e0 - 1.0);
    // world -> object: inverse of the object matrix = transpose of the normal matrix
    vec3 lb = normalize(wb * mat3(normal_matrix));
    ts = normalize(vec3(dot(lb, T), dot(lb, B), dot(lb, N)));
  }
  ts.y *= flip_y;
  fragColor = vec4(ts * 0.5 + 0.5, 1.0);
}"""

_DILATE_F = """
void main(){
  ivec2 p = ivec2(gl_FragCoord.xy);
  vec4 c = texelFetch(src, p, 0);
  if (c.a < 0.5) {
    ivec2 s = textureSize(src, 0);
    for (int dy = -1; dy <= 1; dy++)
      for (int dx = -1; dx <= 1; dx++) {
        vec4 q = texelFetch(src, clamp(p + ivec2(dx, dy), ivec2(0), s - 1), 0);
        if (q.a > 0.5) c = q;
      }
  }
  fragColor = vec4(c.rgb, 1.0);
}"""

_QUAD = {"pos": [(-1, -1), (1, -1), (1, 1), (-1, -1), (1, 1), (-1, 1)]}


def _tex(img):
    """GPU texture from the image's pixels (gpu.texture.from_image crashed right after a Cycles bake)."""
    w, h = img.size
    n = w * h * 4
    arr = array.array('f', bytes(4 * n))
    img.pixels.foreach_get(arr)
    fmt = 'RGBA32F' if img.is_float else 'RGBA8'
    return gpu.types.GPUTexture((w, h), format=fmt, data=gpu.types.Buffer('FLOAT', n, arr))


def _quad_shader(frag, pushes, samplers):
    return _shader(_QUAD_V, frag, [('VEC2', 'pos')], pushes=pushes, samplers=samplers)


def _draw(off, shader, batch, uniforms, samplers):
    with off.bind():
        fb = gpu.state.active_framebuffer_get()
        fb.clear(color=(0.0, 0.0, 0.0, 0.0))
        gpu.state.depth_test_set('NONE')
        gpu.state.blend_set('NONE')
        gpu.state.face_culling_set('NONE')
        shader.bind()
        for n, v in samplers.items():
            shader.uniform_sampler(n, v)
        for n, (kind, v) in uniforms.items():
            if kind == 'f':
                shader.uniform_float(n, v)
        batch.draw(shader)


def _dilate(off, margin, res):
    """Spread the covered texels outward `margin` pixels (ping-pong between two targets)."""
    if margin <= 0:
        return off, None
    sh = _quad_shader(_DILATE_F, (), ["src"])
    batch = batch_for_shader(sh, 'TRIS', _QUAD)
    other = gpu.types.GPUOffScreen(res, res, format='RGBA8')
    a, b = off, other
    for _ in range(margin):
        _draw(b, sh, batch, {}, {"src": a.texture_color})
        a, b = b, a
    return a, other


def _to_image(off, img, res):
    with off.bind():
        fb = gpu.state.active_framebuffer_get()
        buf = fb.read_color(0, 0, res, res, 4, 0, 'FLOAT')
    try:
        img.pixels.foreach_set(buf)
    except (TypeError, ValueError):
        img.pixels.foreach_set([v for row in buf.to_list() for px in row for v in px])
    img.update()


def _frame_batch(tb, uv_name=None):
    """Triangles of the evaluated low poly: UV as position + normal + tangent (w = bitangent sign)."""
    dg = tb.view_layer.depsgraph
    tb.view_layer.update()
    ev = tb.low.evaluated_get(dg)
    me = bpy.data.meshes.new_from_object(ev, depsgraph=dg)
    try:
        layer = me.uv_layers.active
        if layer is None:
            raise RuntimeError("The low poly has no UV map")
        me.calc_tangents(uvmap=layer.name)
        me.calc_loop_triangles()
        nl = len(me.loops)
        uvs = [0.0] * (nl * 2)
        layer.uv.foreach_get('vector', uvs)
        nrm = [0.0] * (nl * 3)
        me.corner_normals.foreach_get('vector', nrm)
        tan = [0.0] * (nl * 3)
        me.loops.foreach_get('tangent', tan)
        sgn = [0.0] * nl
        me.loops.foreach_get('bitangent_sign', sgn)
        tl = [0] * (len(me.loop_triangles) * 3)
        me.loop_triangles.foreach_get('loops', tl)
        pos = [(uvs[2 * i], uvs[2 * i + 1]) for i in tl]
        nn = [(nrm[3 * i], nrm[3 * i + 1], nrm[3 * i + 2]) for i in tl]
        tt = [(tan[3 * i], tan[3 * i + 1], tan[3 * i + 2], sgn[i]) for i in tl]
    finally:
        bpy.data.meshes.remove(me)
    return pos, nn, tt


def render(tb, key, images, name, flip_y):
    """Compute one GL map from `images` (already baked maps) and return it as a new image."""
    from .cycles_bake import new_image
    p = tb.bset
    res = int(p.resolution)
    img = tb.track_image(new_image(name, res, key))
    off = gpu.types.GPUOffScreen(res, res, format='RGBA8')
    keep = []
    try:
        if key == 'ORM':
            sh = _quad_shader(_ORM_F, (), ["ao", "rough", "metal"])
            batch = batch_for_shader(sh, 'TRIS', _QUAD)
            _draw(off, sh, batch, {}, {"ao": _tex(images['AO']), "rough": _tex(images['ROUGHNESS']),
                                       "metal": _tex(images['METALLIC'])})
            final = off
        elif key in ('CURVATURE', 'CAVITY'):
            sh = _quad_shader(_CURV_F, (('FLOAT', 'radius'), ('FLOAT', 'strength'), ('FLOAT', 'mode'),
                                        ('FLOAT', 'flip_y')), ["nmap"])
            batch = batch_for_shader(sh, 'TRIS', _QUAD)
            cav = key == 'CAVITY'
            _draw(off, sh, batch,
                  {"radius": ('f', float(p.cavity_radius if cav else p.curvature_radius)),
                   "strength": ('f', p.cavity_strength if cav else p.curvature_strength),
                   "mode": ('f', 1.0 if cav else 0.0), "flip_y": ('f', flip_y)},
                  {"nmap": _tex(images['NORMAL'])})
            final = off
        elif key == 'BENT_NORMAL':
            sh = _shader(_MESH_V, _BENT_F, [('VEC2', 'pos'), ('VEC3', 'nrm'), ('VEC4', 'tan')],
                         ifaces=[('VEC3', 'v_n'), ('VEC3', 'v_t')], flats=[('FLOAT', 'v_s')],
                         pushes=[('MAT4', 'normal_matrix'), ('FLOAT', 'flip_y')],
                         samplers=["e0map", "exmap", "eymap", "ezmap"])
            pos, nn, tt = _frame_batch(tb)
            batch = batch_for_shader(sh, 'TRIS', {"pos": pos, "nrm": nn, "tan": tt})
            nm = tb.low.matrix_world.to_3x3().inverted_safe().transposed().to_4x4()
            e = images['_bent']
            _draw(off, sh, batch, {"normal_matrix": ('f', nm), "flip_y": ('f', flip_y)},
                  {"e0map": _tex(e[0]), "exmap": _tex(e[1]), "eymap": _tex(e[2]), "ezmap": _tex(e[3])})
            final, spare = _dilate(off, p.margin, res)
            if spare is not None:
                keep.append(spare)
        else:  # DUST, PAINT_BASE
            sh = _shader(_MESH_V, _MESH_F, [('VEC2', 'pos'), ('VEC3', 'nrm'), ('VEC4', 'tan')],
                         ifaces=[('VEC3', 'v_n'), ('VEC3', 'v_t')], flats=[('FLOAT', 'v_s')],
                         pushes=[('MAT4', 'normal_matrix'), ('FLOAT', 'threshold'), ('FLOAT', 'softness'),
                                 ('FLOAT', 'weight'), ('FLOAT', 'mode'), ('FLOAT', 'flip_y')],
                         samplers=["nmap", "ao"])
            pos, nn, tt = _frame_batch(tb)
            batch = batch_for_shader(sh, 'TRIS', {"pos": pos, "nrm": nn, "tan": tt})
            nm = tb.low.matrix_world.to_3x3().inverted_safe().transposed().to_4x4()
            paint = key == 'PAINT_BASE'
            _draw(off, sh, batch,
                  {"normal_matrix": ('f', nm), "threshold": ('f', p.dust_threshold),
                   "softness": ('f', max(p.dust_softness, 1e-4)), "weight": ('f', p.paint_dust_weight),
                   "mode": ('f', 1.0 if paint else 0.0), "flip_y": ('f', flip_y)},
                  {"nmap": _tex(images['NORMAL']), "ao": _tex(images['AO'] if paint else images['NORMAL'])})
            final, spare = _dilate(off, p.margin, res)
            if spare is not None:
                keep.append(spare)
        _to_image(final, img, res)
    finally:
        off.free()
        for k in keep:
            k.free()
    return img
