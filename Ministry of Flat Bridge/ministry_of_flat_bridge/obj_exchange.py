# SPDX-License-Identifier: GPL-3.0-or-later
"""OBJ exchange with the unwrapper (no bpy). The tool keeps vertices, face order and corner order of the input and
only adds `vt` lines and a `v/vt` index to every face corner (checked in the tests), so UVs are matched back by
position in the file."""

import numpy as np


def write_obj(path, parts, use_normals=False):
    """`parts`: list of dicts with `verts` (n,3), `loop_vertex` (nloops,), `loop_start`, `loop_total` and, when
    `use_normals`, `loop_normals` (nloops,3). All parts go in one file, faces in part order."""
    with open(path, "w", encoding="ascii", newline="\n") as f:
        f.write("# Blender export for Ministry of Flat\n")
        offset = 0
        for part in parts:
            np.savetxt(f, np.asarray(part["verts"], dtype=np.float64), fmt="v %.9g %.9g %.9g")
        if use_normals:
            for part in parts:
                np.savetxt(f, np.asarray(part["loop_normals"], dtype=np.float64), fmt="vn %.6g %.6g %.6g")
        noff = 0
        for part in parts:
            idx = (np.asarray(part["loop_vertex"]) + 1 + offset).tolist()
            starts, totals = part["loop_start"].tolist(), part["loop_total"].tolist()
            if use_normals:
                lines = ["f " + " ".join("{0}//{1}".format(idx[i], i + 1 + noff) for i in range(s, s + t))
                         for s, t in zip(starts, totals)]
                noff += len(idx)
            else:
                lines = ["f " + " ".join(map(str, idx[s:s + t])) for s, t in zip(starts, totals)]
            f.write("\n".join(lines) + "\n")
            offset += len(part["verts"])


def read_faces(path):
    """(uvs (n,2) array of the `vt` lines, faces) with faces = list of (vertex indices, uv indices), 0-based."""
    uvs, faces = [], []
    with open(path, encoding="utf-8", errors="replace") as f:
        for line in f:
            if line.startswith("vt "):
                _, u, v = line.split()[:3]
                uvs.append((float(u), float(v)))
            elif line.startswith("f "):
                vs, ts = [], []
                for tok in line.split()[1:]:
                    parts = tok.split("/")
                    vs.append(int(parts[0]) - 1)
                    ts.append(int(parts[1]) - 1 if len(parts) > 1 and parts[1] else -1)
                faces.append((vs, ts))
    return np.array(uvs, dtype=np.float64).reshape(-1, 2), faces


def corner_uvs(uvs, faces, loop_vertex_global, starts_global, totals):
    """UV of every input corner, as an (nloops,2) array in input order. Faces whose vertex order came back rotated or
    reversed are matched by vertex index. Raises ValueError when the tool changed the face structure."""
    if len(faces) != len(starts_global):
        raise ValueError("the unwrapper returned {} faces for {} sent".format(len(faces), len(starts_global)))
    out = np.zeros((len(loop_vertex_global), 2), dtype=np.float64)
    lv = loop_vertex_global.tolist()
    for fi, (s, t) in enumerate(zip(starts_global.tolist(), totals.tolist())):
        vs, ts = faces[fi]
        if len(vs) != t:
            raise ValueError("face {} came back with {} corners instead of {}".format(fi, len(vs), t))
        if min(ts) < 0:
            raise ValueError("face {} came back without UV indices".format(fi))
        if vs == lv[s:s + t]:
            out[s:s + t] = uvs[ts]
        else:
            by_vertex = dict(zip(vs, ts))
            try:
                out[s:s + t] = uvs[[by_vertex[v] for v in lv[s:s + t]]]
            except KeyError:
                raise ValueError("face {} came back with different vertices".format(fi))
    return out


def seam_edge_keys(loop_vertex, loop_start, loop_total, uv, eps=1e-6):
    """Keys (min*N+max, N = max vertex + 1) of the edges whose two faces disagree on the UV of an end point."""
    n = len(loop_vertex)
    nxt = np.arange(n) + 1
    ends = loop_start + loop_total - 1
    nxt[ends] = loop_start
    a, b = loop_vertex, loop_vertex[nxt]
    uva, uvb = uv, uv[nxt]
    swap = a > b
    lo, hi = np.where(swap, b, a), np.where(swap, a, b)
    uvlo, uvhi = np.where(swap[:, None], uvb, uva), np.where(swap[:, None], uva, uvb)
    base = int(loop_vertex.max()) + 1 if n else 1
    key = lo.astype(np.int64) * base + hi
    order = np.argsort(key, kind="stable")
    ks = key[order]
    new_group = np.concatenate(([True], ks[1:] != ks[:-1]))
    first = np.maximum.accumulate(np.where(new_group, np.arange(len(ks)), 0))
    dlo = np.abs(uvlo[order] - uvlo[order][first]).max(axis=1)
    dhi = np.abs(uvhi[order] - uvhi[order][first]).max(axis=1)
    bad = np.maximum(dlo, dhi) > eps
    return np.unique(ks[bad]), base


def fit_to_tiles(uv, udims=1):
    """The tool returns its layout at an arbitrary scale (about 1 unit per cube edge, not 0-1). Scale it uniformly
    (texel density is kept) so that it fits `udims` tiles along U and one along V, assuming the right/top margin
    equals the left/bottom one."""
    if len(uv) == 0:
        return uv
    lo, hi = uv.min(axis=0), uv.max(axis=0)
    width, height = hi[0] + lo[0], hi[1] + lo[1]
    scale = min(udims / width, 1.0 / height)
    return uv * scale
