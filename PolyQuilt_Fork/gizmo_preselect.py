# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful, but
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTIBILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU
# General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program. If not, see <http://www.gnu.org/licenses/>.

import bpy
import mathutils
import time
from .QMesh import *
from .utils import draw_util
from .subtools import *
from .pq_tool import *
from .subtools.subtool_brush_pin import pin_state, PIN_GROUP_NAME
import gpu
from gpu_extras.batch import batch_for_shader

class PQ_Gizmo_Preselect( bpy.types.Gizmo):
    bl_idname = "MESH_GT_PQ_Preselect"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.bmo = None
        self.currentElement = None
        self.preferences = bpy.context.preferences.addons[__package__].preferences
        self.DrawHighlight = None
        self.region = None
        self.subtool = None
        self.tool_table = [None,None,None,None]
        self.tool = None
        self.invalid = False
        self.temp = bmesh.new()
        self.pin_cache = None
        self.pin_rev = -1

    def setup(self):
        self.bmo = None        
        self.currentElement = ElementItem.Empty()

    def init( self , context , tool ) :
        self.tool = tool
        self.maintool = maintools[tool.pq_main_tool]
        self.subtool = self.maintool
        self.region = context.region_data
        self.bmo = QMesh( context.active_object , self.preferences )
        self.keyitem = None
        if self.subtool :
            context.window.cursor_set( self.subtool.GetCursor() )

    def exit( self , context, cancel) :
        print("hoge")
        pass

    @property
    def operator( self ) :
        return self.tool.pq_operator

    def test_select(self, context, location):
        if self.invalid :
            self.bmo.invalid = True
            self.invalid = False

        if PQ_GizmoGroup_Base.running_polyquilt :
            self.DrawHighlight = None
            return -1
        
        if self.currentElement == None :
            self.currentElement = ElementItem.Empty()

        self.mouse_pos = mathutils.Vector(location) 
        if context.region == self.region :
            return -1
        if self.bmo == None :
            self.bmo = QMesh( context.active_object , self.preferences )
        self.bmo.CheckValid( context )
        self.bmo.UpdateView(context)
        QSnap.update(context)

        if self.subtool != None :
            element = self.subtool.pick_element( self.bmo , location , self.preferences )
            element.set_snap_div( self.preferences.loopcut_division )
            if self.subtool.UpdateHighlight( self , element ) :
                context.area.tag_redraw()
        else :
            element = ElementItem.Empty()

        self.currentElement = element

        if self.subtool != None :
            self.DrawHighlight = self.subtool.DrawHighlight( self , self.currentElement )
        else :
            self.DrawHighlight = None

        return -1

    def draw(self, context):
        if PQ_GizmoGroup_Base.running_polyquilt  :
            self.DrawHighlight = None

        self.draw_pinned( context )

        if self.DrawHighlight != None :
            if type(self.DrawHighlight) == list :
                for drawHighlight in self.DrawHighlight :
                    drawHighlight()
            else :
                self.DrawHighlight()

    PIN_COLOR = (0.0, 1.0, 1.0, 1.0)

    def draw_pinned( self , context ) :
        """Cyan dots on the vertices that have weight in 'vertex_pinned' (vertex select mode only)."""
        try :
            if not context.tool_settings.mesh_select_mode[0] :
                return
            bmo = self.bmo
            if bmo is None or not bmo.bm.is_valid :
                return
            obj = bmo.obj
            group = obj.vertex_groups.get( PIN_GROUP_NAME )
            layer = bmo.bm.verts.layers.deform.active
            if group is None or layer is None :
                return
            bm = bmo.bm
            if self.pin_cache is None or self.pin_rev != pin_state["rev"] :
                index = group.index
                self.pin_cache = [ v for v in bm.verts if not v.hide and v[layer].get( index , 0.0 ) > 0.0 ]
                self.pin_rev = pin_state["rev"]
            if not self.pin_cache :
                return

            rv3d = context.region_data
            matrix_world = obj.matrix_world
            normal_matrix = matrix_world.to_3x3()
            if rv3d.is_perspective :
                eye = rv3d.view_matrix.inverted().translation
            else :
                eye = None
                view_dir = rv3d.view_matrix.inverted().to_3x3() @ mathutils.Vector((0,0,-1))
            pos = []
            for v in self.pin_cache :
                if not v.is_valid :
                    self.pin_cache = None
                    return
                world = matrix_world @ v.co
                n = normal_matrix @ v.normal
                to_vert = ( world - eye ) if eye is not None else view_dir
                if n.dot( to_vert ) > 0.0 :   # facing away from the camera
                    continue
                pos.append( world )
            if pos :
                shader = gpu.shader.from_builtin('POINT_UNIFORM_COLOR')
                shader.bind()
                shader.uniform_float("color", self.PIN_COLOR)
                gpu.state.blend_set("ALPHA")
                gpu.state.point_size_set( 8.0 )
                batch_for_shader( shader , 'POINTS' , {"pos": pos} ).draw( shader )
        except ( ReferenceError , ValueError ) :
            self.pin_cache = None

    def refresh( self , context ) :
        self.pin_cache = None
        if self.bmo != None :
            self.invalid = True
            self.currentElement = ElementItem.Empty()
            self.DrawHighlight = None

    def recive_event( self , context , event ) :
        subtool = self.maintool

        self.keyitem = self.get_keyitem( event.shift , event.ctrl , event.alt,  event.oskey )
        if self.keyitem and hasattr( self.keyitem.properties , "tool_mode" ) :
            subtool = maintools[ self.keyitem.properties.tool_mode ]

        if self.subtool != subtool :
            self.subtool = subtool
            if context.area :
                context.area.tag_redraw()
        if self.subtool :
            PQ_GizmoGroup_Base.set_cursor( subtool.GetCursor() )
        else :
            PQ_GizmoGroup_Base.set_cursor( )

        if context.region_data == self.region and self.subtool:
            return self.subtool.recive_event( self , context , event )
        return False

    def get_keyitem( self , shift , ctrl , alt,  oskey ) :
        keymap = bpy.context.window_manager.keyconfigs.user.keymaps["3D View Tool: Edit Mesh, " + self.tool.bl_label]
        for item in keymap.keymap_items :
            if self.operator == item.idname and item.active :
                if [ item.shift , item.ctrl , item.alt,  item.oskey ] == [ shift , ctrl , alt,  oskey ] :
                    return item
        return None

    def invoke( self , context, event) :
        pass

    def get_attr( self , attr ) :
        if self.keyitem :
            if self.keyitem.properties.is_property_set(attr) :
                return getattr( self.keyitem.properties , attr )

        for tool in bpy.context.workspace.tools :
            if tool.idname is self.operator :
                props = tool.operator_properties(self.operator)
                return getattr( props , attr )

        return None

