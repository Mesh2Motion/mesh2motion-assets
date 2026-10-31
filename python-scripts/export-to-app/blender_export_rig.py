import bpy
import json
import os
import re
import sys

## WHAT IS THIS SCRIPT FOR?
# Exports one rig for the app: the rig model (mesh + skeleton, no animations), the skeleton alone, and one GLB per animation
# action (skeleton + that one animation, no mesh), plus a rig.json describing each animation.
# export.mjs runs this once per rig, then compresses the GLBs and builds the manifest.
# Run it by hand with:
#   blender -b "rigs/rig-human.blend" --python "python-scripts/export-to-app/blender_export_rig.py" -- --out <staging dir>
# The rig .blend is never saved; everything happens in memory.

argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
out_root = argv[argv.index("--out") + 1] if "--out" in argv else None
if not out_root:
    raise SystemExit("Pass the staging folder with: -- --out <dir>")

current_file_path = bpy.data.filepath  # e.g. "C:\git\mesh2motion-assets\rigs\rig-human.blend"
rigs_dir = os.path.dirname(current_file_path)
repo_dir = os.path.dirname(rigs_dir)
rig_type = os.path.basename(current_file_path).replace("rig-", "").replace(".blend", "")
rig_dir = os.path.join(rigs_dir, rig_type)

out_dir = os.path.join(out_root, rig_type)
anim_out_dir = os.path.join(out_dir, "animations")
os.makedirs(anim_out_dir, exist_ok=True)

# Settings shared by every export. They match combine-and-export.py.
GLTF_SETTINGS = dict(
    export_format="GLB",
    use_selection=True,
    use_visible=True,  # hides bone shape collection objects
    export_apply=True,  # apply modifiers to mesh (like subdivision) before export
    export_yup=True,  # Blender uses Z-up, three.js uses Y-up
    export_def_bones=True,  # only export deformation bones
)


def slugify(text):
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def all_fcurves(action):
    return [fc for layer in action.layers for strip in layer.strips for cb in strip.channelbags for fc in cb.fcurves]


def pin_unkeyed_properties(action, defaults):
    # During export, custom properties (IK/FK switches, the dragon's wing_fold_l/r, ...) keep the value
    # the previous action left them at. Give every action a flat key at the rig default for each one it doesn't animate.
    for layer in action.layers:
        for strip in layer.strips:
            for channelbag in strip.channelbags:
                keyed = {fc.data_path for fc in channelbag.fcurves}
                for path, value in defaults.items():
                    if path not in keyed:
                        channelbag.fcurves.new(path).keyframe_points.insert(action.frame_range[0], value)


def trim_repeated_loop_frame(action):
    # Many looping files key the same pose on their first and last frame. Played as a loop that frame shows twice
    # (a hitch), so export the action without that last frame when it is cyclic, or starts at frame 0.
    start, end = action.frame_range
    if not action.use_cyclic and start != 0:
        return False
    curves = all_fcurves(action)
    if curves and all(abs(fc.evaluate(start) - fc.evaluate(end)) < 1e-4 for fc in curves):
        action.use_frame_range = True
        action.frame_range = (start, end - 1)
        return True
    return False


def action_tags(action):
    # Extra tags can be added per animation as an action custom property "m2m_tags": "tag one, tag two"
    value = action.get("m2m_tags")
    if value is None:
        return []
    items = value.split(",") if isinstance(value, str) else list(value)
    return [slugify(str(t)) for t in items if str(t).strip()]


def select_only(objects):
    for obj in bpy.context.scene.objects:
        obj.select_set(obj in objects)


armature = next((obj for obj in bpy.data.objects if obj.type == 'ARMATURE'), None)
if armature is None:
    raise SystemExit("No armature found in the rig file.")
meshes = [obj for obj in bpy.context.scene.objects if obj.type == 'MESH']
scene = bpy.context.scene
fps = scene.render.fps / scene.render.fps_base

property_defaults = {f'["{k}"]': float(v) for k, v in armature.items() if isinstance(v, (int, float))}
for pose_bone in armature.pose.bones:
    property_defaults.update({f'pose.bones["{pose_bone.name}"]["{k}"]': float(v)
                              for k, v in pose_bone.items() if isinstance(v, (int, float))})

