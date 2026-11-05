#!/usr/bin/env bash
# Smoke test of dist/rustremesher.exe under Wine (needs ./build.sh first). Wine older than 7 lacks
# bcryptprimitives.dll (Windows 10+ has it), so a test-only stand-in DLL is built and put next to a COPY of the exe.
set -euo pipefail
cd "$(dirname "$0")"
WINE=${WINE:-wine64}
work=$(mktemp -d); trap 'rm -rf "$work"' EXIT
export WINEDEBUG=-all WINEPREFIX="$work/prefix"

(cd tools/bcryptprimitives_shim && RUSTFLAGS="-C target-feature=+crt-static" \
  cargo xwin build --release --target x86_64-pc-windows-msvc >/dev/null 2>&1)
cp ../dist/rustremesher.exe tools/bcryptprimitives_shim/target/x86_64-pc-windows-msvc/release/bcryptprimitives.dll "$work/"
exe="$WINE $work/rustremesher.exe"

printf 'v -1 -1 -1\nv 1 -1 -1\nv 1 1 -1\nv -1 1 -1\nv -1 -1 1\nv 1 -1 1\nv 1 1 1\nv -1 1 1\nf 1 4 3 2\nf 5 6 7 8\nf 1 2 6 5\nf 3 4 8 7\nf 2 3 7 6\nf 4 1 5 8\n' > "$work/cube.obj"
$exe --convert "Z:$work/cube.obj" "Z:$work/cube.rrm"
printf 'FileIn="Z:%s/cube.rrm"\r\nFileOut="Z:%s/out.rrm"\r\nProgressFile="Z:%s/progress.txt"\r\n' "$work" "$work" "$work" > "$work/s.txt"
$exe -s "Z:$work/s.txt"
[ "$(cat "$work/progress.txt")" = "2" ] || { echo "FAIL: progress is not 2"; exit 1; }
[ "$(head -c 8 "$work/out.rrm")" = "RRMO0001" ] || { echo "FAIL: bad output"; exit 1; }
$exe --info "Z:$work/out.rrm" | grep -q "6 quads" || { echo "FAIL: quads lost"; exit 1; }
echo "OK: rustremesher.exe works under $WINE"
