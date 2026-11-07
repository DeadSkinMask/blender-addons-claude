"""Assets: instanced collections → <asset>.asset.json + <asset>.glb (FORMAT.md §4c).

An asset is a collection of the same .blend instanced by an empty (role
INSTANCE) or by Geometry Nodes with the "Scenes" instance policy. Its content
follows the level rules (roles, skip) but ignores view layer exclusion (asset
collections usually live excluded, outside the level root). Everything is
written in the asset's space: the collection's instance_offset is the origin.
Lights inside an asset are exported by default; Geometry Nodes instances inside
it are always realized into its GLB. Nested instance empties give nested assets.
"""

import hashlib
import os

import bpy
from mathutils import Matrix

from . import entities, instances, manifest, paths, scope, static_export


def manifest_filename(name):
    return name + ".asset.json"


def glb_filename(name):
    return name + ".glb"


def space(collection):
    """World → asset space: the collection's instance offset becomes the origin."""
    return Matrix.Translation(-collection.instance_offset)


def candidate_collections():
    """Collections that something instances: empties, and Collection Info nodes /
    collection inputs of Geometry Nodes modifiers (to recognise GN instances)."""
    found = set()
    for obj in bpy.data.objects:
        if obj.instance_type == 'COLLECTION' and obj.instance_collection:
            found.add(obj.instance_collection)
        for mod in obj.modifiers:
            if mod.type != 'NODES' or mod.node_group is None:
                continue
            for item in mod.node_group.interface.items_tree:
                if getattr(item, "socket_type", "") == 'NodeSocketCollection' and item.in_out == 'INPUT':
                    value = mod.get(item.identifier)
                    if isinstance(value, bpy.types.Collection):
                        found.add(value)
    for tree in bpy.data.node_groups:
        if tree.bl_idname != 'GeometryNodeTree':
            continue
        for node in tree.nodes:
            if node.bl_idname == 'GeometryNodeCollectionInfo':
                value = node.inputs[0].default_value
                if value is not None:
                    found.add(value)
    return found


def asset_of(obj, candidates):
    """Innermost candidate collection that contains `obj` (or None)."""
    best = None
    for col in candidates:
        if obj.name in col.all_objects and (best is None or len(col.all_objects) < len(best.all_objects)):
            best = col
    return best


def role_in_asset(obj):
    """(asset collection, role) of an object that lives inside an asset collection,
    or (None, None). Used by the Object panel for objects outside the level root."""
    collection = asset_of(obj, candidate_collections())
    if collection is None:
        return None, None
    return collection, dict(scope.collect_collection(collection, in_asset=True)).get(obj)


class AssetExporter:
    """Exports assets once per run (nested ones included). `results` maps the
    asset name to its manifest entry {"manifest": res path, "hash": sha1}."""

    def __init__(self, context, assets_dir, project_root):
        self.context = context
        self.assets_dir = assets_dir
        self.project_root = project_root
        self.results = {}
        self.written = []   # files written this run
        self.issues = []    # [(object or collection name, message)]
        self._sources = {}  # asset name → collection (name clashes)

    def entry(self, collection):
        """Export `collection` (if not done yet) and return its manifest entry."""
        name = entities.asset_name(collection)
        other = self._sources.get(name)
        if other is not None and other is not collection:
            raise ValueError("collections {} and {} give the same asset name '{}'".format(
                other.name, collection.name, name))
        if name not in self.results:
            self._sources[name] = collection
            self.results[name] = None  # in progress: Blender forbids instancing cycles
            self.results[name] = self._export(collection, name)
        return self.results[name]

    def _export(self, collection, name):
        context = self.context
        scene = context.scene
        depsgraph = context.evaluated_depsgraph_get()
        to_asset = space(collection)
        entries = scope.collect_collection(collection, in_asset=True)
        entity_objs = [obj for obj, role in entries if role in entities.ENTITY_ROLES]
        for obj_name, message in entities.ensure_uids(entity_objs):
            self.issues.append((obj_name, message))
        exported = set(entity_objs)

        def uid_of(obj):
            return obj.gbridge.uid if obj in exported else None

        data = manifest.new_manifest(name)
        static_objs = scope.by_role(entries, 'STATIC')
        found = instances.collect(depsgraph, static_objs, with_meshes=True)
        glb_path = os.path.join(self.assets_dir, glb_filename(name))
        try:
            written = None
            if static_objs:
                written = static_export.export(
                    context, static_objs, glb_path, name,
                    extra=instances.realized(found, scene), space=to_asset)
        finally:
            instances.free_meshes(found)
        if written is None:
            if os.path.isfile(glb_path):
                os.remove(glb_path)
        else:
            data["static_scene"] = glb_filename(name)
            if written:
                self.written.append(glb_path)

        for obj, role in entries:
            if role not in entities.ENTITY_ROLES:
                continue
            entity = entities.build(obj, role, depsgraph, self.project_root, uid_of, space=to_asset)
            if entity is None:
                continue
            if role == 'INSTANCE':
                nested = self.entry(obj.instance_collection)
                if nested is not None:
                    data["assets"][entity["asset"]] = nested
            data["entities"].append(entity)

        json_path = os.path.join(self.assets_dir, manifest_filename(name))
        text = manifest.dumps(data)
        if manifest.write_if_changed(json_path, text):
            self.written.append(json_path)
        digest = hashlib.sha1(text.encode("utf-8"))
        if data["static_scene"]:
            with open(glb_path, "rb") as f:
                digest.update(f.read())
        return {"manifest": paths.to_res(self.project_root, json_path), "hash": digest.hexdigest()}
