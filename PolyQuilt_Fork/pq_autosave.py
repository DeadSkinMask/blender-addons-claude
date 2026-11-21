# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 3 of the License, or
# (at your option) any later version.

"""Backup autosave for sessions where PolyQuilt is the active tool.

PolyQuilt keeps a modal operator (the "daemon") running the whole time its tool is active in Edit
Mode, and Blender postpones its own autosave while any modal operator is running. This module writes
the same kind of safety copy by itself in that situation.

It never touches the user's .blend: it saves a *copy* (`copy=True`, so the current filepath and the
dirty flag are untouched) into the temporary directory under a name Blender's own autosave never
uses, and refuses to write to the current file path.
"""

import os
import atexit
import tempfile
import time
import bpy

CHECK_INTERVAL = 15.0          # seconds between checks
SUFFIX = "_pq_autosave.blend"

_state = { "last" : time.time() , "path" : None , "saving" : False }


def target_path() :
    prefs = bpy.context.preferences.filepaths
    directory = prefs.temporary_directory or tempfile.gettempdir()
    if bpy.data.filepath :
        base = os.path.splitext( os.path.basename( bpy.data.filepath ) )[0]
    else :
        base = "untitled"
    return os.path.join( directory , "%s_%d%s" % ( base , os.getpid() , SUFFIX ) )


def _is_current_file( path ) :
    cur = bpy.data.filepath
    if not cur :
        return False
    return os.path.normcase( os.path.abspath( path ) ) == os.path.normcase( os.path.abspath( cur ) ) \
        or os.path.exists( path ) and os.path.exists( cur ) and os.path.samefile( path , cur )


def _busy() :
    """True while something other than the PolyQuilt daemon is running modally (drag, sculpt stroke...)."""
    from .gizmo_preselect import PQ_GizmoGroup_Base
    if PQ_GizmoGroup_Base.running_polyquilt :
        return True
    wm = bpy.context.window_manager
    for win in wm.windows :
        for op in win.modal_operators :
            if op.bl_idname != "NONE_OT_poly_quilt_daemon" :
                return True
    return False


def save_now( force = False ) :
    """Write the backup copy. Returns the path, or None if nothing was written."""
    path = target_path()
    if path.lower() == os.path.abspath( bpy.data.filepath ).lower() or not path.endswith( SUFFIX ) or _is_current_file( path ) :
        return None
    if not force and _busy() :
        return None
    _state["saving"] = True        # save_post also fires for copies: do not delete our own backup
    try :
        bpy.ops.wm.save_as_mainfile( filepath = path , copy = True , check_existing = False ,
                                     compress = False , relative_remap = False )
    except Exception as e :
        print( "PolyQuilt autosave failed:" , e )
        return None
    finally :
        _state["saving"] = False
    _state["path"] = path
    _state["last"] = time.time()
    return path


def _timer() :
    try :
        from .subtools.subtool_brush_pin import pin_state   # noqa: F401  (module import sanity)
        from .pq_operator import MESH_OT_poly_quilt_daemon
        prefs = bpy.context.preferences
        addon = prefs.addons.get( __package__ )
        enabled = addon is None or getattr( addon.preferences , "backup_autosave" , True )
        if enabled and MESH_OT_poly_quilt_daemon.is_running and prefs.filepaths.use_auto_save_temporary_files :
            minutes = max( 1 , prefs.filepaths.auto_save_time )
            if time.time() - _state["last"] >= minutes * 60.0 :
                if save_now() is None :
                    _state["last"] = time.time() - minutes * 60.0 + 10.0   # busy: retry in ~10 s
        else :
            _state["last"] = time.time()
    except Exception as e :
        print( "PolyQuilt autosave timer error:" , e )
    return CHECK_INTERVAL


def _remove_backup( *args ) :
    """The user saved (or Blender is closing normally): the backup is no longer needed."""
    if _state["saving"] :
        return
    path = _state.get( "path" )
    if path and path.endswith( SUFFIX ) and os.path.isfile( path ) :
        try :
            os.remove( path )
        except OSError :
            pass
    _state["path"] = None


def register() :
    _state["last"] = time.time()
    if not bpy.app.timers.is_registered( _timer ) :
        bpy.app.timers.register( _timer , first_interval = CHECK_INTERVAL , persistent = True )
    if _remove_backup not in bpy.app.handlers.save_post :
        bpy.app.handlers.save_post.append( _remove_backup )
    atexit.register( _remove_backup )


def unregister() :
    if bpy.app.timers.is_registered( _timer ) :
        bpy.app.timers.unregister( _timer )
    while _remove_backup in bpy.app.handlers.save_post :
        bpy.app.handlers.save_post.remove( _remove_backup )
    atexit.unregister( _remove_backup )
