import bpy
import json
import os
import re
import sys

## WHAT IS THIS SCRIPT FOR?
# The PSX characters pack (CC0-packs/psx-lowpoly/Models/Characters_PSX.blend) has about 20 characters that are
# already rigged with the Mesh2Motion human skeleton. This splits every rigged character into its own
# rig-variations/human-psx-<name>.blend (armature + its mesh, moved to the origin), so the export-to-app
# script picks them up as human variations.
# Characters without an armature are skipped. Textures stay as files in CC0-packs/psx-lowpoly/Textures;
# the saved files point at them with relative paths.
# Run it with:
#   blender -b --python "python-scripts/export-psx-variations.py" [-- --overwrite] [-- --dry-run]
# Existing variation files are skipped unless --overwrite is passed. The source .blend is never saved.

argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
overwrite = "--overwrite" in argv
dry_run = "--dry-run" in argv

repo_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
source_file = os.path.join(repo_dir, "CC0-packs", "psx-lowpoly", "Models", "Characters_PSX.blend")
variations_dir = os.path.join(repo_dir, "rig-variations")


def slugify(text):
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def skinned_meshes(scene, armature):
    return [obj for obj in scene.objects if obj.type == 'MESH'
            and any(mod.type == 'ARMATURE' and mod.object == armature for mod in obj.modifiers)]


# List the rigged characters first, then reopen the source file for each one, so every save starts clean
bpy.ops.wm.open_mainfile(filepath=source_file)
characters = []
for armature in sorted((obj for obj in bpy.context.scene.objects if obj.type == 'ARMATURE'), key=lambda o: o.name):
    meshes = skinned_meshes(bpy.context.scene, armature)
    if not meshes:
        print(f"WARNING: {armature.name} has no skinned mesh; skipped")
        continue
    # Name the variation after its mesh: Character_17_Female_Police -> psx-17-female-police
    name = "psx-" + slugify(re.sub(r"^Character_?", "", meshes[0].name) or meshes[0].name)
    characters.append((armature.name, name))

written, skipped = [], []
for armature_name, name in characters:
    filepath = os.path.join(variations_dir, f"human-{name}.blend")
    if os.path.exists(filepath) and not overwrite:
        skipped.append(name)
        continue
    if dry_run:
        written.append(name)
        continue

    bpy.ops.wm.open_mainfile(filepath=source_file)
    scene = bpy.context.scene
    armature = bpy.data.objects[armature_name]
    keep = {armature, *skinned_meshes(scene, armature)}
    for obj in list(bpy.data.objects):
        if obj not in keep:
            bpy.data.objects.remove(obj, do_unlink=True)

    # The pack lays the characters out in a grid; a variation stands at the origin like the rig.
    # The meshes are parented to the armature, so they move with it.
    armature.location = (0.0, 0.0, 0.0)
    armature.name = "Armature"
    armature.data.name = "Armature"
    for obj in keep:
        if obj.type == 'MESH' and obj.parent != armature:
            print(f"WARNING: {obj.name} is not parented to {armature_name}; it keeps its world position")

    # Keep only the characters' material textures. The source file's brushes also use an image that isn't in the repo.
    materials = {slot.material for obj in keep for slot in obj.material_slots if slot.material and slot.material.node_tree}
    used_images = {node.image for mat in materials for node in mat.node_tree.nodes if node.type == 'TEX_IMAGE' and node.image}
    for image in list(bpy.data.images):
        if image.source == 'FILE' and image not in used_images:
            bpy.data.images.remove(image)
    bpy.data.orphans_purge(do_local_ids=True, do_linked_ids=True, do_recursive=True)
    for image in used_images:
        if not image.packed_file and not os.path.exists(bpy.path.abspath(image.filepath)):
            print(f"WARNING: {name} texture not found: {image.filepath}")

    # relative_remap rewrites the texture paths relative to the new file's folder
    bpy.ops.wm.save_as_mainfile(filepath=filepath, copy=True, relative_remap=True, compress=True)
    written.append(name)
    print(f"Saved {armature_name} -> {os.path.relpath(filepath, repo_dir)}")

print("RESULT_JSON" + json.dumps(dict(written=written, skipped=skipped, dry_run=dry_run)))
