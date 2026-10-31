import bpy
import json
import os
import re
import sys

## WHAT IS THIS SCRIPT FOR?
# Exports the rig variations (the rig fitted to other meshes, rig-variations/<rig>-<name>.blend) for the app:
# one GLB each with the mesh + skeleton and no animations, plus a variations.json listing them per rig.
# export.mjs runs this once for every rig it exports.
# Run it by hand with:
#   blender -b --python "python-scripts/export-to-app/blender_export_variations.py" -- --out <staging dir> [--rigs human fox]
# The variation .blend files are never saved; everything happens in memory.

argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
out_root = argv[argv.index("--out") + 1] if "--out" in argv else None
if not out_root:
    raise SystemExit("Pass the staging folder with: -- --out <dir>")
rigs = None
if "--rigs" in argv:
    rigs = [a for a in argv[argv.index("--rigs") + 1:] if not a.startswith("--")]

repo_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
variations_dir = os.path.join(repo_dir, "rig-variations")

# A variation's file name starts with the rig it uses. The app calls the shark rig "fish".
RIG_ALIASES = {"fish": "shark"}

# Same settings as the rig model in blender_export_rig.py
GLTF_SETTINGS = dict(
    export_format="GLB",
    use_selection=True,
    use_visible=True,  # hides bone shape objects
    export_apply=True,  # apply modifiers to mesh (mirror, shrinkwrap, ...) before export
    export_yup=True,  # Blender uses Z-up, three.js uses Y-up
    export_def_bones=True,  # only export deformation bones
    export_animations=False,
)


def slugify(text):
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


variations = {}
for file in sorted(os.listdir(variations_dir)):
    if not file.endswith(".blend"):
        continue
    prefix, _, variant = file[:-len(".blend")].partition("-")
    rig_type = RIG_ALIASES.get(prefix, prefix)
    if rigs is not None and rig_type not in rigs:
        continue
    if not variant:
        print(f"WARNING: {file} has no variation name after the rig name; skipped")
        continue

    filepath = os.path.join(variations_dir, file)
    bpy.ops.wm.open_mainfile(filepath=filepath)
    scene = bpy.context.scene
    armatures = [obj for obj in scene.objects if obj.type == 'ARMATURE']
    if len(armatures) != 1:
        print(f"WARNING: {file} has {len(armatures)} armatures in the scene; skipped")
        continue
    meshes = [obj for obj in scene.objects if obj.type == 'MESH' and obj.visible_get()]
    for obj in scene.objects:
        obj.select_set(obj in armatures or obj in meshes)

    # The exporter silently leaves out a texture it can't find
    for image in bpy.data.images:
        if image.users and image.source == 'FILE' and not image.packed_file \
                and not os.path.exists(bpy.path.abspath(image.filepath)):
            print(f"WARNING: {file} texture not found: {image.filepath}")

    slug = slugify(variant)
    out_dir = os.path.join(out_root, rig_type, "variations")
    os.makedirs(out_dir, exist_ok=True)
    bpy.ops.export_scene.gltf(filepath=os.path.join(out_dir, f"{slug}.glb"), **GLTF_SETTINGS)

    variations.setdefault(rig_type, []).append(dict(
        name=slug,
        file=f"variations/{slug}.glb",
        source=os.path.relpath(filepath, repo_dir).replace(os.sep, "/"),
        meshes=[obj.name for obj in meshes],
        materials=sorted({slot.material.name for obj in meshes for slot in obj.material_slots if slot.material}),
    ))
    print(f"Exported {file} -> {rig_type}/variations/{slug}.glb")

with open(os.path.join(out_root, "variations.json"), "w", encoding="utf-8") as f:
    json.dump(variations, f, indent=2)
print("RESULT_JSON" + json.dumps({rig: len(v) for rig, v in variations.items()}))
