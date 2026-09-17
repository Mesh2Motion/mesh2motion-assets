import os
import bpy

# run this after importing the BVH mocopi data
# this will expand the arms a bit to be in line with Mesh2Motion
# it then creates new IK bones for the elbow and knee poles
# so we can retarget to a Mesh2Motion IK rig

script_dir = bpy.path.abspath("//")

scripts = [
    "scripts\\expand-arms.py",
    "scripts\\add-ik-bones.py",
]

for script in scripts:
    path = os.path.join(script_dir, script)

    print(f"Running: {path}")

    with open(path, "r", encoding="utf-8") as f:
        exec(compile(f.read(), path, "exec"))