# SPDX-License-Identifier: GPL-3.0-or-later
"""Bake one set: validation, Cycles maps, GL maps, saving."""
import math
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


def _file(prefix_, key, tile=None):
    """<prefix>_<map>.png, or <prefix>_<map>.<tile>.png for a UDIM tile."""
    mid = ".{}".format(tile) if tile else ""
    return "{}_{}{}.png".format(prefix_, MAP_INFO[key]['suffix'], mid)


def _path(bset, gs, key, tile=None):
    return os.path.join(output_dir(gs), _file(prefix(bset), key, tile))


def tiles_of(context, bset):
    """UDIM tiles (1001, 1002, ...) holding faces of the low poly's active UV map, sorted.
    [1001] when UDIM is off, nothing can be read, or every face is in the 0-1 square."""
    low = bset.low
    if not bset.udim or low is None or low.type != 'MESH' or not low.data.uv_layers:
        return [1001]
    try:
        dg = context.evaluated_depsgraph_get()
        me = bpy.data.meshes.new_from_object(low.evaluated_get(dg), depsgraph=dg)
    except (RuntimeError, ReferenceError):
        me = bpy.data.meshes.new_from_object(low)      # not in this view layer: the base mesh
    try:
        layer = me.uv_layers.active
        if layer is None:
            return [1001]
        me.calc_loop_triangles()
        uv = [0.0] * (2 * len(me.loops))
        layer.uv.foreach_get('vector', uv)
        tri = [0] * (3 * len(me.loop_triangles))
        me.loop_triangles.foreach_get('loops', tri)
        tiles = set()
        for t in range(0, len(tri), 3):
            a, b, c = tri[t], tri[t + 1], tri[t + 2]
            iu = math.floor((uv[2 * a] + uv[2 * b] + uv[2 * c]) / 3.0)
            iv = math.floor((uv[2 * a + 1] + uv[2 * b + 1] + uv[2 * c + 1]) / 3.0)
            if 0 <= iu < 10 and iv >= 0:       # faces outside the UDIM range cannot be baked
                tiles.add(1001 + iu + 10 * iv)
    finally:
        bpy.data.meshes.remove(me)
    return sorted(tiles) or [1001]


def _srgb(key):
    return 'sRGB' if MAP_INFO[key]['srgb'] else 'Non-Color'


def _save(img, key, prefix_, folder, tile=None):
    """Write one map (or one UDIM tile of it). Plain images get their datablock refreshed here;
    UDIM ones are registered once all tiles are written (`_register_tiled`)."""
    os.makedirs(folder, exist_ok=True)
    name = "{}_{}".format(prefix_, MAP_INFO[key]['suffix'])
    path = os.path.join(folder, _file(prefix_, key, tile))
    img.filepath_raw = path
    img.file_format = 'PNG'
    img.save()
    if tile is None:
        old = bpy.data.images.get(name)
        final = bpy.data.images.load(path, check_existing=False)
        if old is not None and old is not final:
            bpy.data.images.remove(old)
        final.name = name
        final.colorspace_settings.name = _srgb(key)
    return path


def _register_tiled(bset, key, folder, tiles):
    """One UDIM image datablock (`<texture>_<map>`) reading the files written for every tile."""
    prefix_ = prefix(bset)
    name = "{}_{}".format(prefix_, MAP_INFO[key]['suffix'])
    res = int(bset.resolution)
    old = bpy.data.images.get(name)
    if old is not None:
        bpy.data.images.remove(old)
    img = bpy.data.images.new(name, res, res, alpha=True, tiled=True)
    for t in tiles:
        if img.tiles.get(t) is None:
            img.tiles.new(tile_number=t)
    if 1001 not in tiles:
        img.tiles.remove(img.tiles.get(1001))
    img.filepath = os.path.join(folder, _file(prefix_, key, "<UDIM>"))
    img.source = 'TILED'
    img.colorspace_settings.name = _srgb(key)
    img.reload()
    return img


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


def total_cost(bset, gs, ntiles=1):
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
    return (c + step_cost('SAVE', bset, gs) * len(wanted)) * max(1, ntiles)


