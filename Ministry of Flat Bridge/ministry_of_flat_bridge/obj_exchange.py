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


def coverage(items, resolution, max_grid=2048):
    """Fraction of the UV tile(s) covered by the faces, by rasterizing them (overlapping islands count once).
    `items`: list of (uv (n,2) per corner, loop_start, loop_total). The tiles measured are those the layout touches.
    Returns (fraction, first_tile_u, first_tile_v, tiles_u, tiles_v)."""
    all_uv = np.concatenate([u for u, _, _ in items]) if items else np.zeros((0, 2))
    if len(all_uv) == 0:
        return 0.0, 0, 0, 1, 1
    lo, hi = all_uv.min(axis=0), all_uv.max(axis=0)
    tx0, ty0 = int(np.floor(lo[0] + 1e-6)), int(np.floor(lo[1] + 1e-6))
    ntx, nty = max(1, int(np.floor(hi[0] - 1e-6)) - tx0 + 1), max(1, int(np.floor(hi[1] - 1e-6)) - ty0 + 1)
    n = int(min(resolution, max_grid))
    canvas = np.zeros((nty * n, ntx * n), dtype=bool)
    origin = np.array([tx0, ty0], dtype=np.float64)
    for uv, starts, totals in items:
        starts, totals = np.asarray(starts), np.asarray(totals)
        ntri = totals - 2
        a = np.repeat(starts, ntri)
        grp_first = np.cumsum(ntri) - ntri
        k = np.arange(ntri.sum()) - np.repeat(grp_first, ntri)
        p0, p1, p2 = [(uv[idx] - origin) * n for idx in (a, a + k + 1, a + k + 2)]
        tri = np.stack([p0, p1, p2], axis=1)
        tmin = np.floor(tri.min(axis=1) - 0.0).astype(np.int64)
        tmax = np.floor(tri.max(axis=1)).astype(np.int64)
        for (x0, y0), (x1, y1), t in zip(tmin.tolist(), tmax.tolist(), tri):
            x0, y0 = max(x0, 0), max(y0, 0)
            x1, y1 = min(x1, canvas.shape[1] - 1), min(y1, canvas.shape[0] - 1)
            if x1 < x0 or y1 < y0:
                continue
            xs, ys = np.meshgrid(np.arange(x0, x1 + 1) + 0.5, np.arange(y0, y1 + 1) + 0.5)
            (ax, ay), (bx, by), (cx, cy) = t
            den = (by - cy) * (ax - cx) + (cx - bx) * (ay - cy)
            if abs(den) < 1e-12:
                continue
            w0 = ((by - cy) * (xs - cx) + (cx - bx) * (ys - cy)) / den
            w1 = ((cy - ay) * (xs - cx) + (ax - cx) * (ys - cy)) / den
            inside = (w0 >= 0) & (w1 >= 0) & (w0 + w1 <= 1)
            if not inside.any():  # sub-pixel triangle: still mark the pixel it sits in
                inside = np.zeros_like(inside)
                inside[min(int(t[:, 1].mean()) - y0, inside.shape[0] - 1) if y1 > y0 else 0,
                       min(int(t[:, 0].mean()) - x0, inside.shape[1] - 1) if x1 > x0 else 0] = True
            canvas[y0:y1 + 1, x0:x1 + 1] |= inside
    return float(canvas.mean()), tx0, ty0, ntx, nty
