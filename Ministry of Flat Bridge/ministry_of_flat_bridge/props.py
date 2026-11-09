# SPDX-License-Identifier: GPL-3.0-or-later
import bpy
from bpy.props import BoolProperty, EnumProperty, FloatProperty, IntProperty, PointerProperty, StringProperty


class MoFSettings(bpy.types.PropertyGroup):
    uv_map_name: StringProperty(name="UV Map", default="MoF",
                                description="UV map that receives the result (created, or overwritten if it already exists)")
    combine: BoolProperty(name="Pack Selected Together", default=False,
                          description="Unwrap all selected objects in one run so their islands share the same UV space "
                                      "(world-space sizes are used). Off: each object is unwrapped on its own")
    fit_to_tiles: BoolProperty(name="Fit to 0-1", default=True,
                               description="The tool returns its layout at an arbitrary scale (a cube comes out about 4 units wide). "
                                           "Scale it uniformly (texel density kept) to fit the UV tile(s). Off: raw output. "
                                           "Ignored with World Scale")
    mark_seams: BoolProperty(name="Mark Seams", default=False,
                             description="Replace the object's seams with the edges where the new UV islands are cut")
    resolution: IntProperty(name="Texture Resolution", default=1024, min=16, soft_max=8192,
                            description="Texture size in pixels; sets the gap between islands to prevent bleeding")
    aspect: FloatProperty(name="Pixel Aspect", default=1.0, min=0.01, soft_max=8.0,
                          description="Aspect ratio of the pixels, for non-square textures")
    separate_hard_edges: BoolProperty(name="Separate Hard Edges", default=False,
                                      description="Guarantee that every hard edge is an island border (lightmaps, normal maps)")
    use_normals: BoolProperty(name="Use Normals", default=False,
                              description="Send the corner normals (so Sharp Edges and custom normals) to help classify polygons")
    udims: IntProperty(name="UDIMs", default=1, min=1, soft_max=16,
                       description="Split the model over several UDIM tiles along U. NOTE: UnWrapConsole3 3.7.x ignores it "
                                   "(identical output for 1, 2, 4 and 8 on a 31k-face mesh)")
    overlap_identical: BoolProperty(name="Overlap Identical Parts", default=False,
                                    description="Identical parts share the same texture space")
    overlap_mirrored: BoolProperty(name="Overlap Mirrored Parts", default=False,
                                   description="Mirrored parts share the same texture space")
    world_scale: BoolProperty(name="World Scale", default=False,
                              description="Scale the UVs to the real size of the model (may go beyond the 0-1 range)")
    density: FloatProperty(name="Texture Density", default=1024.0, min=1.0, soft_max=8192.0,
                           description="With World Scale: pixels per unit")


def register():
    bpy.utils.register_class(MoFSettings)
    bpy.types.Scene.mof_bridge = PointerProperty(type=MoFSettings)


def unregister():
    del bpy.types.Scene.mof_bridge
    bpy.utils.unregister_class(MoFSettings)
