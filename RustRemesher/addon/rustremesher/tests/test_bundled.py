# Used by release.sh: installs a ZIP that carries the engine in bin/ and remeshes a cube with NO engine path set.
import sys

import bpy

ZIP = sys.argv[sys.argv.index("--") + 1]
r = bpy.ops.extensions.package_install_files(filepath=ZIP, repo="user_default", enable_on_install=True)
assert r == {'FINISHED'}, r
name = "bl_ext.user_default.rustremesher"
assert bpy.context.preferences.addons[name].preferences.engine_path == ""
bpy.context.scene.rustremesher.target_quads = 200
assert bpy.ops.rustremesher.check_engine() == {'FINISHED'}
r = bpy.ops.rustremesher.remesh()
assert r == {'FINISHED'}, r
new = bpy.context.object
assert new.name == "Cube_remesh" and all(p.loop_total == 4 for p in new.data.polygons) and len(new.data.polygons) > 100
print("BUNDLED OK", len(new.data.polygons))
