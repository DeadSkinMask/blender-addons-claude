"""Blender → Godot coordinates: (x, y, z) → (x, z, -y), matrices as C · M · C⁻¹."""

from mathutils import Matrix, Vector

# Rows map Blender axes to Godot axes.
C = Matrix(((1, 0, 0, 0), (0, 0, 1, 0), (0, -1, 0, 0), (0, 0, 0, 1)))
C_INV = C.inverted()

DECIMALS = 5


def num(value):
    """Rounded float for the manifest (stable text, no -0.0)."""
    value = round(float(value), DECIMALS)
    return 0.0 if value == 0 else value


def vec(v):
    """Godot-space list for a Blender vector or size triple."""
    return [num(v[0]), num(v[2]), num(-v[1])]


def size(v):
    """Godot-space extents (no sign flip) for a Blender (dx, dy, dz)."""
    return [num(v[0]), num(v[2]), num(v[1])]


def matrix(m):
    """Godot transform as 12 floats: basis columns x, y, z, then origin."""
    g = C @ m @ C_INV
    return [num(g[r][c]) for c in range(3) for r in range(3)] + [num(g[r][3]) for r in range(3)]


def without_scale(m):
    """World matrix with the scale removed (location + rotation only)."""
    loc, rot, _scale = m.decompose()
    return Matrix.LocRotScale(loc, rot, Vector((1, 1, 1)))
