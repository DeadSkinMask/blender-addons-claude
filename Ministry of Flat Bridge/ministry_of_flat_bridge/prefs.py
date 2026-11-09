# SPDX-License-Identifier: GPL-3.0-or-later
import bpy
from bpy.props import IntProperty, StringProperty

from . import runner


class MoFPreferences(bpy.types.AddonPreferences):
    bl_idname = __package__

    exe_path: StringProperty(name="UnWrapConsole3.exe", subtype='FILE_PATH',
                             description="Path of the Ministry of Flat console unwrapper (or of the folder that contains it)")
    wine_path: StringProperty(name="Wine", subtype='FILE_PATH',
                              description="Wine launcher (Linux/macOS only). Empty: wine64 or wine from the PATH")

    timeout: IntProperty(name="Timeout (s)", default=300, min=0, soft_max=3600,
                         description="Stop the unwrapper after this many seconds (0 = never). A crash under Wine leaves an "
                                     "error dialog open that would otherwise block forever")

    def draw(self, context):
        layout = self.layout
        layout.prop(self, "exe_path")
        layout.prop(self, "timeout")
        if not runner.IS_WINDOWS:
            layout.prop(self, "wine_path")
        try:
            layout.label(text="Unwrapper: " + runner.find_exe(bpy.path.abspath(self.exe_path)), icon='CHECKMARK')
            if not runner.IS_WINDOWS:
                layout.label(text="Wine: " + runner.find_wine(bpy.path.abspath(self.wine_path)), icon='CHECKMARK')
        except runner.EngineError as e:
            layout.label(text=str(e), icon='ERROR')
        layout.operator("mof_bridge.check_engine", icon='PLAY')
        layout.label(text="Ministry of Flat is not bundled: download it from ministryofflat.com.", icon='INFO')


def get_prefs(context=None):
    return (context or bpy.context).preferences.addons[__package__].preferences


def register():
    bpy.utils.register_class(MoFPreferences)


def unregister():
    bpy.utils.unregister_class(MoFPreferences)
