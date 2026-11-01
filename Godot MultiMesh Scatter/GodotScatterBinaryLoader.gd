@tool
extends Node3D
class_name GodotScatterBinaryLoader

## Import scatter_mesh.glb (or scatter_mesh.obj) and scatter_transforms.bin into res://.
## Assign mesh_scene_path to the imported GLB/glTF scene, an OBJ file (imported either
## as a Mesh or as a scene) or any saved Mesh resource. Set relative_to_host in Blender
## and place this node where the original Blender scatter host should be.

@export_file("*.glb", "*.gltf", "*.obj", "*.tscn", "*.scn", "*.res", "*.tres", "*.mesh") var mesh_scene_path: String = "res://scatter_mesh.glb"
@export_file("*.bin") var transforms_path: String = "res://scatter_transforms.bin"
@export var rebuild_on_ready: bool = true
@export var use_custom_aabb: bool = false
@export var custom_aabb: AABB = AABB(Vector3(-100, -100, -100), Vector3(200, 200, 200))
## Replaces every surface material of the imported mesh. Leave empty to keep the originals.
@export var override_material: Material:
	set(value):
		override_material = value
		if is_instance_valid(multimesh_instance):
			multimesh_instance.material_override = value
## Reloads the .bin and mesh from the inspector (Godot 4.4+).
@export_tool_button("Rebuild", "Reload") var rebuild_button: Callable = _on_rebuild_pressed

enum DebugPreview { NONE, POINTS, BOUNDS, POINTS_AND_BOUNDS }

@export_group("Debug preview")
## Draws a point at each instance origin and/or the box enclosing all instances.
@export var debug_preview: DebugPreview = DebugPreview.NONE:
	set(value):
		debug_preview = value
		_update_debug()
@export var debug_color: Color = Color(1.0, 0.55, 0.1):
	set(value):
		debug_color = value
		_update_debug()
@export_range(1.0, 32.0, 0.5) var debug_point_size: float = 6.0:
	set(value):
		debug_point_size = value
		_update_debug()
## Draw the debug preview through other geometry.
@export var debug_on_top: bool = false:
	set(value):
		debug_on_top = value
		_update_debug()
## By default the preview only shows in the editor.
@export var debug_in_game: bool = false:
	set(value):
		debug_in_game = value
		_update_debug()

var multimesh_instance: MultiMeshInstance3D
var _debug_instance: MeshInstance3D
var _origins := PackedVector3Array()
var _bounds := AABB()
var _has_data := false

const MAGIC := "GMMI0001"
const HEADER_SIZE := 12 # 8-byte magic + uint32 count
const FLOATS_PER_TRANSFORM := 12
const BYTES_PER_TRANSFORM := FLOATS_PER_TRANSFORM * 4
const CHILD_NAME := "ScatterMultiMesh"

func _ready() -> void:
	if rebuild_on_ready:
		var err := rebuild()
		if err != OK:
			push_error("GodotScatterBinaryLoader: failed to load scatter data (error %s)." % err)

func rebuild() -> Error:
	# Binary data is not a Godot Resource; read it with FileAccess.
	var file := FileAccess.open(transforms_path, FileAccess.READ)
	if file == null:
		return FileAccess.get_open_error()

	var magic_bytes := file.get_buffer(8)
	if magic_bytes.get_string_from_ascii() != MAGIC:
		file.close()
		push_error("Invalid scatter binary header.")
		return ERR_FILE_CORRUPT

	var count := file.get_32()
	if count > 10_000_000:
		file.close()
		push_error("Unreasonable instance count: %d" % count)
		return ERR_FILE_CORRUPT
	if file.get_length() < HEADER_SIZE + count * BYTES_PER_TRANSFORM:
		file.close()
		push_error("Scatter file is truncated: expected %d instances." % count)
		return ERR_FILE_CORRUPT

	# File layout per instance: basis X column (xyz), Y column (xyz), Z column (xyz), origin (xyz).
	var values := file.get_buffer(count * BYTES_PER_TRANSFORM).to_float32_array()
	file.close()

	var mesh := _load_mesh(mesh_scene_path)
	if mesh == null:
		push_error("No Mesh found in: %s" % mesh_scene_path)
		return ERR_CANT_OPEN

	var mm := MultiMesh.new()
	mm.transform_format = MultiMesh.TRANSFORM_3D
	mm.mesh = mesh
	mm.instance_count = count
	mm.visible_instance_count = count

	# MultiMesh buffer stores each Transform3D as a row-major 3x4 matrix:
	# (x.x, y.x, z.x, o.x, x.y, y.y, z.y, o.y, x.z, y.z, z.z, o.z).
	var buffer := PackedFloat32Array()
	buffer.resize(count * FLOATS_PER_TRANSFORM)
	var origins := PackedVector3Array()
	origins.resize(count)
	var mesh_aabb := mesh.get_aabb()
	var bounds := AABB()
	for i in count:
		var s := i * FLOATS_PER_TRANSFORM
		for row in 3:
			var d := s + row * 4
			buffer[d] = values[s + row]
			buffer[d + 1] = values[s + 3 + row]
			buffer[d + 2] = values[s + 6 + row]
			buffer[d + 3] = values[s + 9 + row]
		var xform := Transform3D(
			Basis(
				Vector3(values[s], values[s + 1], values[s + 2]),
				Vector3(values[s + 3], values[s + 4], values[s + 5]),
				Vector3(values[s + 6], values[s + 7], values[s + 8])
			),
			Vector3(values[s + 9], values[s + 10], values[s + 11])
		)
		origins[i] = xform.origin
		var instance_bounds := xform * mesh_aabb
		bounds = instance_bounds if i == 0 else bounds.merge(instance_bounds)
	mm.buffer = buffer
	if use_custom_aabb:
		mm.custom_aabb = custom_aabb

	_remove_previous_instance()
	multimesh_instance = MultiMeshInstance3D.new()
	multimesh_instance.name = CHILD_NAME
	multimesh_instance.multimesh = mm
	multimesh_instance.material_override = override_material
	add_child(multimesh_instance)
	multimesh_instance.owner = get_tree().edited_scene_root if Engine.is_editor_hint() else self

	_origins = origins
	_bounds = bounds
	_has_data = count > 0
	_update_debug()
	return OK

