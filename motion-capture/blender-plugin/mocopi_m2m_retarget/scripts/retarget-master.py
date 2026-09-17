import os

import bpy

# run this after importing the BVH mocopi data
# this will expand the arms a bit to be in line with Mesh2Motion
# it then creates new IK bones for the elbow and knee poles
# so we can retarget to a Mesh2Motion IK rig
#
# The addon executes this file with __file__ set, so sibling scripts are
# found next to it. Running it straight out of the text editor instead
# falls back to a "scripts" folder beside the current .blend, which is how
# the original manual workflow behaved.

try:
    script_dir = os.path.dirname(os.path.abspath(__file__))
except NameError:
    script_dir = os.path.join(bpy.path.abspath("//"), "scripts")

scripts = [
    "expand-arms.py",
    "add-ik-bones.py",
]

for script in scripts:
    path = os.path.join(script_dir, script)

    print(f"Running: {path}")

    with open(path, "r", encoding="utf-8") as f:
        exec(compile(f.read(), path, "exec"))