def bake_set_steps(context, bset, gs):
    """Generator: yields (label, cost_done) *before* each step runs, so a modal operator can show
    what is about to happen and redraw. Closing it early cleans up the temporary scene.
    Returns the list of written paths (one per map and UDIM tile)."""
    errs = validate(bset, gs)
    if errs:
        raise ValueError("; ".join(errs))
    clear = bset.clear_before_bake
    order, wanted = resolve(set(bset.maps))
    folder = output_dir(gs)
    gl_maps.init()
    flip_y = 1.0 if gs.normal_y == 'OPENGL' else -1.0
    res = int(bset.resolution)
    tiles = tiles_of(context, bset)
    tiled = tiles != [1001]

    def bake_tile(tile, done):
        """Everything for one UDIM tile (the only one, 1001, without UDIM). Returns (paths, done)."""
        tag = " (UDIM {})".format(tile) if tiled else ""
        file_tile = tile if tiled else None
        images, paths = {}, []

        def existing(key):
            """Pixels to keep when not clearing (None = start from empty)."""
            if clear or key not in wanted:
                return None
            return cycles_bake.read_existing(_path(bset, gs, key, file_tile), res)

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
                yield "{}{}: fixing AO on transparent materials".format(bset.name, tag), done
                images['AO'] = gl_maps.fix_ao(tb, images['AO'], alpha_map(), "_bs_AO_fix")
            if 'NORMAL' in images and not tb.projected:
                yield "{}{}: fixing the normal on transparent materials".format(bset.name, tag), done
                images['NORMAL'] = gl_maps.fix_normal(tb, images['NORMAL'], alpha_map(), "_bs_NORMAL_fix")

        yield "Preparing {}{}".format(bset.name, tag), done
        with TempBake(context, bset, gs, tile) as tb:
            done += 1.0
            for key in order:
                if MAP_INFO[key]['engine'] == 'CYCLES':
                    if key not in ('NORMAL', 'AO'):
                        yield from native_fixes()
                    yield "{}{}: {}".format(bset.name, tag, MAP_INFO[key]['label']), done
                    images[key] = cycles_bake.bake_map(tb, key, "_bs_{}".format(key), init=existing(key))
                    done += step_cost(key, bset, gs)
                    if key == 'ALBEDO' and bset.albedo_alpha:
                        # the materials' own transparency (Principled Alpha) goes into the alpha channel
                        yield "{}{}: Albedo alpha".format(bset.name, tag), done
                        images[key] = gl_maps.combine_alpha(tb, images[key], alpha_map(), "_bs_ALBEDO_a")
                        done += step_cost(key, bset, gs)
            yield from native_fixes()
            if 'BENT_NORMAL' in order:
                yield "{}{}: Bent Normal (light probes)".format(bset.name, tag), done
                images['_bent'] = cycles_bake.bake_bent_probes(tb, "_bs_bent")
                done += step_cost('BENT_PROBES', bset, gs)
            for key in order:
                if MAP_INFO[key]['engine'] == 'GL':
                    yield "{}{}: {} (GPU)".format(bset.name, tag, MAP_INFO[key]['label']), done
                    images[key] = gl_maps.render(tb, key, images, "_bs_{}".format(key), flip_y,
                                                 existing=existing(key))
                    done += step_cost(key, bset, gs)
            for key in wanted:
                yield "{}{}: saving {}".format(bset.name, tag, MAP_INFO[key]['label']), done
                paths.append(_save(images[key], key, prefix(bset), folder, file_tile))
                done += step_cost('SAVE', bset, gs)
        return paths, done

    all_paths, done = [], 0.0
    for tile in tiles:
        tile_paths, done = yield from bake_tile(tile, done)
        all_paths += tile_paths
    if tiled:
        for key in wanted:
            _register_tiled(bset, key, folder, tiles)
    return all_paths


def bake_set(context, bset, gs):
    """Synchronous version (scripts, tests). Returns the list of written file paths."""
    gen = bake_set_steps(context, bset, gs)
    try:
        while True:
            next(gen)
    except StopIteration as e:
        return e.value
