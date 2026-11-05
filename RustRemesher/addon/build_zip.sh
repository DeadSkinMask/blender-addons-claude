#!/usr/bin/env bash
# Build the installable ZIP (manifest at the archive root, as extensions require).
set -euo pipefail
VERSION="0.1.0"
cd "$(dirname "$0")/rustremesher"
rm -f ../rustremesher-*.zip
zip -r "../rustremesher-$VERSION.zip" \
  blender_manifest.toml __init__.py README.md exchange.py blender_io.py runner.py props.py prefs.py guides.py \
  operators.py ui.py \
  -x '*/__pycache__/*'
