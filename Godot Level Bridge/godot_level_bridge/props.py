"""Per-scene, per-collection and per-object bridge settings (bpy PropertyGroups)."""

import bpy
from bpy.props import BoolProperty, BoolVectorProperty, EnumProperty, FloatProperty, PointerProperty, StringProperty

# Default output folders, relative to the .blend (rooms live in
# levels/<area>/<room>/source/<room>.blend, see FORMAT.md).
DEFAULT_OUTPUT_DIR = "//../generated/"
DEFAULT_ASSETS_DIR = "//../../_kit/bridge_assets/"

ROLES = [
    ('STATIC', "Geometría estática", "Malla visual dentro del GLB estático del nivel"),
    ('COLLIDER', "Colisor", "Forma primitiva de colisión (StaticBody3D)"),
    ('AREA', "Área", "Area3D con forma primitiva"),
    ('PLACEHOLDER', "Placeholder", "Instancia de una escena .tscn de Godot"),
    ('MARKER', "Marcador", "Marker3D (spawns, entry points, waypoints)"),
    ('PATH', "Path", "Path3D a partir de una curva"),
]

OBJECT_ROLES = [('AUTO', "Automático", "Rol de su colección o, si no tiene, según el tipo de objeto")] + ROLES
COLLECTION_ROLES = [('NONE', "Ninguno", "Sin rol por defecto para su contenido")] + ROLES

MODIFIER_POLICIES = [
    ('APPLY', "Aplicar", "Exportar la malla evaluada (lo que se ve en el viewport)"),
    ('BASE', "No aplicar", "Exportar la malla base, sin modificadores"),
]

INSTANCE_POLICIES = [
    ('MULTIMESH', "MultiMesh", "Solo las instancias de Geometry Nodes, como MultiMeshInstance3D; la geometría propia del objeto no se exporta"),
    ('SCENES', "Escenas", "Cada instancia de una colección-asset como instancia de su escena"),
    ('REALIZE', "Realizar", "Las instancias como nodos de la geometría estática (malla compartida), junto con la geometría del objeto"),
    ('IGNORE', "Ignorar", "No exportar las instancias"),
]


VERTEX_COLOR_MODES = [
    ('DATA', "Datos", "Se exportan (COLOR_0) y el material de Godot no los usa: para máscaras y shaders"),
    ('ALBEDO', "Albedo", "El material de Godot multiplica el albedo por el color de vértice"),
    ('NONE', "No exportar", "Se quitan los colores de vértice de la copia exportada"),
]

SHAPES = [
    ('BOX', "Caja", "BoxShape3D ajustada a la caja local del objeto"),
    ('SPHERE', "Esfera", "SphereShape3D; radio = mitad de la mayor dimensión"),
    ('CAPSULE', "Cápsula", "CapsuleShape3D vertical (eje Z de Blender)"),
    ('CYLINDER', "Cilindro", "CylinderShape3D vertical (eje Z de Blender)"),
    ('CONVEX', "Convexa", "ConvexPolygonShape3D con los vértices de la malla"),
    ('TRIMESH', "Trimesh", "ConcavePolygonShape3D con los triángulos de la malla (solo si hace falta)"),
]

BODIES = [
    ('STATIC', "Estático", "StaticBody3D"),
    ('ANIMATABLE', "Animable", "AnimatableBody3D (se mueve por código o animación)"),
]


def _with_scene_default(items):
    return [('SCENE', "Por defecto de la escena", "Usar el valor de la escena")] + items


class GBRIDGE_SceneProps(bpy.types.PropertyGroup):
    root: PointerProperty(
        name="Colección raíz", type=bpy.types.Collection,
        description="Solo se exporta lo que está dentro de esta colección")
    output_dir: StringProperty(
        name="Salida del nivel", subtype='DIR_PATH', default=DEFAULT_OUTPUT_DIR,
        description="Carpeta generated/ de la sala, dentro del proyecto Godot")
    assets_dir: StringProperty(
        name="Carpeta de assets", subtype='DIR_PATH', default=DEFAULT_ASSETS_DIR,
        description="Carpeta compartida de assets del área, dentro del proyecto Godot")
    level_name: StringProperty(
        name="Nombre del nivel", default="",
        description="Vacío = nombre del archivo .blend")
    modifiers: EnumProperty(name="Modificadores", items=MODIFIER_POLICIES, default='APPLY')
    instances: EnumProperty(name="Instancias", items=INSTANCE_POLICIES, default='MULTIMESH')
    vertex_colors: EnumProperty(name="Colores de vértice", items=VERTEX_COLOR_MODES, default='DATA')
    multimesh_chunk: FloatProperty(
        name="Trozo MultiMesh (m)", default=20.0, min=1.0,
        description="Las instancias MultiMesh se dividen en trozos de este ancho en X para que Godot descarte los que no ve")


class GBRIDGE_CollectionProps(bpy.types.PropertyGroup):
    role: EnumProperty(name="Rol por defecto", items=COLLECTION_ROLES, default='NONE')
    skip: BoolProperty(name="No exportar", default=False,
                       description="Excluir esta colección y todo su contenido")


class GBRIDGE_ObjectProps(bpy.types.PropertyGroup):
    role: EnumProperty(name="Rol", items=OBJECT_ROLES, default='AUTO')
    skip: BoolProperty(name="No exportar", default=False)
    uid: StringProperty(name="UID", default="",
                        description="Identificador estable de la entidad entre Blender y Godot")
    uid_owner: StringProperty(default="",
                              description="Nombre del objeto cuando se asignó el uid, para detectar duplicados")
    modifiers: EnumProperty(name="Modificadores", items=_with_scene_default(MODIFIER_POLICIES), default='SCENE')
    instances: EnumProperty(name="Instancias", items=_with_scene_default(INSTANCE_POLICIES), default='SCENE')
    vertex_colors: EnumProperty(name="Colores de vértice", items=_with_scene_default(VERTEX_COLOR_MODES),
                                default='SCENE')

    # Placeholder / área basada en escena
    scene: StringProperty(name="Escena", default="",
                          description="Escena de Godot a instanciar (res://….tscn)")
    # Colisor / área
    shape: EnumProperty(name="Forma", items=SHAPES, default='BOX')
    body: EnumProperty(name="Cuerpo", items=BODIES, default='STATIC')
    body_group: StringProperty(name="Grupo de cuerpo", default="",
                               description="Colisores con el mismo grupo comparten un cuerpo en Godot")
    # Índice i = capa i + 1 de Godot. Vacío = valor por defecto del rol (ver FORMAT.md).
    layer: BoolVectorProperty(name="Capa", size=32, default=(False,) * 32)
    mask: BoolVectorProperty(name="Máscara", size=32, default=(False,) * 32)


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
