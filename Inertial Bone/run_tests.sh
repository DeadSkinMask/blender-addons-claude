#!/bin/bash
set -e
BLENDER="${1:-blender}"
cd "$(dirname "$0")"
./build_zip.sh
T=$(mktemp -d)
export BLENDER_USER_CONFIG="$T/cfg" BLENDER_USER_EXTENSIONS="$T/ext"
mkdir -p "$BLENDER_USER_CONFIG" "$BLENDER_USER_EXTENSIONS"
"$BLENDER" -b --factory-startup --python inertial_bone/tests/test_headless.py -- "$PWD/inertial_bone-1.1.1.zip" 2>&1 | tee test_log.txt | grep -E "PASS|FAIL|Error|Traceback"
! grep -q FAIL test_log.txt
