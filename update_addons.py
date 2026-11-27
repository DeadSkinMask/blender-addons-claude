#!/usr/bin/env python3
"""Install / update the extensions built in this repo through Blender's own command line.

    python update_addons.py [names...] [--all] [--list] [--force] [--dry-run] [--blender PATH]

It scans the repo for ZIPs that contain a blender_manifest.toml, keeps the newest build of each
extension (for RustRemesher the one matching this OS, with the engine, if there is one) and
compares its version with the installed one (path asked to Blender, so BLENDER_USER_EXTENSIONS
and portable setups work).

* no names:      update the extensions that are already installed (also the ones still installed
                 under an old id: they are migrated); list the ones that are not
* names:         only those (match on id or name, case-insensitive substring) - installed or not
* --all:         also install the extensions that are not installed yet, except the OPTIONAL ones
                 (see below); --include-optional adds those too
* --list:        show the state of every extension and exit
* --force:       install even if the version is the same or older
* --dry-run:     only print the Blender commands
* --blender:     blender executable (else $BLENDER_EXE, else the newest
                 "Program Files/Blender Foundation/Blender *" on Windows, else `blender` on PATH)

Optional extensions (OPTIONAL below) are meant for specific PCs: --all does not install them,
but they are updated when already installed and installed when named explicitly.

Each install removes the previous one first (installing over it can leave the old code running),
then installs into the user_default repository and enables it. Add-on preferences are kept
across updates with the same id.
"""

import argparse
import glob
import os
import platform
import re
import shutil
import subprocess
import sys
import zipfile

REPO = "user_default"
HERE = os.path.dirname(os.path.abspath(__file__))
SKIP_DIRS = {".git", "target", "node_modules", "__pycache__", ".venv"}
# only wanted on some PCs: never installed by --all, only when named (or already installed)
OPTIONAL = {"sculpt_brush_shelf", "rustremesher"}
# ids that an extension used to have: removed together with the current one
OLD_IDS = {"polyquilt_luis_fork": ["PolyQuilt_Fork"], "retopoflow_luis_fork": ["retopoflow"]}


def vkey(version):
    return tuple(int(p) for p in re.findall(r"\d+", version or "")[:3]) or (0,)


def parse_manifest(text):
    def field(name):
        m = re.search(r'(?m)^%s\s*=\s*"([^"]+)"' % name, text)
        return m.group(1) if m else None
    m = re.search(r"(?m)^platforms\s*=\s*\[([^\]]*)\]", text)
    platforms = re.findall(r'"([^"]+)"', m.group(1)) if m else []
    return {"id": field("id"), "version": field("version"), "name": field("name"), "platforms": platforms}


def this_platform():
    machine = platform.machine().lower()
    arm = machine in ("arm64", "aarch64")
    if sys.platform.startswith("win"):
        return "windows-arm64" if arm else "windows-x64"
    if sys.platform == "darwin":
        return "macos-arm64" if arm else "macos-x64"
    return "linux-arm64" if arm else "linux-x64"


def scan_zips():
    """Best ZIP per extension id: highest version, then platform-specific for this OS, then generic."""
    best = {}
    me = this_platform()
    for root, dirs, files in os.walk(HERE):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        for f in files:
            if not f.lower().endswith(".zip"):
                continue
            path = os.path.join(root, f)
            try:
                with zipfile.ZipFile(path) as z:
                    names = z.namelist()
                    if "blender_manifest.toml" not in names:
                        continue
                    info = parse_manifest(z.read("blender_manifest.toml").decode("utf-8"))
            except (zipfile.BadZipFile, OSError):
                continue
            if not info["id"] or not info["version"]:
                continue
            if info["platforms"] and me not in info["platforms"]:
                continue                      # a build for another OS
            rank = (vkey(info["version"]), 1 if info["platforms"] else 0)
            if info["id"] not in best or rank > best[info["id"]]["rank"]:
                info.update(path=path, rank=rank)
                best[info["id"]] = info
    return best


def find_blender(arg):
    if arg:
        return arg
    if os.environ.get("BLENDER_EXE"):
        return os.environ["BLENDER_EXE"]
    if os.name == "nt":
        root = os.path.join(os.environ.get("ProgramFiles", r"C:\Program Files"), "Blender Foundation")
        found = sorted(glob.glob(os.path.join(root, "Blender *", "blender.exe")),
                       key=lambda p: vkey(os.path.basename(os.path.dirname(p))), reverse=True)
        if found:
            return found[0]
    return shutil.which("blender")


