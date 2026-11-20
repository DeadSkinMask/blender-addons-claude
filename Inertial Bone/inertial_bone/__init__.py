"""Inertial Bone: live preview in Blender of the translating inertial bone used in Godot (InertialBone3D)."""
from . import props, runtime, operators, ui

_modules = (props, runtime, operators, ui)


def register():
    for m in _modules:
        m.register()


def unregister():
    for m in reversed(_modules):
        m.unregister()