class PQ_GizmoGroup_Base(bpy.types.GizmoGroup):
    my_tool = ToolPolyQuiltBase
    bl_idname = "MESH_GGT_PQ_Preselect"
    bl_label = "PolyQuilt Preselect Gizmo"
    bl_options = {'3D'}    
    bl_region_type = 'WINDOW'
    bl_space_type = 'VIEW_3D'
    bl_idname = my_tool.bl_widget
    child_gizmos = []

    cursor = 'DEFAULT'

    running_polyquilt = False

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.gizmo = None

    def __del__(self) :
        gizmo = getattr( self , "gizmo" , None )
        if gizmo in PQ_GizmoGroup_Base.child_gizmos :
            PQ_GizmoGroup_Base.child_gizmos.remove( gizmo )
        if not PQ_GizmoGroup_Base.child_gizmos :
            QSnap.remove_ref()

    @classmethod
    def poll(cls, context):
        if context.mode != 'EDIT_MESH' :
            return False
        # 自分を使っているツールを探す。
        workspace = context.workspace
        for tool in workspace.tools:
            if tool.widget == cls.bl_idname:
                break
        else:
            context.window_manager.gizmo_group_type_unlink_delayed(cls.bl_idname)
            return False
        if not PQ_GizmoGroup_Base.running_polyquilt :
            context.window.cursor_set( cls.cursor )        
        return True

    def setup(self, context):
        QSnap.add_ref(context)
        self.gizmo = self.gizmos.new(PQ_Gizmo_Preselect.bl_idname)
        self.gizmo.init(context , self.my_tool )
        PQ_GizmoGroup_Base.child_gizmos.append(self.gizmo)

    def refresh( self , context ) :
        if hasattr( self , "gizmo" ) :        
            self.gizmo.refresh(context)

    @classmethod
    def set_cursor(cls, cursor = 'DEFAULT' ):
        cls.cursor = cursor

    @classmethod
    def get_gizmo(cls, region ):
        gizmo = [ i for i in cls.child_gizmos if i.region == region ]
        if gizmo :
            return gizmo[0]
        return None

    @classmethod
    def recive_event( cls , context , event ) :
        ret = False
        for gizmo in cls.child_gizmos :
            ret = ret | gizmo.recive_event( context , event)
        return ret

    @classmethod
    def depsgraph_update_post( cls , scene ) :
        for gizmo in cls.child_gizmos :
            gizmo.refresh( bpy.context )