def extensions_dir(blender):
    out = subprocess.run(
        [blender, "-b", "--python-expr",
         "import bpy;print('PQ_EXT=' + bpy.utils.user_resource('EXTENSIONS'))"],
        # other add-ons print UTF-8 (box drawing...) that the Windows code page (cp1252) cannot decode
        capture_output=True, encoding="utf-8", errors="replace").stdout or ""
    m = re.search(r"PQ_EXT=(.+)", out)
    return m.group(1).strip() if m else None


def installed_version(ext_dir, ext_id, repo=REPO):
    if not ext_dir:
        return None
    path = os.path.join(ext_dir, repo, ext_id, "blender_manifest.toml")
    if not os.path.isfile(path):
        return None
    with open(path, encoding="utf-8") as f:
        return parse_manifest(f.read())["version"]


def run(cmd, dry):
    print("    $", " ".join('"%s"' % c if " " in c else c for c in cmd))
    return 0 if dry else subprocess.run(cmd).returncode


def state(info, ext_dir):
    old = installed_version(ext_dir, info["id"])
    if old is None:
        for rid in OLD_IDS.get(info["id"], []):
            if installed_version(ext_dir, rid) is not None:
                return "update available (replaces old id %s)" % rid, installed_version(ext_dir, rid)
        return "not installed", old
    a, b = vkey(old), vkey(info["version"])
    return ("up to date" if a == b else "update available" if a < b else "installed is NEWER"), old


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("names", nargs="*")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--include-optional", action="store_true",
                    help="let --all install the OPTIONAL extensions too")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--blender")
    args = ap.parse_args()

    blender = find_blender(args.blender)
    if not blender or not (os.path.exists(blender) or shutil.which(blender)):
        sys.exit("[ERROR] blender not found. Use --blender PATH.")
    zips = scan_zips()
    if not zips:
        sys.exit("[ERROR] no extension ZIPs found under %s" % HERE)
    ext_dir = extensions_dir(blender)

    print("Blender:   ", blender)
    print("Extensions:", ext_dir or "?")
    print("Platform:  ", this_platform(), "\n")

    rows = []
    for ext_id, info in sorted(zips.items()):
        st, old = state(info, ext_dir)
        rows.append((ext_id, info, st, old))
        print("%-26s zip %-8s installed %-9s %s%s" % (ext_id, info["version"], old or "-", st,
              "  [optional]" if ext_id in OPTIONAL else ""))
    if args.list:
        return 0

    wanted = [n.lower() for n in args.names]
    chosen = []
    for ext_id, info, st, old in rows:
        if wanted:
            if not any(w in ext_id.lower() or w in (info["name"] or "").lower() for w in wanted):
                continue
        elif st == "not installed" and (not args.all or (ext_id in OPTIONAL and not args.include_optional)):
            continue
        if st in ("up to date", "installed is NEWER") and not args.force:
            continue
        chosen.append((ext_id, info, st))
    missing = [w for w in wanted if not any(w in i.lower() or w in (zips[i]["name"] or "").lower() for i in zips)]
    for w in missing:
        print("\n[WARNING] no extension matches %r" % w)

    skipped = [i for i, _, st, _ in rows if i in OPTIONAL and st == "not installed"]
    if skipped and not wanted and not args.include_optional:
        print("\nOptional, not installed (name them to install): " + ", ".join(skipped))

    if not chosen:
        print("\nNothing to do.%s" % ("" if wanted or args.all else
              "  (extensions that are not installed need --all or their name)"))
        return 0

    failed = []
    for ext_id, info, st in chosen:
        print("\n== %s %s  (%s)" % (ext_id, info["version"], st))
        if os.path.isdir(os.path.join(ext_dir or "", "blender_org", ext_id)):
            print("  [WARNING] %s is also installed from extensions.blender.org: uninstall that copy "
                  "in Preferences first, or Blender will see two with the same id." % ext_id)
        for rid in [ext_id] + OLD_IDS.get(ext_id, []):
            if installed_version(ext_dir, rid) is not None or args.dry_run:
                run([blender, "--command", "extension", "remove", rid], args.dry_run)
        if run([blender, "--command", "extension", "install-file", "-r", REPO, "-e", info["path"]],
               args.dry_run):
            failed.append(ext_id)
            print("  [ERROR] install failed")
            continue
        if not args.dry_run:
            now = installed_version(ext_dir, ext_id)
            if now == info["version"]:
                print("  OK: now %s" % now)
            else:
                failed.append(ext_id)
                print("  [WARNING] installed version reads %r, expected %s" % (now, info["version"]))

    print("\nDone.%s Restart Blender if it was open." % (" Failed: " + ", ".join(failed) + "." if failed else ""))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
