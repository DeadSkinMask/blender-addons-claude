#!/usr/bin/env bash
# Builds, tests and packages everything. Usage: ./release.sh [path/to/blender]
# Output in dist/release/: the addon ZIP without an engine, and one per platform with the engine inside (bin/).
set -euo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
BLENDER="${1:-$(command -v blender)}"
VERSION="$(sed -n 's/^version = "\(.*\)"/\1/p' "$here/addon/rustremesher/blender_manifest.toml")"
out="$here/dist/release"

echo "== engine: build, tests, clippy"
"$here/engine/build.sh" >/dev/null
(cd "$here/engine" && cargo test --release 2>&1 | grep -E '^test result' ; cargo clippy --release --all-targets 2>&1 | grep -c '^warning' | sed 's/^/clippy warnings: /' || true)
echo "== engine: Windows exe under wine"
"$here/engine/wine_smoke.sh" 2>&1 | grep -E '^OK|rror'
echo "== addon: headless tests"
"$here/addon/run_tests.sh" "$BLENDER" 2>&1 | grep -E '^\[FAIL|passed'
"$BLENDER" --command extension validate "$here/addon/rustremesher" 2>&1 | tail -1

echo "== packaging"
rm -rf "$out"; mkdir -p "$out"
"$here/addon/build_zip.sh" >/dev/null
cp "$here/addon/rustremesher-$VERSION.zip" "$out/"
bundle() { # platform, engine file
  local platform="$1" engine="$2" tmp
  tmp="$(mktemp -d)"
  cp -r "$here/addon/rustremesher/." "$tmp/"
  rm -rf "$tmp/tests" "$tmp/__pycache__"
  mkdir "$tmp/bin"; cp "$here/dist/$engine" "$tmp/bin/"; chmod +x "$tmp/bin/$engine"
  # platforms is a top-level key: it goes before the first table
  python3 - "$tmp/blender_manifest.toml" "$platform" <<'PY'
import sys
path, platform = sys.argv[1:]
text = open(path).read().replace("\n[permissions]", '\nplatforms = ["%s"]\n\n[permissions]' % platform, 1)
open(path, "w").write(text)
PY
  (cd "$tmp" && zip -qr "$out/rustremesher-$VERSION-$platform.zip" blender_manifest.toml __init__.py README.md exchange.py blender_io.py runner.py props.py prefs.py guides.py operators.py ui.py "bin/$engine" -x '*/__pycache__/*')
  "$BLENDER" --command extension validate "$tmp" 2>&1 | tail -1
  rm -rf "$tmp"
}
bundle linux-x64 rustremesher
bundle windows-x64 rustremesher.exe

echo "== bundled ZIP works with no engine path set (Linux)"
tmp="$(mktemp -d)"; trap 'rm -rf "$tmp"' EXIT
export BLENDER_USER_CONFIG="$tmp/config" BLENDER_USER_EXTENSIONS="$tmp/ext"; mkdir -p "$tmp/config" "$tmp/ext"
"$BLENDER" --background --factory-startup --python "$here/addon/rustremesher/tests/test_bundled.py" -- "$out/rustremesher-$VERSION-linux-x64.zip" 2>&1 | grep -E 'BUNDLED OK|Error|Traceback'
ls -la "$out"
