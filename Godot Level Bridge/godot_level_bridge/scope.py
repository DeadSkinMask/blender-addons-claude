"""What gets exported: objects inside the level root collection and their roles.

Rules (FORMAT.md §2): only objects inside the root collection (recursively);
collections excluded from the view layer or marked "skip" drop their whole
content; objects marked "skip" are dropped; viewport visibility is ignored.
A role comes from the object, else from the nearest collection with a role,
else from the object type (meshes are static geometry, the rest is ignored).
"""

import bpy

TYPE_DEFAULT_ROLES = {'MESH': 'STATIC'}


def _layer_collections(view_layer):
    """{collection: LayerCollection} for the whole view layer tree."""
    result = {}
    stack = [view_layer.layer_collection]
    while stack:
        lc = stack.pop()
        result[lc.collection] = lc
        stack.extend(lc.children)
    return result


def collect(scene, view_layer):
    """[(object, role)] in a stable order (collection tree, then object name)."""
    root = scene.gbridge.root
    if root is None:
        return []
    layer_colls = _layer_collections(view_layer)
    found = {}

    def walk(collection, inherited_role):
        lc = layer_colls.get(collection)
        if collection.gbridge.skip or (lc is not None and lc.exclude):
            return
        role = collection.gbridge.role if collection.gbridge.role != 'NONE' else inherited_role
        for obj in sorted(collection.objects, key=lambda o: o.name):
            if obj.name in found or obj.gbridge.skip:
                continue
            obj_role = obj.gbridge.role if obj.gbridge.role != 'AUTO' else (
                role or TYPE_DEFAULT_ROLES.get(obj.type))
            if obj_role:
                found[obj.name] = (obj, obj_role)
        for child in sorted(collection.children, key=lambda c: c.name):
            walk(child, role)

    walk(root, None)
    return list(found.values())


def by_role(entries, role):
    return [obj for obj, r in entries if r == role]