class PQ_GizmoGroup_Preselect(PQ_GizmoGroup_Base):
    my_tool = ToolPolyQuilt
    bl_idname = my_tool.bl_widget
    bl_label = "PolyQuilt Preselect Gizmo"

class PQ_GizmoGroup_Lowpoly(PQ_GizmoGroup_Base):
    my_tool = ToolPolyQuiltPoly
    bl_idname = my_tool.bl_widget
    bl_label = "PolyQuilt Lowpoly Gizmo"

class PQ_GizmoGroup_Knife(PQ_GizmoGroup_Base):
    my_tool = ToolPolyQuiltKnife
    bl_idname = my_tool.bl_widget
    bl_label = "PolyQuilt Knife Gizmo"

class PQ_GizmoGroup_Delete(PQ_GizmoGroup_Base):
    my_tool = ToolPolyQuiltDelete
    bl_idname = my_tool.bl_widget
    bl_label = "PolyQuilt Delete Gizmo"

class PQ_GizmoGroup_Extrude(PQ_GizmoGroup_Base):
    my_tool = ToolPolyQuiltExtrude
    bl_idname = my_tool.bl_widget
    bl_label = "PolyQuilt Extrude Gizmo"

class PQ_GizmoGroup_EdgeLoop(PQ_GizmoGroup_Base):
    my_tool = ToolPolyQuiltEdgeLoop
    bl_idname = my_tool.bl_widget
    bl_label = "PolyQuilt Edgeloop Gizmo"

class PQ_GizmoGroup_LoopCut(PQ_GizmoGroup_Base):
    my_tool = ToolPolyQuiltLoopCut
    bl_idname = my_tool.bl_widget
    bl_label = "PolyQuilt LoopCut Gizmo"

class PQ_GizmoGroup_Brush(PQ_GizmoGroup_Base):
    my_tool = ToolPolyQuiltBrush
    bl_idname = my_tool.bl_widget
    bl_label = "PolyQuilt Brush Gizmo"

class PQ_GizmoGroup_Seam(PQ_GizmoGroup_Base):
    my_tool = ToolPolyQuiltSeam
    bl_idname = my_tool.bl_widget
    bl_label = "PolyQuilt Seam Gizmo"

class PQ_GizmoGroup_QuadPatch(PQ_GizmoGroup_Base):
    my_tool = ToolPolyQuiltQuadPatch
    bl_idname = my_tool.bl_widget
    bl_label = "PolyQuilt QuadOatch Gizmo"

all_gizmos = ( PQ_Gizmo_Preselect , PQ_GizmoGroup_Preselect , PQ_GizmoGroup_Lowpoly , PQ_GizmoGroup_Knife , PQ_GizmoGroup_Delete, PQ_GizmoGroup_Extrude, PQ_GizmoGroup_EdgeLoop, PQ_GizmoGroup_LoopCut, PQ_GizmoGroup_Brush, PQ_GizmoGroup_Seam, PQ_GizmoGroup_QuadPatch )



