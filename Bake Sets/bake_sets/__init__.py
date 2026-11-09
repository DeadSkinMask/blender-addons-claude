# SPDX-License-Identifier: GPL-3.0-or-later
import bpy

from . import operators, props, ui

_classes = operators.classes + ui.classes


def register():
    props.register()
    for c in _classes:
        bpy.utils.register_class(c)


def unregister():
    for c in reversed(_classes):
        bpy.utils.unregister_class(c)
    props.unregister()
