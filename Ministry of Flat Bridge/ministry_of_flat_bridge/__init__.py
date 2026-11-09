# SPDX-License-Identifier: GPL-3.0-or-later
"""Ministry of Flat Bridge: sends meshes to the external UnWrapConsole3 tool as OBJ and reads the UVs back."""

from . import operators, prefs, props, ui

MODULES = (props, prefs, operators, ui)


def register():
    for module in MODULES:
        module.register()


def unregister():
    for module in reversed(MODULES):
        module.unregister()
