#!/usr/bin/env bash
# Build the installable ZIP (manifest at the archive root, as extensions require).
set -euo pipefail
VERSION="0.2.2"
cd "$(dirname "$0")/godot_level_bridge"
rm -f ../godot_level_bridge-*.zip
zip -r "../godot_level_bridge-$VERSION.zip" \
  blender_manifest.toml __init__.py README.md props.py paths.py manifest.py export.py ui.py \
  coords.py godot_project.py scope.py entities.py validate.py static_export.py \
  -x '*/__pycache__/*'
