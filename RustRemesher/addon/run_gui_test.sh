#!/usr/bin/env bash
# Optional GUI test (opens a Blender window for a few seconds; needs a display and, for the Esc check, xdotool).
# Usage: ./run_gui_test.sh [path/to/blender] [path/to/rustremesher]
set -euo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
BLENDER="${1:-$(command -v blender)}"
ENGINE="${2:-$here/../dist/rustremesher}"
"$here/build_zip.sh" >/dev/null
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT
export BLENDER_USER_CONFIG="$tmp/config" BLENDER_USER_EXTENSIONS="$tmp/ext"
mkdir -p "$BLENDER_USER_CONFIG" "$BLENDER_USER_EXTENSIONS"
timeout 240 "$BLENDER" --factory-startup --python "$here/rustremesher/tests/test_gui.py" \
  -- "$(ls "$here"/rustremesher-*.zip)" "$ENGINE" "$tmp/result.txt" >"$tmp/blender.log" 2>&1 || true
cat "$tmp/result.txt" 2>/dev/null || { echo "no result file"; tail -20 "$tmp/blender.log"; exit 1; }
grep -q '^OK$' "$tmp/result.txt"
