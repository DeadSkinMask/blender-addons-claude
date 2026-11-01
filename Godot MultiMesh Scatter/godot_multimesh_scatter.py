bl_info = {
    "name": "Godot MultiMesh Scatter Exporter",
    "author": "Luis Flores Zamorano",
    "version": (1, 1, 0),
    "blender": (5, 2, 0),
    "location": "View3D > Sidebar > Godot",
    "description": "Export Geometry Nodes instances as a GLB and/or OBJ mesh plus a compact transform binary for Godot MultiMeshInstance3D",
    "category": "Import-Export",
}

import bpy
import os
import struct
from mathutils import Matrix, Vector

MAGIC = b"GMMI0001"

def blender_to_godot_vec(v):
    # Blender XYZ -> Godot X,Y,Z. Blender +Z is up; Godot +Y is up.
    return Vector((v.x, v.z, -v.y))

def blender_to_godot_matrix(m):
    # C * M * C^-1, where C maps Blender (x,y,z) to Godot (x,z,-y).
    c = Matrix(((1, 0, 0, 0), (0, 0, 1, 0), (0, -1, 0, 0), (0, 0, 0, 1)))
    return c @ m @ c.inverted()

def find_instance_matrices(host, depsgraph):
    # DepsgraphObjectInstance items are temporary and only valid during iteration,
    # so copy the world matrix here instead of keeping references to them.
    host_eval = host.evaluated_get(depsgraph)
    found = []
    for inst in depsgraph.object_instances:
        if not inst.is_instance:
            continue
        parent = inst.parent
        if parent is None:
            continue
        # Geometry Nodes instances are parented to the evaluated scatter host.
        if parent == host_eval or parent.original == host:
            found.append(inst.matrix_world.copy())
    return host_eval, found

def write_transforms(filepath, transforms):
    with open(filepath, "wb") as f:
        f.write(MAGIC)
        f.write(struct.pack("<I", len(transforms)))
        for m in transforms:
            # Basis columns followed by origin, matching Godot Basis(x,y,z)+origin.
            for axis in (m.col[0].xyz, m.col[1].xyz, m.col[2].xyz, m.translation):
                f.write(struct.pack("<3f", float(axis[0]), float(axis[1]), float(axis[2])))

def select_only(obj):
    bpy.ops.object.select_all(action='DESELECT')
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj

def export_mesh_glb(mesh_obj, filepath):
    # Export the source mesh alone. Its mesh data remains local; use an applied/clean
    # object transform (ideally identity) so the GLB node transform doesn't surprise.
    select_only(mesh_obj)
    bpy.ops.export_scene.gltf(
        filepath=filepath,
        export_format='GLB',
        use_selection=True,
        export_apply=True,
        export_yup=True,
    )

def export_mesh_obj(mesh_obj, filepath):
    # OBJ has no node hierarchy: the object's world transform is baked into the
    # vertices, so keep the source mesh at an identity transform.
    select_only(mesh_obj)
    bpy.ops.wm.obj_export(
        filepath=filepath,
        export_selected_objects=True,
        apply_modifiers=True,
        export_materials=True,
        forward_axis='NEGATIVE_Z',
        up_axis='Y',
    )

MESH_EXPORTERS = {
    'GLB': (".glb", export_mesh_glb),
    'OBJ': (".obj", export_mesh_obj),
}

class GODOTSCATTER_Props(bpy.types.PropertyGroup):
    output_dir: bpy.props.StringProperty(
        name="Output folder", subtype='DIR_PATH',
        default="//godot_scatter"
    )
    mesh_object: bpy.props.PointerProperty(
        name="Instance mesh", type=bpy.types.Object,
        description="The mesh object connected to the Geometry Nodes Object input"
    )
    relative_to_host: bpy.props.BoolProperty(
        name="Relative to host", default=True,
        description="Store transforms relative to the scatter host instead of world origin"
    )
    export_mesh: bpy.props.BoolProperty(name="Export mesh", default=True)
    mesh_format: bpy.props.EnumProperty(
        name="Mesh format",
        items=(
            ('GLB', "GLB", "Export the instance mesh as binary glTF"),
            ('OBJ', "OBJ", "Export the instance mesh as Wavefront OBJ (+ MTL)"),
            ('BOTH', "GLB + OBJ", "Export the instance mesh in both formats"),
        ),
        default='GLB',
    )

