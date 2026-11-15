#!/bin/bash
set -e
cd "$(dirname "$0")"
rm -f selection_to_mask-1.0.0.zip
(cd selection_to_mask && zip -q ../selection_to_mask-1.0.0.zip __init__.py blender_manifest.toml)
echo "built selection_to_mask-1.0.0.zip"
