"""Export each object in the 'guns' collection to its own GLB, centered at the origin.

Run headless:  blender --background Props-MASTER.blend --python export_guns.py
Or open the .blend in Blender and run this from the Text Editor.
"""
import os

import bpy

COLLECTION_NAME = "guns"
EXPORT_FOLDER_NAME = "export"
ORIGIN = (0.0, 0.0, 0.0)
GLB_FORMAT = "GLB"
FILE_EXTENSION = ".glb"


def get_export_dir():
    blend_dir = os.path.dirname(bpy.data.filepath)  # export folder sits next to the .blend
    export_dir = os.path.join(blend_dir, EXPORT_FOLDER_NAME)
    os.makedirs(export_dir, exist_ok=True)
    return export_dir


def select_only(obj):
    bpy.ops.object.select_all(action="DESELECT")
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj


def export_object(obj, export_dir):
    original_location = obj.location.copy()
    obj.location = ORIGIN
    select_only(obj)

    out_path = os.path.join(export_dir, obj.name + FILE_EXTENSION)
    bpy.ops.export_scene.gltf(
        filepath=out_path,
        export_format=GLB_FORMAT,
        use_selection=True,
        export_apply=True,  # bake modifiers into the mesh
    )

    obj.location = original_location  # put it back so the scene is unchanged
    print(f"Exported {obj.name} -> {out_path}")


def main():
    if bpy.context.object and bpy.context.object.mode != "OBJECT":
        bpy.ops.object.mode_set(mode="OBJECT")

    collection = bpy.data.collections.get(COLLECTION_NAME)
    if collection is None:
        raise RuntimeError(f"Collection '{COLLECTION_NAME}' not found")

    export_dir = get_export_dir()
    for obj in collection.objects:
        export_object(obj, export_dir)

    print(f"Done: {len(collection.objects)} objects exported to {export_dir}")


main()