func _on_rebuild_pressed() -> void:
	var err := rebuild()
	if err != OK:
		push_error("GodotScatterBinaryLoader: rebuild failed (error %s)." % err)

func _update_debug() -> void:
	# Setters also run while the scene is loading; rebuild() redraws once ready.
	if not is_inside_tree():
		return
	if is_instance_valid(_debug_instance):
		remove_child(_debug_instance)
		_debug_instance.queue_free()
	_debug_instance = null

	if debug_preview == DebugPreview.NONE or not _has_data:
		return
	if not Engine.is_editor_hint() and not debug_in_game:
		return

	var mat := StandardMaterial3D.new()
	mat.shading_mode = BaseMaterial3D.SHADING_MODE_UNSHADED
	mat.albedo_color = debug_color
	mat.use_point_size = true
	mat.point_size = debug_point_size
	mat.no_depth_test = debug_on_top
	if debug_color.a < 1.0:
		mat.transparency = BaseMaterial3D.TRANSPARENCY_ALPHA

	var debug_mesh := ArrayMesh.new()
	if debug_preview in [DebugPreview.POINTS, DebugPreview.POINTS_AND_BOUNDS]:
		_add_debug_surface(debug_mesh, Mesh.PRIMITIVE_POINTS, _origins, mat)
	if debug_preview in [DebugPreview.BOUNDS, DebugPreview.POINTS_AND_BOUNDS]:
		_add_debug_surface(debug_mesh, Mesh.PRIMITIVE_LINES, _aabb_edges(_bounds), mat)

	_debug_instance = MeshInstance3D.new()
	_debug_instance.name = "ScatterDebugPreview"
	_debug_instance.mesh = debug_mesh
	_debug_instance.cast_shadow = GeometryInstance3D.SHADOW_CASTING_SETTING_OFF
	# Internal and without owner: never shown in the scene tree nor saved.
	add_child(_debug_instance, false, Node.INTERNAL_MODE_BACK)

func _add_debug_surface(target: ArrayMesh, primitive: Mesh.PrimitiveType, vertices: PackedVector3Array, mat: Material) -> void:
	var arrays := []
	arrays.resize(Mesh.ARRAY_MAX)
	arrays[Mesh.ARRAY_VERTEX] = vertices
	target.add_surface_from_arrays(primitive, arrays)
	target.surface_set_material(target.get_surface_count() - 1, mat)

func _aabb_edges(box: AABB) -> PackedVector3Array:
	# AABB.get_endpoint() index bits: 1 = +Z, 2 = +Y, 4 = +X. Each edge joins
	# two corners that differ in exactly one bit.
	var lines := PackedVector3Array()
	for i in 8:
		for bit in [1, 2, 4]:
			if i & bit == 0:
				lines.append(box.get_endpoint(i))
				lines.append(box.get_endpoint(i | bit))
	return lines

func _remove_previous_instance() -> void:
	# The child is saved with the scene in the editor, so after reopening the scene
	# the reference is lost; look it up by name to avoid stacking duplicates.
	var old: Node = multimesh_instance if is_instance_valid(multimesh_instance) else get_node_or_null(CHILD_NAME)
	if old:
		remove_child(old) # Free the name immediately so the new child keeps it.
		old.queue_free()
	multimesh_instance = null

func _load_mesh(path: String) -> Mesh:
	var res := load(path)
	# OBJ files import as a Mesh by default; GLB/glTF (and OBJ imported "as Scene") as PackedScene.
	if res is Mesh:
		return res
	if res is PackedScene:
		var instance := (res as PackedScene).instantiate()
		var mesh := _find_first_mesh(instance)
		instance.free()
		return mesh
	push_error("Could not load mesh as Mesh or PackedScene: %s" % path)
	return null

func _find_first_mesh(node: Node) -> Mesh:
	if node is MeshInstance3D and node.mesh != null:
		return node.mesh
	for child in node.get_children():
		var found := _find_first_mesh(child)
		if found != null:
			return found
	return null
