"""Assemble and zip the Mocopi -> Mesh2Motion Blender extension.

Run from anywhere:

    python build.py

It copies the live prep scripts out of the human-mocap folder into the addon
package, then writes dist/mocopi_m2m_retarget-<version>.zip ready to drag into
Blender 4.2+.

Ownership, which matters because getting it wrong silently eats work:

  ../scripts/*.py     -> human-mocap owns them. Edit there, rebuild.
  assets/*            -> the ADDON owns all of it: the .blend, the bone map,
                         any textures. Nothing here is ever copied over. Open
                         assets/human-mocopi-rig-setup.blend in Blender and
                         save it in place; texture paths then resolve relative
                         to where the .blend actually ships. Anything sitting
                         in assets/ is zipped as-is.

Every copy this script does checks whether it is about to overwrite something
newer than its source, and refuses if so.
"""

import os
import re
import shutil
import sys
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

# Copied in from human-mocap on every build. Nothing, currently -- the .blend
# and the mapping JSONs all live in the addon now.
ASSETS_TO_COPY = []

# Owned by the addon, never overwritten, but the build fails without them.
ASSETS_REQUIRED = [
    "human-mocopi-rig-setup.blend",
    "mocopi-to-m2m-bone-map.json",
]

EXCLUDE_DIRS = {"__pycache__", "dist", ".git"}
EXCLUDE_SUFFIXES = (".blend1", ".pyc")


def read_version():
    manifest = os.path.join(ADDON_DIR, "blender_manifest.toml")
    with open(manifest, "r", encoding="utf-8") as handle:
        match = re.search(r'^version\s*=\s*"([^"]+)"', handle.read(), re.M)
    return match.group(1) if match else "0.0.0"


class BuildError(Exception):
    """A problem the user has to resolve before the build can run."""


def copy_if_safe(src, dst, label):
    """Copy src over dst, unless dst is newer than src.

    A .blend saved from Blender into the addon used to be overwritten by an
    older copy on the next build, with no warning and no way back. Anything
    that would destroy newer work stops the build instead.
    """
    if not os.path.isfile(src):
        raise BuildError("Missing source file: {}".format(src))

    if os.path.isfile(dst):
        src_mtime = os.path.getmtime(src)
        dst_mtime = os.path.getmtime(dst)

        if dst_mtime > src_mtime + 1:  # 1s slack for filesystem timestamp noise
            raise BuildError(
                "\nRefusing to overwrite newer file:\n"
                "    {}\n"
                "  is newer than its source\n"
                "    {}\n\n"
                "Something edited the addon's copy directly. Copy your changes "
                "back to the source, or delete the newer file if you don't want "
                "them, then build again.".format(dst, src)
            )

    shutil.copy2(src, dst)
    print(label)


def sync_sources():
    scripts_dst = os.path.join(ADDON_DIR, "scripts")
    assets_dst = os.path.join(ADDON_DIR, "assets")
    os.makedirs(scripts_dst, exist_ok=True)
    os.makedirs(assets_dst, exist_ok=True)

    for name in SCRIPTS_TO_COPY:
        copy_if_safe(
            os.path.join(SOURCE_ROOT, "scripts", name),
            os.path.join(scripts_dst, name),
            "  copied  scripts/{}".format(name),
        )

    for name in ASSETS_TO_COPY:
        copy_if_safe(
            os.path.join(SOURCE_ROOT, name),
            os.path.join(assets_dst, name),
            "  copied  assets/{}".format(name),
        )

    for name in ASSETS_REQUIRED:
        path = os.path.join(assets_dst, name)
        if not os.path.isfile(path):
            raise BuildError(
                "Missing addon-owned asset: {}\n"
                "This file is not copied from anywhere - it lives in the addon. "
                "Restore it before building.".format(path)
            )
        print("  kept    assets/{}".format(name))


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


def main():
    print("Syncing sources into the addon package...")
    sync_sources()

    extras = sorted(
        name
        for name in os.listdir(os.path.join(ADDON_DIR, "assets"))
        if name not in ASSETS_TO_COPY
        and name not in ASSETS_REQUIRED
        and not name.endswith(EXCLUDE_SUFFIXES)
    )
    for name in extras:
        print("  bundled assets/{}".format(name))

    version = read_version()
    out = build_zip(version)

    size = os.path.getsize(out) / 1024.0 / 1024.0
    print("\nBuilt {} ({:.1f} MB)".format(out, size))
    print("Drag it into Blender, or Edit > Preferences > Add-ons > Install from Disk.")


if __name__ == "__main__":
    try:
        main()
    except BuildError as exc:
        print("\nBUILD FAILED\n{}".format(exc))
        sys.exit(1)
