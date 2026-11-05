# SPDX-License-Identifier: GPL-3.0-or-later
"""The binary mesh exchange format with the engine (see ../../FORMAT.md). Pure Python + numpy, no bpy, so it can be
tested anywhere. Little-endian; `RRMI0001` goes to the engine, `RRMO0001` comes back."""

import struct

import numpy as np

MAGIC_IN = b"RRMI0001"
MAGIC_OUT = b"RRMO0001"


def _section(sections, ident, payload):
    sections.append(ident + struct.pack("<I", len(payload)) + payload)


def encode_mesh(positions, face_sizes, indices, colors=None, materials=None, sharp_edges=None, guides=()):
    """positions (V, 3) float; face_sizes (F,) and indices (I,) integers; colors (V, 3); materials (F,);
    sharp_edges (E, 2) vertex pairs; guides: iterable of (closed, stroke, (N, 3) points)."""
    positions = np.ascontiguousarray(positions, dtype="<f4").reshape(-1, 3)
    face_sizes = np.ascontiguousarray(face_sizes, dtype="<u4")
    indices = np.ascontiguousarray(indices, dtype="<u4")
    out = [MAGIC_IN, struct.pack("<III", len(positions), len(face_sizes), len(indices)),
           positions.tobytes(), face_sizes.tobytes(), indices.tobytes()]
    sections = []
    if colors is not None:
        colors = np.ascontiguousarray(colors, dtype="<f4").reshape(-1, 3)
        if len(colors) != len(positions):
            raise ValueError("one color per vertex is needed")
        _section(sections, b"COLR", colors.tobytes())
    if materials is not None:
        materials = np.ascontiguousarray(materials, dtype="<u4")
        if len(materials) != len(face_sizes):
            raise ValueError("one material per face is needed")
        _section(sections, b"MATL", materials.tobytes())
    if sharp_edges is not None and len(sharp_edges):
        _section(sections, b"SHRP", np.ascontiguousarray(sharp_edges, dtype="<u4").reshape(-1, 2).tobytes())
    guides = list(guides)
    if guides:
        payload = [struct.pack("<I", len(guides))]
        for closed, stroke, points in guides:
            points = np.ascontiguousarray(points, dtype="<f4").reshape(-1, 3)
            payload.append(struct.pack("<III", 1 if closed else 0, int(stroke), len(points)))
            payload.append(points.tobytes())
        _section(sections, b"GUID", b"".join(payload))
    out.append(struct.pack("<I", len(sections)))
    out.extend(sections)
    return b"".join(out)


def decode_mesh(data):
    """Returns a dict: positions (V, 3) float32, face_sizes (F,) uint32, indices (I,) uint32 and, when present,
    materials (F,) uint32, colors (V, 3) float32."""
    if data[:8] not in (MAGIC_IN, MAGIC_OUT):
        raise ValueError("not a RustRemesher mesh file (bad magic)")
    nv, nf, ni = struct.unpack_from("<III", data, 8)
    at = 20

    def take(dtype, count):
        nonlocal at
        arr = np.frombuffer(data, dtype=dtype, count=count, offset=at)
        at += arr.nbytes
        return arr

    result = {
        "positions": take("<f4", nv * 3).reshape(-1, 3),
        "face_sizes": take("<u4", nf),
        "indices": take("<u4", ni),
    }
    (sections,) = struct.unpack_from("<I", data, at)
    at += 4
    for _ in range(sections):
        ident = data[at:at + 4]
        (length,) = struct.unpack_from("<I", data, at + 4)
        at += 8
        if ident == b"MATL":
            result["materials"] = np.frombuffer(data, dtype="<u4", count=length // 4, offset=at)
        elif ident == b"COLR":
            result["colors"] = np.frombuffer(data, dtype="<f4", count=length // 4, offset=at).reshape(-1, 3)
        at += length
    return result
