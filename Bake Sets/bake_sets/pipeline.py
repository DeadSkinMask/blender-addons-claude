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


def bake_set(context, bset, gs):
    """Bake the maps of one set. Returns the list of written file paths."""
    errs = validate(bset, gs)
    if errs:
        raise ValueError("; ".join(errs))
    order, wanted = resolve(set(bset.maps))
    folder = output_dir(gs)
    gl_maps.init()
    flip_y = 1.0 if gs.normal_y == 'OPENGL' else -1.0
    images, paths = {}, []
    with TempBake(context, bset, gs) as tb:
        for key in order:
            if MAP_INFO[key]['engine'] == 'CYCLES':
                images[key] = cycles_bake.bake_map(tb, key, "_bs_{}".format(key))
        for key in order:
            if MAP_INFO[key]['engine'] == 'GL':
                images[key] = gl_maps.render(tb, key, images, "_bs_{}".format(key), flip_y)
        for key in wanted:
            paths.append(_save(images[key], key, bset.name, folder))
    return paths
