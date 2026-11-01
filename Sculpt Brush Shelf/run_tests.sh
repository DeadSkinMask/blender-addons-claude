#!/usr/bin/env bash
# Usage: ./run_tests.sh [path/to/blender]   (defaults to `blender` on PATH)
set -euo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
BLENDER="${1:-$(command -v blender)}"
"$here/build_zip.sh" >/dev/null
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT
# Isolated config + extension repo so the user's real preferences are untouched.
export BLENDER_USER_CONFIG="$tmp/config" BLENDER_USER_EXTENSIONS="$tmp/ext"
mkdir -p "$BLENDER_USER_CONFIG" "$BLENDER_USER_EXTENSIONS"
"$BLENDER" --background --factory-startup --python "$here/sculpt_brush_shelf/tests/test_headless.py" \
  -- "$here/sculpt_brush_shelf-1.0.0.zip" 2>&1 | tee "$here/test_log.txt"
exit "${PIPESTATUS[0]}"
