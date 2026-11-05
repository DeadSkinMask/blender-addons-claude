# SPDX-License-Identifier: GPL-3.0-or-later
"""Blender mesh <-> arrays for the exchange format. Never changes the user's data: the source mesh is only read."""

import numpy as np


def read_mesh(me, color_attribute="", use_materials=True, use_sharp=True):
    """The arrays `exchange.encode_mesh` takes, from a mesh datablock (its own data, modifiers not applied)."""
    nv, nf, ni = len(me.vertices), len(me.polygons), len(me.loops)
    positions = np.empty(nv * 3, dtype=np.float32)
    me.vertices.foreach_get("co", positions)
    sizes = np.empty(nf, dtype=np.int32)
    me.polygons.foreach_get("loop_total", sizes)
    indices = np.empty(ni, dtype=np.int32)
    me.loops.foreach_get("vertex_index", indices)
    out = {"positions": positions.reshape(-1, 3), "face_sizes": sizes, "indices": indices}

    if color_attribute:
        attr = me.color_attributes.get(color_attribute)
        if attr is not None:
            raw = np.empty(len(attr.data) * 4, dtype=np.float32)
            attr.data.foreach_get("color", raw)
            raw = raw.reshape(-1, 4)[:, :3]
            if attr.domain == "POINT":
                out["colors"] = raw
            else:  # per corner: average the corners of each vertex
                total = np.zeros((nv, 3), dtype=np.float64)
                count = np.zeros(nv, dtype=np.float64)
                np.add.at(total, indices, raw)
                np.add.at(count, indices, 1.0)
                out["colors"] = (total / np.maximum(count, 1.0)[:, None]).astype(np.float32)
    if use_materials and len(me.materials) > 0 and nf:
        mats = np.empty(nf, dtype=np.int32)
        me.polygons.foreach_get("material_index", mats)
        out["materials"] = np.maximum(mats, 0)
    if use_sharp and len(me.edges):
        sharp = np.empty(len(me.edges), dtype=bool)
        me.edges.foreach_get("use_edge_sharp", sharp)
        if sharp.any():
            verts = np.empty(len(me.edges) * 2, dtype=np.int32)
            me.edges.foreach_get("vertices", verts)
            out["sharp_edges"] = verts.reshape(-1, 2)[sharp]
    return out


def build_mesh(name, decoded, source_materials=(), smooth=True):
    """A new mesh datablock from `exchange.decode_mesh`'s result; the source's material slots are kept and each face
    gets the material the engine carried over."""
    import bpy
    me = bpy.data.meshes.new(name)
    pos, sizes, idx = decoded["positions"], decoded["face_sizes"], decoded["indices"]
    nf = len(sizes)
    me.vertices.add(len(pos))
    me.loops.add(len(idx))
    me.polygons.add(nf)
    me.vertices.foreach_set("co", np.ascontiguousarray(pos, dtype=np.float32).ravel())
    me.loops.foreach_set("vertex_index", idx.astype(np.int32))
    starts = np.zeros(nf, dtype=np.int32)
    if nf:
        starts[1:] = np.cumsum(sizes.astype(np.int64))[:-1]
    me.polygons.foreach_set("loop_start", starts)
    me.polygons.foreach_set("loop_total", sizes.astype(np.int32))
    me.update(calc_edges=True)
    for mat in source_materials:
        me.materials.append(mat)
    if "materials" in decoded and len(source_materials):
        top = len(source_materials) - 1
        me.polygons.foreach_set("material_index", np.minimum(decoded["materials"], top).astype(np.int32))
    if smooth and nf:
        me.polygons.foreach_set("use_smooth", np.ones(nf, dtype=bool))
    me.update()
    return me