# Rig model: mesh + skeleton, no animations
anim_data = armature.animation_data_create()
anim_data.action = None
select_only([armature] + meshes)
bpy.ops.export_scene.gltf(filepath=os.path.join(out_dir, "model.glb"), export_animations=False, **GLTF_SETTINGS)

# Skeleton: deform bones only, no mesh, no animations. The app fits this to a custom mesh someone imports.
select_only([armature])
bpy.ops.export_scene.gltf(filepath=os.path.join(out_dir, "skeleton.glb"), export_animations=False, **GLTF_SETTINGS)

# Same filter as combine-and-export.py: any .blend in the rig folder or its sub-folders with "animation" in its name
animation_files = []
for folder, _, files in os.walk(rig_dir):
    for file in files:
        if file.endswith(".blend") and "animation" in file.lower():
            animation_files.append(os.path.join(folder, file))
animation_files.sort()

animations = []
used_slugs = set()
for filepath in animation_files:
    with bpy.data.libraries.load(filepath) as (data_from, data_to):
        data_to.actions = data_from.actions
    actions = [a for a in data_to.actions if a is not None
               and "EmptyAction" not in a.name and not a.name.startswith("RIG_")]

    file_name = os.path.basename(filepath)
    rel_folder = os.path.relpath(os.path.dirname(filepath), rig_dir)
    folder_tags = [] if rel_folder == "." else [slugify(p) for p in rel_folder.split(os.sep)]
    base_slug = re.sub(rf"^(wip-)?animation-{rig_type}-", "", slugify(file_name[:-len(".blend")]))
    is_wip = file_name.upper().startswith("WIP-")
    if not actions:
        print(f"WARNING: no exportable action in {file_name}")
    if len(actions) > 1:
        print(f"WARNING: {file_name} has {len(actions)} actions; exporting each one")

    for action in actions:
        # The action name becomes the animation name in the app. Appending gives a clashing name a ".001" suffix.
        name = re.sub(r"\.\d{3}$", "", action.name)
        slug = base_slug if len(actions) == 1 else f"{base_slug}-{slugify(name)}"
        while slug in used_slugs:
            slug += "-2"
        used_slugs.add(slug)

        pin_unkeyed_properties(action, property_defaults)
        trimmed = trim_repeated_loop_frame(action)
        start, end = (int(round(f)) for f in action.frame_range)

        for track in list(anim_data.nla_tracks):
            anim_data.nla_tracks.remove(track)
        track = anim_data.nla_tracks.new()
        track.name = name
        strip = track.strips.new(action.name, start, action)
        # Some actions don't get a slot bound automatically, and the exporter then skips the strip ("no action slot assigned")
        if strip.action_slot is None and action.slots:
            strip.action_slot = action.slots[0]
        # export_frame_range clips the action to the scene range
        scene.frame_start, scene.frame_end = start, end

        select_only([armature])
        bpy.ops.export_scene.gltf(
            filepath=os.path.join(anim_out_dir, f"{slug}.glb"),
            export_animations=True,
            export_animation_mode='ACTIONS',
            export_nla_strips=False,
            export_anim_single_armature=False,  # only the NLA-track action; otherwise RIG_ actions get exported too
            export_frame_range=True,
            export_anim_slide_to_zero=True,  # actions that still start at frame 1 would otherwise get an empty first frame
            **GLTF_SETTINGS,
        )
        root_motion = bool(re.search(r"[_-]rm$", base_slug) or re.search(r"[_ ]RM$", name))
        animations.append(dict(
            name=name,
            slug=slug,
            file=f"animations/{slug}.glb",
            source=os.path.relpath(filepath, repo_dir).replace(os.sep, "/"),
            tags=folder_tags + [t for t in action_tags(action) if t not in folder_tags],
            loop=bool(action.use_cyclic),
            root_motion=root_motion,
            wip=is_wip,
            frame_start=start,
            frame_end=end,
            frame_count=end - start + 1,
            duration=round((end - start) / fps, 4),  # matches the glTF clip: first key to last key
            looped_frame_trimmed=trimmed,
        ))
        print(f"Exported {name} -> {slug}.glb ({start}..{end})")

rig_info = dict(rig=rig_type, fps=fps, model="model.glb", skeleton="skeleton.glb", animations=animations)
with open(os.path.join(out_dir, "rig.json"), "w", encoding="utf-8") as f:
    json.dump(rig_info, f, indent=2)
print("RESULT_JSON" + json.dumps(dict(rig=rig_type, animations=len(animations))))
