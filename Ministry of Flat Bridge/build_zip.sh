#!/usr/bin/env bash
# Build the installable ZIP (manifest at the archive root, as extensions require). The unwrapper is NOT included.
set -euo pipefail
VERSION="0.2.0"
cd "$(dirname "$0")/ministry_of_flat_bridge"
rm -f ../ministry_of_flat_bridge-*.zip
zip -r "../ministry_of_flat_bridge-$VERSION.zip" \
  blender_manifest.toml __init__.py README.md obj_exchange.py runner.py props.py prefs.py operators.py ui.py \
  -x '*/__pycache__/*'
