import bpy
from bpy.types import Panel


def _active_bone_settings(context):
    pb = context.active_pose_bone
    return pb.inertial if pb else None


def _count_enabled(context):
    n = 0
    for obj in context.scene.objects:
        if obj.type == 'ARMATURE' and obj.pose:
            n += sum(1 for pb in obj.pose.bones if pb.inertial.enabled)
    return n


class INERTIALBONE_PT_main(Panel):
    bl_label = "Inertial Bone"
    bl_idname = "INERTIALBONE_PT_main"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "Inertial"

    def draw(self, context):
        layout = self.layout
        scn = context.scene.inertial_bone
        row = layout.row()
        row.scale_y = 1.4
        row.prop(scn, "preview", toggle=True, text="Preview ON" if scn.preview else "Preview OFF",
                 icon='PAUSE' if scn.preview else 'PLAY')
        n = _count_enabled(context)
        layout.label(text=f"{n} bone(s) enabled" + ("" if scn.preview else " (idle, no processing)"))
        layout.operator("inertial_bone.reset", icon='LOOP_BACK')

        col = layout.column(align=True)
        col.label(text="Selected pose bones:")
        row = col.row(align=True)
        op = row.operator("inertial_bone.enable_selected", text="Enable", icon='CHECKMARK')
        op.enable = True
        op = row.operator("inertial_bone.enable_selected", text="Disable", icon='X')
        op.enable = False
        col.operator("inertial_bone.couple_selected", icon='LINKED')
        col.operator("inertial_bone.copy_settings", icon='COPYDOWN')


class _BonePanel(Panel):
    bl_space_type = 'PROPERTIES'
    bl_region_type = 'WINDOW'
    bl_context = "bone"

    @classmethod
    def poll(cls, context):
        return context.active_pose_bone is not None


class INERTIALBONE_PT_bone(_BonePanel):
    bl_label = "Inertial Bone"
    bl_idname = "INERTIALBONE_PT_bone"
    bl_options = {'DEFAULT_CLOSED'}

    def draw_header(self, context):
        self.layout.prop(_active_bone_settings(context), "enabled", text="")

    def draw(self, context):
        s = _active_bone_settings(context)
        layout = self.layout
        layout.use_property_split = True
        layout.active = s.enabled
        layout.label(text="Vectors: X = right, Y = up, Z = back (character axes)")
        layout.prop(s, "axes_object")
        layout.prop(s, "front_axis")


class INERTIALBONE_PT_dynamics(_BonePanel):
    bl_label = "Dynamics"
    bl_parent_id = "INERTIALBONE_PT_bone"

    def draw(self, context):
        s = _active_bone_settings(context)
        layout = self.layout
        layout.use_property_split = True
        layout.active = s.enabled
        layout.prop(s, "frequency")
        layout.prop(s, "damping_ratio")
        layout.prop(s, "mass")
        layout.prop(s, "damping_pos")
        layout.prop(s, "damping_neg")
        layout.prop(s, "gravity")


class INERTIALBONE_PT_limits(_BonePanel):
    bl_label = "Limits"
    bl_parent_id = "INERTIALBONE_PT_bone"

    def draw(self, context):
        s = _active_bone_settings(context)
        layout = self.layout
        layout.use_property_split = True
        layout.active = s.enabled
        layout.prop(s, "max_offset")
        layout.prop(s, "max_factor_pos")
        layout.prop(s, "max_factor_neg")
        layout.prop(s, "progressive")


class INERTIALBONE_PT_tilt(_BonePanel):
    bl_label = "Tilt From Offset"
    bl_parent_id = "INERTIALBONE_PT_bone"
    bl_options = {'DEFAULT_CLOSED'}

    def draw(self, context):
        s = _active_bone_settings(context)
        layout = self.layout
        layout.use_property_split = True
        layout.active = s.enabled
        layout.label(text="Degrees per cm of offset")
        layout.prop(s, "tilt_from_z")
        layout.prop(s, "tilt_from_x")
        layout.prop(s, "tilt_from_y")
        layout.prop(s, "max_tilt")


class INERTIALBONE_PT_coupling(_BonePanel):
    bl_label = "Coupling"
    bl_parent_id = "INERTIALBONE_PT_bone"
    bl_options = {'DEFAULT_CLOSED'}

    def draw(self, context):
        s = _active_bone_settings(context)
        layout = self.layout
        layout.use_property_split = True
        layout.active = s.enabled
        layout.prop_search(s, "coupled_bone", context.object.pose, "bones", text="Coupled With")
        layout.prop(s, "coupling")


classes = (INERTIALBONE_PT_main, INERTIALBONE_PT_bone, INERTIALBONE_PT_dynamics,
           INERTIALBONE_PT_limits, INERTIALBONE_PT_tilt, INERTIALBONE_PT_coupling)


def register():
    for c in classes:
        bpy.utils.register_class(c)


def unregister():
    for c in reversed(classes):
        bpy.utils.unregister_class(c)
