# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Layout

Git repo `blender-addons-claude` (remote `origin` on GitHub, branch `master`): a folder of independent Blender 5.2 addon projects, one subfolder each. Built ZIPs are committed (the repo is for addons in general and the user installs from them); `test_log.txt` and screenshots are gitignored. Keep projects self-contained; don't share code between them. Code and comments in English, user-facing UI strings in Spanish (Sculpt Brush Shelf, Godot Level Bridge) — match the project you are in.

Blender 5.2.3 is installed at `/opt/blender-5.2.3-linux-x64/blender` (also `blender` on PATH). Target API is Blender 5.2 only; verify API names by running Blender (`blender -b --factory-startup --python-expr ...`), not from older docs.

## Sculpt Brush Shelf

Extension-format addon (`blender_manifest.toml`, no `bl_info`) that shows a user-defined short list of Sculpt Mode brushes in the 3D View sidebar (`N` → "Pinceles").

```bash
cd "Sculpt Brush Shelf"
./run_tests.sh /opt/blender-5.2.3-linux-x64/blender   # builds ZIP, runs headless tests, writes test_log.txt
./build_zip.sh                                         # only rebuild sculpt_brush_shelf-1.0.0.zip
```

- Tests are a single script (`sculpt_brush_shelf/tests/test_headless.py`) with named `check(...)` steps; there is no per-test selector — comment out entries in the list at the bottom to run a subset. It exits non-zero on any FAIL.
- `run_tests.sh` installs the ZIP as an extension into a temp `BLENDER_USER_CONFIG`/`BLENDER_USER_EXTENSIONS`, so the module under test is `bl_ext.user_default.sculpt_brush_shelf` and the user's real preferences are untouched. Always test the built ZIP, not the source folder.
- New files must be added to the `zip` command in `build_zip.sh` (it lists files explicitly). Bump the version in both `blender_manifest.toml` and `build_zip.sh`.
- Validate the manifest with `blender --command extension validate sculpt_brush_shelf`.
- Panel drawing, button highlight and preference persistence cannot be tested headless; the manual GUI checklist is in the project README.

Non-obvious API facts this addon depends on (verified on 5.2.3):
- Built-in brushes are assets; `bpy.data.brushes` is empty at startup. They are activated with `bpy.ops.brush.asset_activate(asset_library_type='ESSENTIALS', relative_asset_identifier='brushes/essentials_brushes-mesh_sculpt.blend/Brush/<Name>')`, which works in `--background` once in Sculpt Mode.
- Brush names are enumerated by `bpy.data.libraries.load(<DATAFILES>/assets/brushes/essentials_brushes-mesh_sculpt.blend, assets_only=True)`. Real names differ from the UI shorthand: `Inflate/Deflate`, `Pinch/Magnify`, `Flatten/Contrast`, `Crease Sharp`, `Draw Sharp`, `Relax Pinch`.
- Preferences cannot be written while registering, and no timers are used. Defaults are therefore applied lazily: `prefs.customized == False` means "use `DEFAULT_BRUSHES`"; any edit operator calls `materialize()` first to copy the defaults into the stored collection. Changing `DEFAULT_BRUSHES` only reaches users who have not customized the list.
- Dynamic enum items (`SCULPTSHELF_OT_add`) are kept in the module-level `_enum_items_ref` to avoid garbled strings.
- The native asset shelf cannot be filtered via public API, hence the custom panel.

`INSTRUCCIONES_ADDON_SCULPT_BRUSH_SHELF.md` is the original spec (written by GPT); its brush names and "tool region" panel placement were wrong, the implementation above supersedes it.

## Godot MultiMesh Scatter

Two files that form one pipeline and must stay in sync on the binary format. User-facing docs (setup, inspector properties, coordinate convention, troubleshooting) live in `README_Godot_MultiMesh_Scatter.md`; keep it updated when behavior changes.

- `godot_multimesh_scatter.py` (v1.1.0) — legacy addon (`bl_info`), installed by loading the single file via Preferences → Add-ons → Install from Disk. Sidebar tab "Godot". Exports a Geometry Nodes scatter host's instances as `scatter_transforms.bin` plus the source mesh as GLB, OBJ or both (`MESH_EXPORTERS` maps format → exporter).
- `GodotScatterBinaryLoader.gd` — `@tool` Godot 4 node (4.4+ for the Rebuild button) that builds a `MultiMeshInstance3D` from those two files, with an editor debug preview.

