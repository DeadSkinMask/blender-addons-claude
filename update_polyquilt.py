#!/usr/bin/env python3
"""Install / update "PolyQuilt Luis Fork" through Blender's own command line (no UI, no PowerShell).

    python update_polyquilt.py [--blender PATH] [--zip PATH] [--force] [--dry-run]

* Blender: --blender, else $BLENDER_EXE, else the newest "Program Files/Blender Foundation/Blender *"
  (Windows), else `blender` on PATH.
* ZIP: --zip, else the newest polyquilt_luis_fork-*.zip next to this script.
* The version inside the ZIP's blender_manifest.toml is compared with the installed manifest
  (location asked to that Blender, so BLENDER_USER_EXTENSIONS and portable setups work).
  Same version: nothing to do. Installed newer: stops. --force installs anyway.
* The previous install is removed first (installing over it can leave the old code running);
  the old id "PolyQuilt_Fork" is removed too. Then the ZIP is installed into the user_default
  repository and enabled.
"""

import argparse
import glob
import os
import re
import shutil
import subprocess
import sys
import zipfile

ID = "polyquilt_luis_fork"
OLD_IDS = ("PolyQuilt_Fork",)
REPO = "user_default"
HERE = os.path.dirname(os.path.abspath(__file__))


def vkey(version):
    return tuple(int(p) for p in re.findall(r"\d+", version)[:3])


def manifest_version(text):
    m = re.search(r'(?m)^version\s*=\s*"([^"]+)"', text)
    return m.group(1) if m else None


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


def find_zip(arg):
    if arg:
        return arg
    found = sorted(glob.glob(os.path.join(HERE, ID + "-*.zip")),
                   key=lambda p: vkey(os.path.basename(p)), reverse=True)
    return found[0] if found else None


def zip_version(path):
    with zipfile.ZipFile(path) as z:
        return manifest_version(z.read("blender_manifest.toml").decode("utf-8"))


def extensions_dir(blender):
    """Ask Blender where its user extensions live."""
    out = subprocess.run(
        [blender, "-b", "--python-expr",
         "import bpy;print('PQ_EXT=' + bpy.utils.user_resource('EXTENSIONS'))"],
        capture_output=True, text=True).stdout
    m = re.search(r"PQ_EXT=(.+)", out)
    return m.group(1).strip() if m else None


def installed_version(ext_dir):
    if not ext_dir:
        return None
    path = os.path.join(ext_dir, REPO, ID, "blender_manifest.toml")
    if not os.path.isfile(path):
        return None
    with open(path, encoding="utf-8") as f:
        return manifest_version(f.read())


def run(cmd, dry, check=False):
    print("  $", " ".join('"%s"' % c if " " in c else c for c in cmd))
    if dry:
        return 0
    return subprocess.run(cmd, check=check).returncode


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--blender")
    ap.add_argument("--zip")
    ap.add_argument("--force", action="store_true", help="install even if the version is the same or older")
    ap.add_argument("--dry-run", action="store_true", help="only print the Blender commands")
    args = ap.parse_args()

    blender = find_blender(args.blender)
    if not blender or not os.path.exists(blender) and not shutil.which(blender):
        sys.exit("[ERROR] blender not found. Use --blender PATH.")
    zip_path = find_zip(args.zip)
    if not zip_path or not os.path.isfile(zip_path):
        sys.exit("[ERROR] no %s-*.zip found next to this script. Use --zip PATH." % ID)

    new = zip_version(zip_path)
    ext_dir = extensions_dir(blender)
    old = installed_version(ext_dir)
    print("Blender:   ", blender)
    print("ZIP:       ", zip_path, "(version %s)" % new)
    print("Installed: ", old or "none found in %s" % os.path.join(ext_dir or "?", REPO, ID))

    if old and not args.force:
        if vkey(old) == vkey(new):
            print("Already up to date. Use --force to reinstall.")
            return 0
        if vkey(old) > vkey(new):
            print("The installed version is NEWER than the ZIP. Use --force to downgrade.")
            return 0

    print("\n[1/3] Removing previous versions (errors for 'not installed' are fine)...")
    for ext_id in (ID,) + OLD_IDS:
        run([blender, "--command", "extension", "remove", ext_id], args.dry_run)

    print("\n[2/3] Installing and enabling...")
    if run([blender, "--command", "extension", "install-file", "-r", REPO, "-e", zip_path], args.dry_run):
        sys.exit("[ERROR] Installation failed.")

    print("\n[3/3] Result:")
    if args.dry_run:
        return 0
    now = installed_version(ext_dir)
    if now == new:
        print("Installed version is now %s. Restart Blender if it was open." % now)
        return 0
    print("[WARNING] Installed version reads %r but the ZIP is %s." % (now, new))
    return 1


if __name__ == "__main__":
    sys.exit(main())
