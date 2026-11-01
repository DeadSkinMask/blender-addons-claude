"""The gbridge-level manifest (contract in FORMAT.md) and how it is written."""

import json
import os

FORMAT = "gbridge-level"
VERSION = 1


def new_manifest(level_name):
    return {
        "format": FORMAT,
        "version": VERSION,
        "level": level_name,
        "static_scene": None,
        "assets": {},
        "entities": [],
    }


def manifest_filename(level_name):
    return level_name + ".level.json"


def dumps(manifest):
    # Deterministic text (no timestamps, stable key order) so an unchanged level
    # produces an identical file and Godot does not reimport anything.
    return json.dumps(manifest, indent=2, ensure_ascii=False) + "\n"


def write_if_changed(path, text):
    """Write text to path unless it already holds exactly that. Returns True if written."""
    if os.path.isfile(path):
        with open(path, encoding="utf-8") as f:
            if f.read() == text:
                return False
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
    return True


def write_bytes_if_changed(path, data):
    """Binary counterpart of write_if_changed. Returns True if written."""
    if os.path.isfile(path):
        with open(path, "rb") as f:
            if f.read() == data:
                return False
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(data)
    return True


def static_filename(level_name):
    return level_name + "_static.glb"
