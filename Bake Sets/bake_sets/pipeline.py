# SPDX-License-Identifier: GPL-3.0-or-later
"""Bake one set: validation, Cycles maps, GL maps, saving."""
import os

import bpy

from . import cycles_bake, gl_maps
from .maps import MAP_INFO, resolve
from .scene_copy import TempBake


def output_dir(gs):
    if gs.output_dir.startswith("//") and not bpy.data.filepath:
        return ""
    return bpy.path.abspath(gs.output_dir)


def prefix(bset):
    """File name prefix of the textures: the Texture Name, or the set name when it is empty."""
    return bset.output_name.strip() or bset.name


def validate(bset, gs):
    """Return a list of human readable problems (empty = ready to bake)."""
    errs = []
    if bset.low is None:
        errs.append("{}: no low poly object".format(bset.name))
    elif bset.low.type != 'MESH':
        errs.append("{}: the low poly must be a mesh".format(bset.name))
    elif not bset.low.data.uv_layers:
        errs.append("{}: the low poly '{}' has no UV map".format(bset.name, bset.low.name))
    for h in bset.highs:
        if h.obj is None:
            errs.append("{}: empty entry in the high poly list".format(bset.name))
        elif h.obj.type != 'MESH':
            errs.append("{}: '{}' is not a mesh".format(bset.name, h.obj.name))
    if not bset.maps:
        errs.append("{}: no maps selected".format(bset.name))
    if not output_dir(gs):
        errs.append("Set an absolute output folder (or save the .blend to use //)")
    return errs


def _path(bset, gs, key):
    return os.path.join(output_dir(gs), "{}_{}.png".format(prefix(bset), MAP_INFO[key]['suffix']))


def _srgb(key):
    return 'sRGB' if MAP_INFO[key]['srgb'] else 'Non-Color'


def _save(img, key, prefix, folder):
    os.makedirs(folder, exist_ok=True)
    name = "{}_{}".format(prefix, MAP_INFO[key]['suffix'])
    path = os.path.join(folder, name + ".png")
    img.filepath_raw = path
    img.file_format = 'PNG'
    img.save()
    old = bpy.data.images.get(name)
    final = bpy.data.images.load(path, check_existing=False)
    if old is not None and old is not final:
        bpy.data.images.remove(old)
    final.name = name
    final.colorspace_settings.name = _srgb(key)
    return path


def step_cost(key, bset, gs):
    """Relative cost of one step, used for the progress fraction and the time estimate."""
    area = (int(bset.resolution) / 1024.0) ** 2
    if key in ('AO', 'THICKNESS'):
        c = gs.ao_samples
    elif key == 'BENT_PROBES':
        c = 4 * gs.ao_samples
    elif key == 'SAVE':
        return 1.0 * area
    elif MAP_INFO.get(key, {}).get('engine') == 'GL':
        return 1.0 * area
    else:
        c = gs.samples + 1.0
    return c * area


def total_cost(bset, gs):
    order, wanted = resolve(set(bset.maps))
    c = 1.0  # scene preparation
    for key in order:
        if MAP_INFO[key]['engine'] == 'CYCLES':
            c += step_cost(key, bset, gs)
            if key == 'ALBEDO' and bset.albedo_alpha:
                c += step_cost(key, bset, gs)
    if 'BENT_NORMAL' in order:
        c += step_cost('BENT_PROBES', bset, gs)
    for key in order:
        if MAP_INFO[key]['engine'] == 'GL':
            c += step_cost(key, bset, gs)
    return c + step_cost('SAVE', bset, gs) * len(wanted)


def bake_set_steps(context, bset, gs):
    """Generator: yields (label, cost_done) *before* each step runs, so a modal operator can show
    what is about to happen and redraw. Closing it early cleans up the temporary scene.
    Returns the list of written paths."""
    errs = validate(bset, gs)
    if errs:
        raise ValueError("; ".join(errs))
    clear = bset.clear_before_bake
    order, wanted = resolve(set(bset.maps))
    folder = output_dir(gs)
    gl_maps.init()
    flip_y = 1.0 if gs.normal_y == 'OPENGL' else -1.0
    images, paths = {}, []
    res = int(bset.resolution)

    def existing(key):
        """Pixels to keep when not clearing (None = start from empty)."""
        if clear or key not in wanted:
            return None
        return cycles_bake.read_existing(_path(bset, gs, key), res)

    def alpha_map():
        """The sources' own alpha, baked once per set (EMIT of the Principled Alpha)."""
        if '_alpha' not in images:
            images['_alpha'] = cycles_bake.bake_map(tb, 'ALPHA', "_bs_alpha")
        return images['_alpha']

    fixed = [False]

    def native_fixes():
        """Cycles' native NORMAL / AO bakes come out multiplied by the transparency of the
        materials they read (AO: the sources', black under a transparent texture, which ORM
        inherits; NORMAL: the self-baked material's, a grey of zero length). Undo that with the
        baked alpha. NOTE: baking the alpha rewires the source materials to emission, so this runs
        after the last native bake and before the first EMIT one."""
        if fixed[0]:
            return
        fixed[0] = True
        if not cycles_bake.sources_have_alpha(tb):
            return
        if 'AO' in images:
            yield "{}: fixing AO on transparent materials".format(bset.name), done
            images['AO'] = gl_maps.fix_ao(tb, images['AO'], alpha_map(), "_bs_AO_fix")
        if 'NORMAL' in images and not tb.projected:
            yield "{}: fixing the normal on transparent materials".format(bset.name), done
            images['NORMAL'] = gl_maps.fix_normal(tb, images['NORMAL'], alpha_map(), "_bs_NORMAL_fix")

    done = 0.0
    yield "Preparing {}".format(bset.name), done
    with TempBake(context, bset, gs) as tb:
        done += 1.0
        for key in order:
            if MAP_INFO[key]['engine'] == 'CYCLES':
                if key not in ('NORMAL', 'AO'):
                    yield from native_fixes()
                yield "{}: {}".format(bset.name, MAP_INFO[key]['label']), done
                images[key] = cycles_bake.bake_map(tb, key, "_bs_{}".format(key), init=existing(key))
                done += step_cost(key, bset, gs)
                if key == 'ALBEDO' and bset.albedo_alpha:
                    # the materials' own transparency (Principled Alpha) goes into the alpha channel
                    yield "{}: Albedo alpha".format(bset.name), done
                    images[key] = gl_maps.combine_alpha(tb, images[key], alpha_map(), "_bs_ALBEDO_a")
                    done += step_cost(key, bset, gs)
        yield from native_fixes()
        if 'BENT_NORMAL' in order:
            yield "{}: Bent Normal (light probes)".format(bset.name), done
            images['_bent'] = cycles_bake.bake_bent_probes(tb, "_bs_bent")
            done += step_cost('BENT_PROBES', bset, gs)
        for key in order:
            if MAP_INFO[key]['engine'] == 'GL':
                yield "{}: {} (GPU)".format(bset.name, MAP_INFO[key]['label']), done
                images[key] = gl_maps.render(tb, key, images, "_bs_{}".format(key), flip_y,
                                             existing=existing(key))
                done += step_cost(key, bset, gs)
        for key in wanted:
            yield "{}: saving {}".format(bset.name, MAP_INFO[key]['label']), done
            paths.append(_save(images[key], key, prefix(bset), folder))
            done += step_cost('SAVE', bset, gs)
    return paths


def bake_set(context, bset, gs):
    """Synchronous version (scripts, tests). Returns the list of written file paths."""
    gen = bake_set_steps(context, bset, gs)
    try:
        while True:
            next(gen)
    except StopIteration as e:
        return e.value
