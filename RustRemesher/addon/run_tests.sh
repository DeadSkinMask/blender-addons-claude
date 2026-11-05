#!/usr/bin/env bash
# Usage: ./run_tests.sh [path/to/blender] [path/to/rustremesher]
# Builds the ZIP and runs the headless tests against the real engine (default ../dist/rustremesher; build it with
# ../engine/build.sh). The ZIP is installed as an extension into a temporary config, so the user's preferences stay.
set -euo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
BLENDER="${1:-$(command -v blender)}"
ENGINE="${2:-$here/../dist/rustremesher}"
"$here/build_zip.sh" >/dev/null
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT
export BLENDER_USER_CONFIG="$tmp/config" BLENDER_USER_EXTENSIONS="$tmp/ext"
mkdir -p "$BLENDER_USER_CONFIG" "$BLENDER_USER_EXTENSIONS"
zip_path="$(ls "$here"/rustremesher-*.zip)"
"$BLENDER" --background --factory-startup --python "$here/rustremesher/tests/test_headless.py" \
  -- "$zip_path" "$ENGINE" 2>&1 | tee "$here/test_log.txt"
exit "${PIPESTATUS[0]}"
