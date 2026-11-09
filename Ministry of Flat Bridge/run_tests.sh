#!/usr/bin/env bash
# Usage: ./run_tests.sh [path/to/blender] [path/to/UnWrapConsole3.exe]
# Builds the ZIP and runs the headless tests against the real tool (default: unzipped from ~/GIT/personal/MinistryOfFlat_Release.zip).
# The ZIP is installed as an extension into a temporary config, so the user's preferences stay untouched.
set -euo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
BLENDER="${1:-$(command -v blender)}"
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT
EXE="${2:-}"
if [ -z "$EXE" ]; then
  unzip -q "$HOME/GIT/personal/MinistryOfFlat_Release.zip" -d "$tmp/mof"
  EXE="$(find "$tmp/mof" -iname UnWrapConsole3.exe | head -1)"
fi
"$here/build_zip.sh" >/dev/null
export BLENDER_USER_CONFIG="$tmp/config" BLENDER_USER_EXTENSIONS="$tmp/ext"
mkdir -p "$BLENDER_USER_CONFIG" "$BLENDER_USER_EXTENSIONS"
zip_path="$(ls "$here"/ministry_of_flat_bridge-*.zip)"
"$BLENDER" --background --factory-startup --python "$here/ministry_of_flat_bridge/tests/test_headless.py" \
  -- "$zip_path" "$EXE" 2>&1 | tee "$here/test_log.txt"
exit "${PIPESTATUS[0]}"
