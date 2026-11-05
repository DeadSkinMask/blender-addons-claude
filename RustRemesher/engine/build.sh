#!/usr/bin/env bash
# Builds the engine for Linux and Windows (cross-compiled with cargo-xwin, static CRT so the .exe needs no
# Visual C++ redistributable) into ../dist/.
set -euo pipefail
cd "$(dirname "$0")"
mkdir -p ../dist

cargo build --release
cp target/release/rustremesher ../dist/rustremesher

RUSTFLAGS="-C target-feature=+crt-static" \
  cargo xwin build --release --target x86_64-pc-windows-msvc
cp target/x86_64-pc-windows-msvc/release/rustremesher.exe ../dist/rustremesher.exe

ls -l ../dist
