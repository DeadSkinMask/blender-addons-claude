"""Per-scene, per-collection and per-object bridge settings (bpy PropertyGroups)."""

import bpy
from bpy.props import BoolProperty, BoolVectorProperty, EnumProperty, FloatProperty, PointerProperty, StringProperty

# Default output folders, relative to the .blend (rooms live in
# levels/<area>/<room>/source/<room>.blend, see FORMAT.md).
DEFAULT_OUTPUT_DIR = "//../generated/"
DEFAULT_ASSETS_DIR = "//../../_kit/bridge_assets/"

ROLES = [
    ('STATIC', "Static geometry", "Visual mesh inside the level's static GLB"),
    ('COLLIDER', "Collider", "Primitive collision shape (StaticBody3D)"),
    ('AREA', "Area", "Area3D with a primitive shape"),
    ('PLACEHOLDER', "Placeholder", "Instance of a Godot .tscn scene"),
    ('MARKER', "Marker", "Marker3D (spawns, entry points, waypoints)"),
    ('PATH', "Path", "Path3D from a curve"),
    ('INSTANCE', "Collection instance", "Empty that instances a collection: instance of the asset's scene"),
    ('LIGHT', "Light", "OmniLight3D / SpotLight3D (lights inside an asset are exported by default)"),
]

OBJECT_ROLES = [('AUTO', "Auto", "Role of its collection or, without one, by object type (collection instances are always Collection instance)")] + ROLES
COLLECTION_ROLES = [('NONE', "None", "No default role for its content")] + ROLES

MODIFIER_POLICIES = [
    ('APPLY', "Apply", "Export the evaluated mesh (what the viewport shows)"),
    ('BASE', "Don't apply", "Export the base mesh, without modifiers"),
]

INSTANCE_POLICIES = [
    ('MULTIMESH', "MultiMesh", "Only the Geometry Nodes instances, as MultiMeshInstance3D; the object's own geometry is not exported"),
    ('SCENES', "Scenes", "Each instance of an asset collection as an instance of its scene; the object's own geometry is exported"),
    ('REALIZE', "Realize", "Instances as static geometry nodes (shared mesh), together with the object's geometry"),
    ('IGNORE', "Ignore", "Don't export the instances"),
]


VERTEX_COLOR_MODES = [
    ('DATA', "Data", "Exported (COLOR_0) but Godot's material does not use them: for masks and shaders"),
    ('ALBEDO', "Albedo", "Godot's material multiplies the albedo by the vertex color"),
    ('NONE', "Don't export", "Vertex colors are removed from the exported copy"),
]

SHAPES = [
    ('BOX', "Box", "BoxShape3D fitted to the object's local bounds"),
    ('SPHERE', "Sphere", "SphereShape3D; radius = half the largest dimension"),
    ('CAPSULE', "Capsule", "Vertical CapsuleShape3D (Blender Z axis)"),
    ('CYLINDER', "Cylinder", "Vertical CylinderShape3D (Blender Z axis)"),
    ('CONVEX', "Convex", "ConvexPolygonShape3D from the mesh vertices"),
    ('TRIMESH', "Trimesh", "ConcavePolygonShape3D from the mesh triangles (only when needed)"),
]

BODIES = [
    ('STATIC', "Static", "StaticBody3D"),
    ('ANIMATABLE', "Animatable", "AnimatableBody3D (moved by code or animation)"),
]


def _with_scene_default(items):
    return [('SCENE', "Scene default", "Use the scene's value")] + items


class GBRIDGE_SceneProps(bpy.types.PropertyGroup):
    root: PointerProperty(
        name="Root collection", type=bpy.types.Collection,
        description="Only what is inside this collection is exported")
    output_dir: StringProperty(
        name="Level output", subtype='DIR_PATH', default=DEFAULT_OUTPUT_DIR,
        description="The room's generated/ folder, inside the Godot project")
    assets_dir: StringProperty(
        name="Assets folder", subtype='DIR_PATH', default=DEFAULT_ASSETS_DIR,
        description="The area's shared assets folder, inside the Godot project")
    level_name: StringProperty(
        name="Level name", default="",
        description="Empty = name of the .blend file")
    modifiers: EnumProperty(name="Modifiers", items=MODIFIER_POLICIES, default='APPLY')
    instances: EnumProperty(name="Instances", items=INSTANCE_POLICIES, default='MULTIMESH')
    vertex_colors: EnumProperty(name="Vertex colors", items=VERTEX_COLOR_MODES, default='DATA')
    multimesh_chunk: FloatProperty(
        name="MultiMesh chunk (m)", default=20.0, min=1.0,
        description="MultiMesh instances are split in chunks of this width along X so Godot can cull the ones it does not see")


class GBRIDGE_CollectionProps(bpy.types.PropertyGroup):
    role: EnumProperty(name="Default role", items=COLLECTION_ROLES, default='NONE')
    skip: BoolProperty(name="Don't export", default=False,
                       description="Exclude this collection and all its content")


class GBRIDGE_ObjectProps(bpy.types.PropertyGroup):
    role: EnumProperty(name="Role", items=OBJECT_ROLES, default='AUTO')
    skip: BoolProperty(name="Don't export", default=False)
    uid: StringProperty(name="UID", default="",
                        description="Stable identifier of the entity between Blender and Godot")
    uid_owner: StringProperty(default="",
                              description="Object name when the uid was assigned, to detect duplicates")
    modifiers: EnumProperty(name="Modifiers", items=_with_scene_default(MODIFIER_POLICIES), default='SCENE')
    instances: EnumProperty(name="Instances", items=_with_scene_default(INSTANCE_POLICIES), default='SCENE')
    vertex_colors: EnumProperty(name="Vertex colors", items=_with_scene_default(VERTEX_COLOR_MODES),
                                default='SCENE')

    # Placeholder / área basada en escena
    scene: StringProperty(name="Scene", default="",
                          description="Godot scene to instance (res://….tscn)")
    # Colisor / área
    shape: EnumProperty(name="Shape", items=SHAPES, default='BOX')
    body: EnumProperty(name="Body", items=BODIES, default='STATIC')
    body_group: StringProperty(name="Body group", default="",
                               description="Colliders with the same group share one body in Godot")
    # Índice i = capa i + 1 de Godot. Vacío = valor por defecto del rol (ver FORMAT.md).
    layer: BoolVectorProperty(name="Layer", size=32, default=(False,) * 32)
    mask: BoolVectorProperty(name="Mask", size=32, default=(False,) * 32)


CLASSES = (GBRIDGE_SceneProps, GBRIDGE_CollectionProps, GBRIDGE_ObjectProps)


def register():
    for cls in CLASSES:
        bpy.utils.register_class(cls)
    bpy.types.Scene.gbridge = PointerProperty(type=GBRIDGE_SceneProps)
    bpy.types.Collection.gbridge = PointerProperty(type=GBRIDGE_CollectionProps)
    bpy.types.Object.gbridge = PointerProperty(type=GBRIDGE_ObjectProps)


def unregister():
    del bpy.types.Object.gbridge
    del bpy.types.Collection.gbridge
    del bpy.types.Scene.gbridge
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)
