import bpy
import importlib.util
import json
import os
import sys

## WHAT IS THIS SCRIPT FOR?
# Exports the props (weapons, tools, ...) in props/Props-MASTER.blend for the app: one GLB per object,
# moved to the origin, plus a small PNG preview of each, and a props.json describing them.
# export.mjs runs this, then copies the files to export_to_app/props and writes the props manifest.
# Run it by hand with:
#   blender -b "props/Props-MASTER.blend" --python "python-scripts/export-to-app/blender_export_props.py" -- --out <staging dir>
# The props .blend is never saved; everything happens in memory.
#
# Every mesh in a collection under the scene collection is a prop. The collection name is its category.
# Objects or collections disabled for rendering are skipped.
# Optional custom properties on the object (Object properties > Custom Properties):
#   m2m_display_name   the name the app shows, e.g. "Heavy Sniper Rifle" (defaults to the object name)
#   m2m_tags           extra comma-separated tags, e.g. "two handed, ranged"

argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
out_root = argv[argv.index("--out") + 1] if "--out" in argv else None
if not out_root:
    raise SystemExit("Pass the staging folder with: -- --out <dir>")

props_blend_dir = os.path.dirname(bpy.data.filepath)
out_dir = os.path.join(out_root, "props")
preview_out_dir = os.path.join(out_dir, "preview")
os.makedirs(preview_out_dir, exist_ok=True)

PROP_TYPES = {"MESH", "CURVE", "SURFACE", "META", "FONT"}

# Same settings as the rig model in blender_export_rig.py, without the skeleton
GLTF_SETTINGS = dict(
    export_format="GLB",
    use_selection=True,
    export_apply=True,  # apply modifiers (the sickle's geometry nodes, ...) before export
    export_yup=True,  # Blender uses Z-up, three.js uses Y-up
    export_animations=False,
)


def load_thumbnail_module():
    # The previews come from the same script the artists run by hand, so they look the same either way
    path = os.path.join(props_blend_dir, "render_thumbnails.py")
    sys.dont_write_bytecode = True  # no __pycache__ next to the .blend
    spec = importlib.util.spec_from_file_location("render_thumbnails", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def tag_list(text):
    return [t for t in ("-".join(part.strip().lower().split()) for part in text.split(",")) if t]


def find_props():
    props = []
    for collection in bpy.context.scene.collection.children:
        if collection.hide_render:
            continue
        for obj in collection.objects:
            if obj.type in PROP_TYPES and obj.parent is None and not obj.hide_render:
                props.append((collection.name, obj))
    return props


def export_prop(obj, filepath):
    original_location = obj.location.copy()
    obj.location = (0.0, 0.0, 0.0)  # the app mounts a prop by its origin
    bpy.ops.object.select_all(action="DESELECT")
    for o in [obj, *obj.children_recursive]:
        o.select_set(True)
    bpy.context.view_layer.objects.active = obj
    try:
        bpy.ops.export_scene.gltf(filepath=filepath, **GLTF_SETTINGS)
    finally:
        obj.location = original_location  # the preview render is framed on the object wherever it is


def mesh_stats(obj):
    depsgraph = bpy.context.evaluated_depsgraph_get()
    obj_eval = obj.evaluated_get(depsgraph)
    mesh = obj_eval.to_mesh()
    try:
        mesh.calc_loop_triangles()
        return len(mesh.vertices), len(mesh.loop_triangles)
    finally:
        obj_eval.to_mesh_clear()


if bpy.context.object and bpy.context.object.mode != "OBJECT":
    bpy.ops.object.mode_set(mode="OBJECT")

thumbnails = load_thumbnail_module()
props = find_props()
results = []
thumbnail_scene = thumbnails.build_thumbnail_scene()
try:
    for category, obj in props:
        # three.js strips '.' and ':' from node names, and the file name is the object name
        if any(c in obj.name for c in ".:/\\"):
            print(f"WARNING: prop {obj.name} has '.', ':' or a slash in its name; skipped")
            continue
        for slot in obj.material_slots:
            for node in (slot.material.node_tree.nodes if slot.material and slot.material.node_tree else []):
                image = getattr(node, "image", None)
                if image and image.source == 'FILE' and not image.packed_file \
                        and not os.path.exists(bpy.path.abspath(image.filepath)):
                    print(f"WARNING: prop {obj.name} texture not found: {image.filepath}")

        export_prop(obj, os.path.join(out_dir, f"{obj.name}.glb"))
        thumbnails.render_object(thumbnail_scene, obj, preview_out_dir)

        tags = [*tag_list(category), *tag_list(str(obj.get("m2m_tags", "")))]
        vertices, triangles = mesh_stats(obj) if obj.type == 'MESH' else (None, None)
        width, depth, height = obj.dimensions
        results.append(dict(
            name=obj.name,
            display_name=str(obj.get("m2m_display_name", obj.name)),
            file=f"{obj.name}.glb",
            preview=f"preview/{obj.name}.png",
            category=category,
            tags=list(dict.fromkeys(tags)),
            # metres along Blender's X, Y and Z (Z is up in Blender, Y is up in the GLB)
            dimensions=dict(width=round(width, 4), depth=round(depth, 4), height=round(height, 4)),
            vertices=vertices,
            triangles=triangles,
            materials=[slot.material.name for slot in obj.material_slots if slot.material],
        ))
        print(f"Exported prop {obj.name} ({category})")
finally:
    thumbnails.cleanup(thumbnail_scene)

with open(os.path.join(out_dir, "props.json"), "w", encoding="utf-8") as f:
    json.dump(results, f, indent=2)
print("RESULT_JSON" + json.dumps({"props": len(results)}))
