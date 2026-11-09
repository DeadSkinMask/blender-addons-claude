# SPDX-License-Identifier: GPL-3.0-or-later
"""Temporary scene holding copies of the objects to bake.

The user's data is never modified: the copies get their own material copies, which are
rewired freely (image target node, emission), and everything is removed afterwards."""
import colorsys

import bpy

TMP = "_bakesets_tmp"


def id_color(index):
    h = (index * 0.61803398875) % 1.0
    return colorsys.hsv_to_rgb(h, 0.65, 0.95)


class TempBake:
    def __init__(self, context, bset, gs):
        self.context, self.bset, self.gs = context, bset, gs
        self.scene = self.view_layer = self.world = None
        self.low = self.cage = None
        self.sources = []          # object copies the maps are projected from
        self.projected = False     # True when the sources are not the low poly itself
        self.materials = {}        # object copy -> [material copies]
        self.target_nodes = {}     # low material copy -> image node
        self.emit_nodes = {}       # material copy -> emission node
        self.mat_ids = {}          # original material name -> index
        self._objs, self._meshes, self._mats, self._images = [], [], [], []

    # -- lifecycle ------------------------------------------------------------------
    def __enter__(self):
        self.context.view_layer.update()
        self.scene = bpy.data.scenes.new(TMP)
        self.scene.render.engine = 'CYCLES'
        self.scene.cycles.device = self.gs.device
        self.world = bpy.data.worlds.new(TMP)
        self.scene.world = self.world
        self.view_layer = self.scene.view_layers[0]
        b = self.bset
        self.low = self._copy(b.low, "_bs_low", own_data=True)
        highs = [h.obj for h in b.highs if h.obj and h.obj != b.low]
        self.projected = bool(highs)
        if highs:
            self.sources = [self._copy(o, "_bs_high%d" % i) for i, o in enumerate(highs)]
        else:
            self.sources = [self.low]
        if b.cage:
            self.cage = self._copy(b.cage, "_bs_cage")
        for o in [self.low] + [s for s in self.sources if s is not self.low]:
            self._own_materials(o)
        self.view_layer.update()
        return self

    def __exit__(self, *exc):
        for rm, items in ((bpy.data.scenes, [self.scene]), (bpy.data.objects, self._objs),
                          (bpy.data.meshes, self._meshes), (bpy.data.materials, self._mats),
                          (bpy.data.images, self._images), (bpy.data.worlds, [self.world])):
            for it in items:
                try:
                    if it is not None:
                        rm.remove(it)
                except (ReferenceError, RuntimeError):
                    pass
        return False

    def track_image(self, img):
        self._images.append(img)
        return img

    # -- copies ----------------------------------------------------------------------
    def _copy(self, src, name, own_data=False):
        o = src.copy()
        o.name = name
        if own_data or len(o.material_slots) == 0:
            o.data = src.data.copy()
            self._meshes.append(o.data)
        o.animation_data_clear()
        o.constraints.clear()
        o.parent = None
        o.matrix_world = src.matrix_world.copy()
        o.hide_viewport = False
        o.hide_render = False
        o.hide_select = False
        self.scene.collection.objects.link(o)
        self._objs.append(o)
        return o

    def _own_materials(self, o):
        if len(o.material_slots) == 0:
            o.data.materials.append(None)
        mats = []
        for slot in o.material_slots:
            orig = slot.material
            nm = orig.copy() if orig else bpy.data.materials.new("_bs_default")
            if nm.node_tree is None:
                nm.use_nodes = True
            nm["bs_orig"] = orig.name if orig else ""
            slot.link = 'OBJECT'
            slot.material = nm
            self._mats.append(nm)
            mats.append(nm)
        self.materials[o] = mats

    def material_index(self, mat):
        key = mat.get("bs_orig", "")
        return self.mat_ids.setdefault(key, len(self.mat_ids))
