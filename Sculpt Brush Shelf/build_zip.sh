#!/usr/bin/env bash
# Build the installable ZIP (manifest at the archive root, as extensions require).
set -euo pipefail
cd "$(dirname "$0")/sculpt_brush_shelf"
rm -f ../sculpt_brush_shelf-1.0.0.zip
zip -r ../sculpt_brush_shelf-1.0.0.zip blender_manifest.toml __init__.py README.md -x '*/__pycache__/*'
