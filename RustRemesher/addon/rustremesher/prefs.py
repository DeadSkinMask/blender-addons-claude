# SPDX-License-Identifier: GPL-3.0-or-later
import bpy
from bpy.props import IntProperty, StringProperty

from . import runner


class RustRemesherPreferences(bpy.types.AddonPreferences):
    bl_idname = __package__

    engine_path: StringProperty(name="Engine", subtype='FILE_PATH',
                                description="Ruta del ejecutable rustremesher (o de la carpeta que lo contiene)")
    threads: IntProperty(name="Hilos", description="Hilos del engine (0 = todos)", default=0, min=0, soft_max=64)

    def draw(self, context):
        layout = self.layout
        layout.prop(self, "engine_path")
        layout.prop(self, "threads")
        try:
            layout.label(text="Engine: " + runner.find_engine(bpy.path.abspath(self.engine_path)), icon='CHECKMARK')
        except runner.EngineError as e:
            layout.label(text=str(e), icon='ERROR')
        layout.operator("rustremesher.check_engine", icon='PLAY')


def get_prefs(context=None):
    return (context or bpy.context).preferences.addons[__package__].preferences


def register():
    bpy.utils.register_class(RustRemesherPreferences)


def unregister():
    bpy.utils.unregister_class(RustRemesherPreferences)
