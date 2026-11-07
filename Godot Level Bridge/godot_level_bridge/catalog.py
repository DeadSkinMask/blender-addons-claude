"""Scene catalog written by Godot (gbridge-catalog, FORMAT.md §9) and what Blender does with it:
place entities with their typed default props, check props, and the volume box
drawn in the viewport.

The catalog is <Godot project>/bridge_catalog.json; it is cached by mtime.
"""

import colorsys
import hashlib
import json
import os

from mathutils import Vector

FORMAT = "gbridge-catalog"
VERSION = 1
FILENAME = "bridge_catalog.json"

# Godot type → Python types a custom property may hold for it.
_ACCEPTED = {
    "bool": (bool, int),
    "int": (int,),
    "float": (float, int),
    "string": (str,),
    "string_name": (str,),
    "node_path": (str,),  # or an Object pointer (→ $ref), checked apart
    "resource": (str,),
}
_VECTOR_SIZES = {"vector2": 2, "vector3": 3, "vector4": 4, "color": 4}

_cache = {"path": None, "mtime": None, "data": None, "error": None}


class CatalogError(Exception):
    """The catalog file exists but cannot be used."""


def path_for(project_root):
    return os.path.join(project_root, FILENAME)


def load(project_root):
    """The catalog dict, or None if the project has none. CatalogError if invalid."""
    path = path_for(project_root)
    if not os.path.isfile(path):
        _cache.update(path=path, mtime=None, data=None, error=None)
        return None
    mtime = os.path.getmtime(path)
    if _cache["path"] == path and _cache["mtime"] == mtime:
        if _cache["error"]:
            raise CatalogError(_cache["error"])
        return _cache["data"]
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict) or data.get("format") != FORMAT:
            raise CatalogError("{} is not a gbridge-catalog".format(path))
        if int(data.get("version", 0)) > VERSION:
            raise CatalogError("catalog version {} is newer than this add-on ({})".format(data["version"], VERSION))
        data.setdefault("scenes", [])
    except (OSError, ValueError, CatalogError) as e:
        _cache.update(path=path, mtime=mtime, data=None, error=str(e))
        raise CatalogError(str(e))
    _cache.update(path=path, mtime=mtime, data=data, error=None)
    return data


def clear_cache():
    _cache.update(path=None, mtime=None, data=None, error=None)


def try_load(project_root):
    """Like load() but None on any problem (for drawing and panels)."""
    if not project_root:
        return None
    try:
        return load(project_root)
    except CatalogError:
        return None


def entry_for(data, scene_path):
    if not data or not scene_path:
        return None
    for entry in data["scenes"]:
        if entry["scene"] == scene_path:
            return entry
    return None


def label(entry):
    return "{}/{}".format(entry["category"], entry["name"]) if entry.get("category") else entry["name"]


def category_color(category):
    """Stable, saturated color for a category (RGBA)."""
    hue = int(hashlib.md5(category.encode("utf-8")).hexdigest()[:6], 16) / 0xFFFFFF
    r, g, b = colorsys.hsv_to_rgb(hue, 0.65, 1.0)
    return (r, g, b, 1.0)


# --- Volume ---

def blender_box(entry):
    """(min, max) of the scene's AABB in Blender space (object local), or None if empty.

    Godot (x, y, z) → Blender (x, -z, y)."""
    aabb = entry.get("aabb") or {}
    pos, size = aabb.get("position"), aabb.get("size")
    if not pos or not size or all(abs(v) < 1e-6 for v in size):
        return None
    lo = Vector((pos[0], -(pos[2] + size[2]), pos[1]))
    hi = Vector((pos[0] + size[0], -pos[2], pos[1] + size[1]))
    return lo, hi


def box_edges(lo, hi):
    """The 12 edges of a box as 24 points (pairs), for a LINES batch."""
    c = [Vector((x, y, z)) for x in (lo.x, hi.x) for y in (lo.y, hi.y) for z in (lo.z, hi.z)]
    pairs = [(0, 1), (2, 3), (4, 5), (6, 7), (0, 2), (1, 3), (4, 6), (5, 7), (0, 4), (1, 5), (2, 6), (3, 7)]
    return [c[i] for pair in pairs for i in pair]


# --- Props ---

def default_value(prop):
    """Python value for the custom property of a catalog prop, or None to skip it
    (a node_path or resource without a default cannot be written as a default)."""
    kind, value = prop.get("type"), prop.get("default")
    if kind == "bool":
        return bool(value)
    if kind == "int":
        return int(value or 0)
    if kind == "float":
        return float(value or 0.0)
    if kind in ("string", "string_name"):
        return str(value or "")
    if kind in _VECTOR_SIZES:
        return [float(v) for v in (value or [0.0] * _VECTOR_SIZES[kind])]
    if kind in ("node_path", "resource"):
        return str(value) if value else None
    return None


def _ui_settings(prop):
    """Keyword arguments for id_properties_ui(...).update()."""
    kind = prop.get("type")
    settings = {}
    if prop.get("enum"):
        options = ", ".join("{}={}".format(name, value) for name, value in prop["enum"])
        settings["description"] = "Enum: " + options
        values = [value for _name, value in prop["enum"]]
        settings["min"], settings["max"] = min(values), max(values)
    elif prop.get("hint") == "range" and kind in ("int", "float"):
        parts = [p.strip() for p in prop.get("hint_string", "").split(",")]
        try:
            cast = int if kind == "int" else float
            settings["min"], settings["max"] = cast(float(parts[0])), cast(float(parts[1]))
            if len(parts) > 2 and kind == "float":
                settings["step"] = float(parts[2])
        except (ValueError, IndexError):
            pass
    elif prop.get("hint") in ("file", "dir"):
        settings["description"] = "Godot path ({} {})".format(prop["hint"], prop.get("hint_string", "")).strip()
    if kind == "color":
        settings["subtype"] = 'COLOR_GAMMA'
        settings["min"], settings["max"] = 0.0, 1.0
    elif kind == "vector3":
        settings["subtype"] = 'XYZ'
    if "description" not in settings:
        settings["description"] = "Godot @export {}".format(kind)
    return settings


def apply_props(obj, entry, overwrite=False):
    """Write the scene's @export defaults as custom properties. Existing ones are
    kept unless `overwrite`. Returns the names written."""
    written = []
    for prop in entry.get("props", []):
        name = prop["name"]
        if name in obj.keys() and not overwrite:
            continue
        value = default_value(prop)
        if value is None:
            continue
        obj[name] = value
        try:
            obj.id_properties_ui(name).update(**_ui_settings(prop))
        except (TypeError, ValueError):
            pass
        written.append(name)
    return written


def check_props(obj, entry, prop_items):
    """Messages about custom properties that do not match the scene's @export."""
    import bpy
    by_name = {p["name"]: p for p in entry.get("props", [])}
    messages = []
    for key, value in prop_items:
        prop = by_name.get(key)
        if prop is None:
            messages.append("property '{}' is not an @export of {}".format(key, entry["name"]))
            continue
        kind = prop.get("type")
        if kind == "node_path" and isinstance(value, bpy.types.Object):
            continue
        if kind in _VECTOR_SIZES:
            ok = hasattr(value, "__len__") and not isinstance(value, str) and len(value) == _VECTOR_SIZES[kind]
        else:
            accepted = _ACCEPTED.get(kind)
            ok = accepted is None or (isinstance(value, accepted) and not (kind == "int" and isinstance(value, bool)))
        if not ok:
            messages.append("property '{}' should be {} for {}".format(key, kind, entry["name"]))
    return messages