class GODOTSCATTER_OT_export(bpy.types.Operator):
    bl_idname = "godot_scatter.export"
    bl_label = "Export to Godot"
    bl_description = "Export evaluated Geometry Nodes instances to GLB/OBJ + binary transforms"
    bl_options = {'REGISTER'}

    @classmethod
    def poll(cls, context):
        if context.mode != 'OBJECT':
            cls.poll_message_set("Switch to Object Mode to export")
            return False
        return True

    def execute(self, context):
        if context.mode != 'OBJECT':
            self.report({'ERROR'}, "Switch to Object Mode to export")
            return {'CANCELLED'}
        props = context.scene.godot_scatter_props
        host = context.object
        if host is None:
            self.report({'ERROR'}, "Select the object carrying the Geometry Nodes modifier")
            return {'CANCELLED'}
        if props.mesh_object is None and props.export_mesh:
            self.report({'ERROR'}, "Choose the source mesh used by the Object input")
            return {'CANCELLED'}

        outdir = bpy.path.abspath(props.output_dir)
        if not outdir:
            self.report({'ERROR'}, "Output folder is empty")
            return {'CANCELLED'}
        os.makedirs(outdir, exist_ok=True)

        depsgraph = context.evaluated_depsgraph_get()
        host_eval, instance_matrices = find_instance_matrices(host, depsgraph)
        if not instance_matrices:
            self.report({'ERROR'}, "No dependency-graph instances found for this host. Ensure the Geometry Nodes output still contains instances.")
            return {'CANCELLED'}

        host_world = host_eval.matrix_world.copy()
        inv_host = host_world.inverted_safe()
        transforms = []
        for mw in instance_matrices:
            # Geometry Nodes instance world transform -> host-local or world transform.
            bm = inv_host @ mw if props.relative_to_host else mw
            transforms.append(blender_to_godot_matrix(bm))

        bin_path = os.path.join(outdir, "scatter_transforms.bin")
        write_transforms(bin_path, transforms)

        if props.export_mesh:
            if props.mesh_object.type != 'MESH':
                self.report({'ERROR'}, "Instance mesh must be a Mesh object")
                return {'CANCELLED'}
            formats = ('GLB', 'OBJ') if props.mesh_format == 'BOTH' else (props.mesh_format,)
            for fmt in formats:
                ext, exporter = MESH_EXPORTERS[fmt]
                try:
                    exporter(props.mesh_object, os.path.join(outdir, "scatter_mesh" + ext))
                except Exception as exc:
                    self.report({'ERROR'}, f"{fmt} export failed: {exc}")
                    return {'CANCELLED'}

        # Restore host as active selection after the mesh exporters change selection.
        bpy.ops.object.select_all(action='DESELECT')
        host.select_set(True)
        context.view_layer.objects.active = host

        self.report({'INFO'}, f"Exported {len(transforms)} instances to {outdir}")
        return {'FINISHED'}

class GODOTSCATTER_PT_panel(bpy.types.Panel):
    bl_label = "Godot MultiMesh"
    bl_idname = "GODOTSCATTER_PT_panel"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "Godot"

    def draw(self, context):
        layout = self.layout
        p = context.scene.godot_scatter_props
        layout.label(text="Geometry Nodes scatter")
        layout.prop(p, "output_dir")
        layout.prop(p, "mesh_object")
        layout.prop(p, "relative_to_host")
        layout.prop(p, "export_mesh")
        row = layout.row()
        row.enabled = p.export_mesh
        row.prop(p, "mesh_format")
        if context.mode != 'OBJECT':
            layout.label(text="Export only works in Object Mode", icon='ERROR')
        layout.operator("godot_scatter.export", icon='EXPORT')

classes = (
    GODOTSCATTER_Props,
    GODOTSCATTER_OT_export,
    GODOTSCATTER_PT_panel,
)

def register():
    for cls in classes:
        bpy.utils.register_class(cls)
    bpy.types.Scene.godot_scatter_props = bpy.props.PointerProperty(type=GODOTSCATTER_Props)

def unregister():
    del bpy.types.Scene.godot_scatter_props
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)

if __name__ == "__main__":
    register()
