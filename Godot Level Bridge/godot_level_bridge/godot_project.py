"""What the bridge reads from the Godot project: physics layer names in project.godot."""

import os
import re

_LAYER_RE = re.compile(r'^3d_physics/layer_(\d+)="(.*)"\s*$')
_cache = {}


def layer_names(project_root):
    """{layer number: name} from [layer_names] in project.godot (cached by mtime)."""
    path = os.path.join(project_root, "project.godot")
    try:
        mtime = os.path.getmtime(path)
    except OSError:
        return {}
    cached = _cache.get(path)
    if cached and cached[0] == mtime:
        return cached[1]
    names = {}
    section = None
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line.startswith("["):
                section = line
                continue
            if section == "[layer_names]":
                m = _LAYER_RE.match(line)
                if m:
                    names[int(m.group(1))] = m.group(2)
    _cache[path] = (mtime, names)
    return names


def layer_number(project_root, name):
    for number, layer_name in layer_names(project_root).items():
        if layer_name == name:
            return number
    return None
