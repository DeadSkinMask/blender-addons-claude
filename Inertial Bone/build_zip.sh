#!/bin/bash
set -e
cd "$(dirname "$0")"
rm -f inertial_bone-1.0.0.zip
(cd inertial_bone && zip -q ../inertial_bone-1.0.0.zip __init__.py blender_manifest.toml sim.py props.py runtime.py operators.py ui.py)
echo "built inertial_bone-1.0.0.zip"
