# SPDX-License-Identifier: GPL-3.0-or-later
"""RustRemesher: Blender front end of the local rustremesher quad remeshing engine (see ../../FORMAT.md)."""

from . import guides, operators, prefs, props, ui

MODULES = (props, prefs, guides, operators, ui)


def register():
    for module in MODULES:
        module.register()


def unregister():
    for module in reversed(MODULES):
        module.unregister()
