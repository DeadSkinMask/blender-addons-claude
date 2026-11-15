#!/bin/bash
set -e
BLENDER="${1:-blender}"
cd "$(dirname "$0")"
./build_zip.sh
T=$(mktemp -d)
export BLENDER_USER_CONFIG="$T/cfg" BLENDER_USER_EXTENSIONS="$T/ext"
mkdir -p "$BLENDER_USER_CONFIG" "$BLENDER_USER_EXTENSIONS"
"$BLENDER" -b --factory-startup --python selection_to_mask/tests/test_headless.py -- "$PWD/selection_to_mask-1.0.0.zip" 2>&1 | tee test_log.txt | grep -E "PASS|FAIL|Error|Traceback" 
! grep -q FAIL test_log.txt
