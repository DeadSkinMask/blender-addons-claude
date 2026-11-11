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
    return os.path.join(output_dir(gs), "{}_{}.png".format(bset.name, MAP_INFO[key]['suffix']))


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
        c = 1.0
    elif MAP_INFO.get(key, {}).get('engine') == 'GL':
        c = 1.0
    else:
        c = gs.samples + 1.0
    return c * area


def total_cost(bset, gs):
    order, wanted = resolve(set(bset.maps))
    c = 1.0  # scene preparation
    for key in order:
        if MAP_INFO[key]['engine'] == 'CYCLES':
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
    order, wanted = resolve(set(bset.maps))
    folder = output_dir(gs)
    gl_maps.init()
    flip_y = 1.0 if gs.normal_y == 'OPENGL' else -1.0
    images, paths = {}, []
    res = int(bset.resolution)

    def existing(key):
        """Pixels to keep when not clearing (None = start from empty)."""
        if bset.clear_before_bake or key not in wanted:
            return None
        return cycles_bake.read_existing(_path(bset, gs, key), res)

    done = 0.0
    yield "Preparing {}".format(bset.name), done
    with TempBake(context, bset, gs) as tb:
        done += 1.0
        for key in order:
            if MAP_INFO[key]['engine'] == 'CYCLES':
                yield "{}: {}".format(bset.name, MAP_INFO[key]['label']), done
                images[key] = cycles_bake.bake_map(tb, key, "_bs_{}".format(key), init=existing(key))
                done += step_cost(key, bset, gs)
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
            paths.append(_save(images[key], key, bset.name, folder))
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
