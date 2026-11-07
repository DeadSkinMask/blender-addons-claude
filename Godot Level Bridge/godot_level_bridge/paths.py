"""Godot project paths: locating project.godot and converting disk paths <-> res://."""

import os

import bpy


class PathError(Exception):
    """A path setting is missing or points outside the Godot project."""


def project_root_from_prefs(prefs):
    root = bpy.path.abspath(prefs.godot_project_root) if prefs.godot_project_root else ""
    if not root:
        raise PathError("Set the Godot project folder in the add-on preferences")
    root = os.path.normpath(root)
    if not os.path.isfile(os.path.join(root, "project.godot")):
        raise PathError("No project.godot in {}".format(root))
    return root


def resolve_dir(path):
    """Absolute, normalized folder for a setting that may be relative to the .blend ('//')."""
    if not path:
        raise PathError("Empty output folder")
    if path.startswith("//") and not bpy.data.filepath:
        raise PathError("Save the .blend before exporting: the output folders are relative to it")
    return os.path.normpath(bpy.path.abspath(path))


def to_res(project_root, path):
    """res:// path for a file or folder inside the project; PathError if it is outside."""
    rel = os.path.relpath(os.path.normpath(path), project_root)
    if rel == os.pardir or rel.startswith(os.pardir + os.sep) or os.path.isabs(rel):
        raise PathError("{} is outside the Godot project {}".format(path, project_root))
    return "res://" if rel == os.curdir else "res://" + rel.replace(os.sep, "/")


def from_res(project_root, res_path):
    if not res_path.startswith("res://"):
        raise PathError("{} is not a res:// path".format(res_path))
    return os.path.join(project_root, *res_path[len("res://"):].split("/"))


def describe_dir(project_root, path):
    """One-line status of an output folder setting for the UI: (text, icon)."""
    try:
        folder = resolve_dir(path)
        res = to_res(project_root, folder)
    except PathError as e:
        return str(e), 'ERROR'
    if os.path.isdir(folder):
        return res, 'CHECKMARK'
    return "{} (created on export)".format(res), 'INFO'