Contract shared by both: file starts with magic `GMMI0001`, then `uint32` instance count, then per instance 4 little-endian `float32` vec3 (basis columns x, y, z, then origin) already converted to Godot space (Blender `(x, y, z)` → Godot `(x, z, -y)`, via `C · M · C⁻¹` in `blender_to_godot_matrix`). Changing the format means editing the writer (`write_transforms`) and the loader together and bumping the magic. Mesh exporter axes (GLB `export_yup`, OBJ `up_axis='Y'`/`forward_axis='NEGATIVE_Z'`) must stay consistent with that same convention.

Gotchas: instance matrices come from `depsgraph.object_instances` and must be copied during iteration (items are only valid then). The mesh exporters change the selection, so `execute` restores the host as active afterwards. OBJ bakes the source object's transform into vertices, so the source mesh should have an identity transform. Export only works in Object Mode (operator `poll`). No automated tests exist for this project; it was used in practice before being formalized.

## Godot Level Bridge

Extension-format addon that exports a Blender level (one room per `.blend`) to a Godot 4.7 project as a manifest + GLBs; the Godot side is the `addons/level_bridge/` plugin inside the game repo `/home/luis/GIT/personal/Godot/elegia/` (its own git repo, with its own CLAUDE.md and tests). Docs in Spanish.

- `ANALISIS_Level_Bridge.md`: design and phases B0–B7. `FORMAT.md`: the manifest contract as implemented; change writer and reader together and update it. The joint order with the game phases is in `elegia/docs/plan_de_fases.md`.
- Current phase: **B0–B2 implemented** (addon 0.2.0): scope, uids, validation, static GLB via staging copies, placeholders/markers/colliders/areas/paths. Module map: `scope.py` (what is exported + roles), `entities.py` (manifest entities, shapes, props, uids), `validate.py`, `static_export.py` (staging + glTF), `coords.py` (Blender→Godot), `godot_project.py` (layer names from project.godot), `export.py` (operators + orchestration), `ui.py`.

```bash
cd "Godot Level Bridge"
./run_tests.sh /opt/blender-5.2.3-linux-x64/blender   # builds ZIP, runs headless tests, writes test_log.txt
blender --command extension validate godot_level_bridge
/home/luis/GIT/personal/Godot/elegia/tests/run_all.sh  # Godot side
```

- Same test mechanics as Sculpt Brush Shelf (ZIP installed into a temp `BLENDER_USER_CONFIG`; module `bl_ext.user_default.godot_level_bridge`; `check(...)` list at the bottom of `tests/test_headless.py`). The test's second argument is a temp work dir where it fakes a Godot project.
- Fixtures shared with the game: `t_matches_godot_empty_fixture` and `t_sample_fixture_for_godot` compare Blender's output with `elegia/tests/fixtures/` (path via `GODOT_FIXTURES`). After changing the format run `UPDATE_FIXTURES=1 ./run_tests.sh …` to rewrite `fixtures/sample/` (manifest + GLB), then run the Godot tests. The sample level uses fixed uids so the output is deterministic.
- New files must be added to the `zip` command in `build_zip.sh`; bump the version there and in `blender_manifest.toml` (tagline max 64 chars).
- Upgrading on the user's machine: installing a new ZIP over the old one left the old code running (seen with 0.2.1→0.2.2 in the GUI; not reproducible headless with `package_install_files`). The documented procedure is Uninstall first, then install. When a fix "doesn't work" for the user, first check which version produced the output (e.g. the glTF scene name).
- Releasing a version: also copy the built ZIP to `/home/luis/GIT/personal/Godot/elegia/tools/blender/` (replacing the old one), since the user installs it from the game repo on another PC.
- Operators that `report({'ERROR'})` raise `RuntimeError` when called from Python; tests assert on that.
- The exporter must never modify the user's `.blend` data: static geometry goes through copies in a temporary scene (`static_export.py`), the original object is renamed only during the glTF export so the copy keeps its name (restored in `finally`), and the window scene is restored because the glTF exporter switches it. Only `gbridge.uid`/`uid_owner` are written. Files are written only when their content changes.
- Call `view_layer.update()` before reading `matrix_world` (validation does): transforms edited from Python are stale until then.
- `GLTFState.filename` in Godot has no extension (matters for the import extension's file filter).
