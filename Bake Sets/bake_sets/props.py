# SPDX-License-Identifier: GPL-3.0-or-later
import bpy
from bpy.props import (BoolProperty, CollectionProperty, EnumProperty, FloatProperty,
                       IntProperty, PointerProperty, StringProperty)

from .maps import MAP_ITEMS


def _poll_mesh(self, obj):
    return obj.type == 'MESH'


# Settings shared by every set that writes the same texture (same Texture Name, or same set name when
# it is empty). The objects (low, highs, cage), the name and Clear Before Bake stay per set.
SHARED = ('maps', 'resolution', 'margin', 'cage_extrusion', 'max_ray_distance', 'ao_distance', 'thickness_distance', 'curvature_radius', 'curvature_strength', 'cavity_radius', 'cavity_strength', 'dust_threshold', 'dust_softness', 'paint_ground', 'paint_ao_strength', 'albedo_alpha')
_syncing = [False]


def texture_key(s):
    return s.output_name.strip() or s.name


def _others(s):
    gs = bpy.context.scene.bake_sets
    key = texture_key(s)
    return [o for o in gs.sets if o.as_pointer() != s.as_pointer() and texture_key(o) == key]


def _copy_shared(src, dst):
    for n in SHARED:
        setattr(dst, n, getattr(src, n))


def _sync_shared(self, context):
    """Editing a baking setting copies all of them to the other sets of the same texture."""
    if _syncing[0]:
        return
    _syncing[0] = True
    try:
        for o in _others(self):
            _copy_shared(self, o)
    finally:
        _syncing[0] = False


def _join_group(self, context):
    """Giving a set an existing Texture Name makes it adopt that texture's settings."""
    if _syncing[0]:
        return
    others = _others(self)
    if others:
        _syncing[0] = True
        try:
            _copy_shared(others[0], self)
        finally:
            _syncing[0] = False


class BAKESETS_PG_high(bpy.types.PropertyGroup):
    obj: PointerProperty(name="Object", type=bpy.types.Object, poll=_poll_mesh)


class BAKESETS_PG_set(bpy.types.PropertyGroup):
    # `name` comes from PropertyGroup and is also the file name prefix of the output.
    low: PointerProperty(name="Low Poly", type=bpy.types.Object, poll=_poll_mesh,
                         description="Object that receives the maps (needs a UV map)")
    cage: PointerProperty(name="Cage", type=bpy.types.Object, poll=_poll_mesh,
                          description="Optional cage object; replaces the extrusion")
    highs: CollectionProperty(type=BAKESETS_PG_high)
    high_index: IntProperty()
    maps: EnumProperty(update=_sync_shared, name="Maps", items=MAP_ITEMS, options={'ENUM_FLAG'}, default={'NORMAL'})
    resolution: EnumProperty(update=_sync_shared, name="Resolution", default='2048',
                             items=[(s, s, "") for s in ('256', '512', '1024', '2048', '4096', '8192')])
    output_name: StringProperty(
        name="Texture Name", default="", update=_join_group,
        description="File name prefix of the baked textures (<name>_<map>.png). Empty: the set name. Sets "
                    "with the same name bake into the same textures and share their baking settings, "
                    "so different objects can be added to one texture one at a time")
    clear_before_bake: BoolProperty(
        name="Clear Before Bake", default=True,
        description="On: every bake starts from an empty texture. Off: keep the existing texture file "
                    "(same name and size) and only replace the baked islands, to bake different "
                    "elements separately into one texture")
    albedo_alpha: BoolProperty(update=_sync_shared, 
        name="Transparent Background", default=False,
        description="Albedo gets an alpha channel: opaque on the islands and their margin, "
                    "transparent elsewhere, and the materials' own Principled Alpha "
                    "(e.g. a texture with transparent pixels) is baked into it")
    margin: IntProperty(update=_sync_shared, name="Margin", default=16, min=0, max=128, subtype='PIXEL',
                        description="Padding around UV islands, in pixels")
    cage_extrusion: FloatProperty(update=_sync_shared, name="Extrusion", default=0.1, min=0.0, subtype='DISTANCE',
                                  description="How far the rays start above the low poly")
    max_ray_distance: FloatProperty(update=_sync_shared, name="Max Ray Distance", default=0.0, min=0.0, subtype='DISTANCE',
                                    description="0 = unlimited")
    ao_distance: FloatProperty(update=_sync_shared, name="AO Distance", default=1.0, min=0.001, subtype='DISTANCE')
    thickness_distance: FloatProperty(update=_sync_shared, name="Thickness Distance", default=0.5, min=0.001, subtype='DISTANCE',
                                      description="Rays longer than this count as fully thick")
    curvature_radius: IntProperty(update=_sync_shared, name="Curvature Radius", default=2, min=1, max=32, subtype='PIXEL')
    curvature_strength: FloatProperty(update=_sync_shared, name="Curvature Strength", default=1.0, min=0.0, max=20.0)
    cavity_radius: IntProperty(update=_sync_shared, name="Cavity Radius", default=6, min=1, max=64, subtype='PIXEL')
    cavity_strength: FloatProperty(update=_sync_shared, name="Cavity Strength", default=2.0, min=0.0, max=20.0)
    dust_threshold: FloatProperty(update=_sync_shared, name="Dust Threshold", default=0.5, min=-1.0, max=1.0,
                                  description="Dot of the world normal with +Z where dust starts")
    dust_softness: FloatProperty(update=_sync_shared, name="Dust Softness", default=0.3, min=0.0, max=1.0)
    paint_ground: FloatProperty(update=_sync_shared, name="Paint Ground Level", default=0.2, min=0.0, max=1.0,
                                description="Brightness of surfaces facing down (up-facing ones are white)")
    paint_ao_strength: FloatProperty(update=_sync_shared, name="Paint AO Strength", default=1.0, min=0.0, max=1.0,
                                     description="How much the ambient occlusion darkens the cavities")


class BAKESETS_PG_settings(bpy.types.PropertyGroup):
    sets: CollectionProperty(type=BAKESETS_PG_set)
    active: IntProperty()
    output_dir: StringProperty(name="Output Folder", subtype='DIR_PATH', default="//bakes/")
    device: EnumProperty(name="Device", default='GPU',
                         items=[('GPU', "GPU", "Falls back to the CPU when no device is available"),
                                ('CPU', "CPU", "")])
    samples: IntProperty(name="Samples", default=1, min=1, max=1024,
                         description="Cycles samples for the flat maps (normal, albedo, IDs...)")
    ao_samples: IntProperty(name="AO Samples", default=32, min=1, max=4096,
                            description="Cycles samples for ambient occlusion and thickness")
    normal_y: EnumProperty(name="Normal Y", default='OPENGL',
                           items=[('OPENGL', "OpenGL (+Y)", "Blender, Godot, Unity"),
                                  ('DIRECTX', "DirectX (-Y)", "Unreal")])


classes = (BAKESETS_PG_high, BAKESETS_PG_set, BAKESETS_PG_settings)


def register():
    for c in classes:
        bpy.utils.register_class(c)
    bpy.types.Scene.bake_sets = PointerProperty(type=BAKESETS_PG_settings)


def unregister():
    del bpy.types.Scene.bake_sets
    for c in reversed(classes):
        bpy.utils.unregister_class(c)
