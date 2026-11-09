#!/usr/bin/env bash
# Build the installable ZIP (manifest at the archive root, as extensions require).
# New files must be added to the list below.
set -euo pipefail
cd "$(dirname "$0")/bake_sets"
rm -f ../bake_sets-0.1.0.zip
zip -r ../bake_sets-0.1.0.zip blender_manifest.toml __init__.py maps.py props.py scene_copy.py \
  cycles_bake.py gl_maps.py pipeline.py operators.py ui.py -x '*/__pycache__/*'
