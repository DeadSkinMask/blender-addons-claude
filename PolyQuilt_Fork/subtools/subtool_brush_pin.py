# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 3 of the License, or
# (at your option) any later version.

import bpy
from .subtool_brush_relax import SubToolBrushRelax
from ..QMesh import *

PIN_GROUP_NAME = "vertex_pinned"

# bumped whenever pin weights change so the gizmo rebuilds its overlay cache
pin_state = {"rev": 0}


class SubToolBrushPin(SubToolBrushRelax) :
    """Paints weight into the 'vertex_pinned' group (used by the Smooth brush as a mask)."""
    name = "PinBrushTool"
    erase = False
    color = (1.0, 0.55, 0.1, 0.6)

    def PinWeightGetter( self , bm ) :
        # painting must not be limited by the pin weight itself
        return lambda vt : 0.0

    @classmethod
    def Draw( cls , preferences , pos ) :
        SubToolBrushRelax.DrawCircle( preferences , pos , cls.color , cls.color )

    def OnDraw( self , context ) :
        from ..utils import draw_util
        draw_util.draw_circle2D( self.mouse_pos , self.radius , color = self.color , fill = False , subdivide = 64 , dpi = False , width = 2.0 )

    def DoRelax( self , context , coord ) :
        obj = self.bmo.obj
        bm = self.bmo.bm
        group = obj.vertex_groups.get( PIN_GROUP_NAME )
        if group is None :
            if self.erase :
                return
            group = obj.vertex_groups.new( name = PIN_GROUP_NAME )
        index = group.index
        layer = bm.verts.layers.deform.verify()

        coords = self.CollectVerts( context , coord )
        if not coords :
            return
        self.dirty = True

        strength = self.preferences.brush_strength
        targets = {}
        for vt , (f , orig) in coords.items() :
            targets[vt] = max( targets.get( vt , 0.0 ) , f )
            if self.bmo.is_mirror_mode :
                mirror = self.bmo.find_mirror( vt )
                if mirror is not None :
                    targets[mirror] = max( targets.get( mirror , 0.0 ) , f )

        for vt , f in targets.items() :
            dvert = vt[layer]
            w = dvert.get( index , 0.0 )
            k = min( 1.0 , f * strength )
            if self.erase :
                w = w * (1.0 - k)
                if w < 0.01 :
                    if index in dvert :
                        del dvert[index]
                    continue
            else :
                w = w + (1.0 - w) * k
                if w > 0.995 :
                    w = 1.0
            dvert[index] = w

        pin_state["rev"] += 1
        self.bmo.UpdateMesh( changeTopology = True , loop_triangles = False , destructive = False )


class SubToolBrushUnpin(SubToolBrushPin) :
    name = "UnpinBrushTool"
    erase = True
    color = (0.3, 0.8, 1.0, 0.6)


class MESH_OT_pq_pin_selected(bpy.types.Operator) :
    """Set or clear the 'vertex_pinned' weight of the selected vertices"""
    bl_idname = "mesh.pq_pin_selected"
    bl_label = "Pin Selected"
    bl_options = {'REGISTER', 'UNDO'}

    action : bpy.props.EnumProperty(
        name = "Action",
        items = ( ('PIN', "Pin", ""), ('UNPIN', "Unpin", "") ),
        default = 'PIN',
    )
    weight : bpy.props.FloatProperty( name = "Weight", min = 0.0, max = 1.0, default = 1.0 )

    @classmethod
    def poll( cls , context ) :
        return context.mode == 'EDIT_MESH' and context.edit_object is not None

    def execute( self , context ) :
        import bmesh
        count = 0
        for obj in context.objects_in_mode_unique_data :
            if obj.type != 'MESH' :
                continue
            group = obj.vertex_groups.get( PIN_GROUP_NAME )
            if group is None :
                if self.action == 'UNPIN' :
                    continue
                group = obj.vertex_groups.new( name = PIN_GROUP_NAME )
            bm = bmesh.from_edit_mesh( obj.data )
            layer = bm.verts.layers.deform.verify()
            for v in bm.verts :
                if not v.select :
                    continue
                if self.action == 'PIN' :
                    v[layer][group.index] = self.weight
                elif group.index in v[layer] :
                    del v[layer][group.index]
                count += 1
            bmesh.update_edit_mesh( obj.data , loop_triangles = False , destructive = False )
            pin_state["rev"] += 1
        self.report({'INFO'}, "%s %d vertices" % ( "Pinned" if self.action == 'PIN' else "Unpinned" , count ))
        return {'FINISHED'}
