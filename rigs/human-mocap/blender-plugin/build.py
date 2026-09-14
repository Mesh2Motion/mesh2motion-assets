"""Assemble and zip the Mocopi -> Mesh2Motion Blender extension.

Run from anywhere:

    python build.py

It copies the live scripts and the reference rig .blend out of the
human-mocap folder into the addon package, then writes
dist/mocopi_m2m_retarget-<version>.zip ready to drag into Blender 4.2+.

Keeping the copy step here means scripts/ in the human-mocap folder stays
the single source of truth - edit there, rebuild, reinstall.
"""

import os
import re
import shutil
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
SOURCE_ROOT = os.path.dirname(HERE)          # ...\rigs\human-mocap
ADDON_DIR = os.path.join(HERE, "mocopi_m2m_retarget")
DIST_DIR = os.path.join(HERE, "dist")

# retarget-master.py lives in the addon and is maintained there, because the
# bundled copy resolves its sibling scripts from __file__. The two scripts it
# calls are copied straight from the human-mocap scripts folder.
SCRIPTS_TO_COPY = [
    "expand-arms.py",
    "add-ik-bones.py",
]

ASSETS_TO_COPY = [
    "human-mocopi-rig-setup.blend",
    "mocopi-to-m2m-rokoko-mapping.json",
]

EXCLUDE_DIRS = {"__pycache__", "dist", ".git"}
EXCLUDE_SUFFIXES = (".blend1", ".pyc")


def read_version():
    manifest = os.path.join(ADDON_DIR, "blender_manifest.toml")
    with open(manifest, "r", encoding="utf-8") as handle:
        match = re.search(r'^version\s*=\s*"([^"]+)"', handle.read(), re.M)
    return match.group(1) if match else "0.0.0"


def sync_sources():
    scripts_dst = os.path.join(ADDON_DIR, "scripts")
    assets_dst = os.path.join(ADDON_DIR, "assets")
    os.makedirs(scripts_dst, exist_ok=True)
    os.makedirs(assets_dst, exist_ok=True)

    for name in SCRIPTS_TO_COPY:
        src = os.path.join(SOURCE_ROOT, "scripts", name)
        if not os.path.isfile(src):
            raise SystemExit("Missing source script: {}".format(src))
        shutil.copy2(src, os.path.join(scripts_dst, name))
        print("scripts/{}".format(name))

    for name in ASSETS_TO_COPY:
        src = os.path.join(SOURCE_ROOT, name)
        if not os.path.isfile(src):
            raise SystemExit("Missing asset: {}".format(src))
        shutil.copy2(src, os.path.join(assets_dst, name))
        print("assets/{}".format(name))


def build_zip(version):
    os.makedirs(DIST_DIR, exist_ok=True)
    out = os.path.join(DIST_DIR, "mocopi_m2m_retarget-{}.zip".format(version))

    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as archive:
        for root, dirs, files in os.walk(ADDON_DIR):
            dirs[:] = [d for d in dirs if d not in EXCLUDE_DIRS]
            for name in files:
                if name.endswith(EXCLUDE_SUFFIXES):
                    continue
                path = os.path.join(root, name)
                arcname = os.path.join(
                    "mocopi_m2m_retarget",
                    os.path.relpath(path, ADDON_DIR),
                )
                archive.write(path, arcname)

    return out


if __name__ == "__main__":
    print("Syncing sources into the addon package...")
    sync_sources()

    version = read_version()
    out = build_zip(version)

    size = os.path.getsize(out) / 1024.0 / 1024.0
    print("\nBuilt {} ({:.1f} MB)".format(out, size))
    print("Drag it into Blender, or Edit > Preferences > Add-ons > Install from Disk.")
