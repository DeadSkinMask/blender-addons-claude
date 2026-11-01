#!/usr/bin/env bash
# Usage: ./run_tests.sh [path/to/blender]   (defaults to `blender` on PATH)
# GODOT_FIXTURES may point at the game's tests/fixtures/ to check that Blender's
# output matches the manifests the Godot tests use (skipped if the folder is missing).
# UPDATE_FIXTURES=1 overwrites those fixtures with what Blender produces now.
set -euo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
BLENDER="${1:-$(command -v blender)}"
"$here/build_zip.sh" >/dev/null
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT
# Isolated config + extension repo so the user's real preferences are untouched.
export BLENDER_USER_CONFIG="$tmp/config" BLENDER_USER_EXTENSIONS="$tmp/ext"
export GODOT_FIXTURES="${GODOT_FIXTURES:-/home/luis/GIT/personal/Godot/elegia/tests/fixtures}"
mkdir -p "$BLENDER_USER_CONFIG" "$BLENDER_USER_EXTENSIONS"
zip_path="$(ls "$here"/godot_level_bridge-*.zip)"
"$BLENDER" --background --factory-startup --python "$here/godot_level_bridge/tests/test_headless.py" \
  -- "$zip_path" "$tmp/work" 2>&1 | tee "$here/test_log.txt"
exit "${PIPESTATUS[0]}"
