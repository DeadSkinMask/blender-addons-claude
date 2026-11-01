"""Godot Level Bridge: exports Blender levels to Godot (see ../FORMAT.md)."""

from . import export, props, ui

MODULES = (props, export, ui)


def register():
    for module in MODULES:
        module.register()


def unregister():
    for module in reversed(MODULES):
        module.unregister()
