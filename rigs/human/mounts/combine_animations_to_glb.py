"""
Blender 5.1+. Combine every animation in this script's folder onto one rig and export a single GLB.
Only objects in visible collections are exported, with modifiers applied.

Usage:
  Command line:  blender -b -P combine_animations_to_glb.py
  In Blender:    open this file in the Text Editor and press Run Script
"""
import os
import bpy

# ---- Settings ----
OUTPUT_FILE_NAME = "human_mounted_animations.glb"
BASE_FILE_NAME = None  # e.g. "character.blend" to pick the rig/mesh source; None = first file alphabetically
WORK_SCENE_NAME = "CombinedAnimationExport"

# ---- Constants ----
EXT_FBX = ".fbx"
EXT_GLB = ".glb"
EXT_GLTF = ".gltf"
EXT_BVH = ".bvh"
EXT_BLEND = ".blend"
SUPPORTED_EXTENSIONS = {EXT_FBX, EXT_GLB, EXT_GLTF, EXT_BVH, EXT_BLEND}

OBJECT_TYPE_ARMATURE = "ARMATURE"
POSE_BONE_PATH_PREFIX = "pose.bones"
GLTF_FORMAT_GLB = "GLB"
GLTF_ANIMATION_MODE_NLA = "NLA_TRACKS"  # each NLA track becomes one named glTF animation


def get_script_dir():
    script_file = globals().get("__file__", "")
    if script_file and os.path.isfile(script_file):
        return os.path.dirname(os.path.abspath(script_file))

    space = getattr(bpy.context, "space_data", None)
    text = getattr(space, "text", None)
    if text and text.filepath:
        return os.path.dirname(bpy.path.abspath(text.filepath))

    if bpy.data.filepath:
        return os.path.dirname(bpy.data.filepath)

    raise RuntimeError("Can't find the script folder. Save the script to disk before running it.")


def collect_animation_files(folder, files_to_skip):
    found = []
    for name in sorted(os.listdir(folder)):
        path = os.path.abspath(os.path.join(folder, name))
        ext = os.path.splitext(name)[1].lower()

        if ext not in SUPPORTED_EXTENSIONS or path in files_to_skip:
            continue
        found.append(path)
    return found


def order_with_base_first(files):
    if BASE_FILE_NAME is None:
        return files

    base = [f for f in files if os.path.basename(f) == BASE_FILE_NAME]
    if not base:
        raise RuntimeError(f"BASE_FILE_NAME '{BASE_FILE_NAME}' not found in folder.")
    return base + [f for f in files if f != base[0]]


def get_extension(path):
    return os.path.splitext(path)[1].lower()


def pointer_set(collection):
    return {item.as_pointer() for item in collection}


def get_fcurves(action):
    return [fc for layer in action.layers for strip in layer.strips
            for bag in strip.channelbags for fc in bag.fcurves]


def is_bone_action(action):
    return any(fc.data_path.startswith(POSE_BONE_PATH_PREFIX) for fc in get_fcurves(action))


def find_armature(objects):
    armatures = [o for o in objects if o.type == OBJECT_TYPE_ARMATURE]
    if not armatures:
        return None

    visible = [o for o in armatures if o.visible_get()]
    candidates = visible or armatures
    return max(candidates, key=lambda o: len(o.data.bones))  # biggest rig if there are several


def keep_actions(actions):
    for action in actions:
        action.use_fake_user = True  # survives deleting the imported rig


# ---- Base file (rig + meshes) ----

def load_base_blend(path):
    """Bring in the base .blend's scene so its collection visibility is kept."""
    if bpy.app.background:
        bpy.ops.wm.open_mainfile(filepath=path)
        scene = bpy.context.scene
        actions = list(bpy.data.actions)
        return scene, actions

    actions_before = pointer_set(bpy.data.actions)
    with bpy.data.libraries.load(path, link=False) as (data_from, data_to):
        data_to.scenes = [data_from.scenes[0]]
        data_to.actions = list(data_from.actions)

    scene = data_to.scenes[0]
    bpy.context.window.scene = scene  # leaves your open scene untouched
    actions = [a for a in bpy.data.actions if a.as_pointer() not in actions_before]
    return scene, actions


def load_base_import(path):
    if bpy.app.background:
        bpy.ops.wm.read_factory_settings(use_empty=True)
        scene = bpy.context.scene
    else:
        scene = bpy.data.scenes.new(WORK_SCENE_NAME)  # leaves your open scene untouched
        bpy.context.window.scene = scene

    actions_before = pointer_set(bpy.data.actions)
    import_file(path)
    actions = [a for a in bpy.data.actions if a.as_pointer() not in actions_before]
    return scene, actions


def load_base(path):
    if get_extension(path) == EXT_BLEND:
        scene, actions = load_base_blend(path)
    else:
        scene, actions = load_base_import(path)

    armature = find_armature(scene.objects)
    if armature is None:
        raise RuntimeError(f"No armature found in base file: {os.path.basename(path)}")

    bone_actions = [a for a in actions if is_bone_action(a)]
    keep_actions(bone_actions)
    return armature, bone_actions


# ---- Animation files (actions only) ----

def import_file(path):
    ext = get_extension(path)

    if ext == EXT_FBX:
        bpy.ops.import_scene.fbx(filepath=path)
    elif ext in (EXT_GLB, EXT_GLTF):
        bpy.ops.import_scene.gltf(filepath=path)
    elif ext == EXT_BVH:
        bpy.ops.import_anim.bvh(filepath=path)


def load_actions_from_blend(path):
    with bpy.data.libraries.load(path, link=False) as (data_from, data_to):
        data_to.actions = list(data_from.actions)


def load_animation_file(path):
    objects_before = pointer_set(bpy.data.objects)
    actions_before = pointer_set(bpy.data.actions)

    if get_extension(path) == EXT_BLEND:
        load_actions_from_blend(path)
    else:
        import_file(path)

    new_actions = [a for a in bpy.data.actions
                   if a.as_pointer() not in actions_before and is_bone_action(a)]
    keep_actions(new_actions)

    for obj in [o for o in bpy.data.objects if o.as_pointer() not in objects_before]:
        bpy.data.objects.remove(obj, do_unlink=True)  # drop the duplicate rig/mesh
    return new_actions


# ---- NLA + export ----

def build_nla_tracks(armature, actions):
    anim_data = armature.animation_data or armature.animation_data_create()
    anim_data.action = None  # keep the rig in rest pose; tracks carry all animation

    for track in list(anim_data.nla_tracks):
        anim_data.nla_tracks.remove(track)  # avoid exporting the base file's old tracks twice

    for action in actions:
        track = anim_data.nla_tracks.new()
        track.name = action.name
        start_frame = int(action.frame_range[0])
        strip = track.strips.new(action.name, start_frame, action)
        if strip.action_slot is None and action.slots:
            strip.action_slot = action.slots[0]  # imported slot is named after the old rig, so bind it manually


def export_glb(output_path):
    bpy.ops.export_scene.gltf(
        filepath=output_path,
        export_format="GLB",
        use_active_scene=True,
        use_visible=True,  # skips hidden/excluded collections and hidden objects
        export_apply=True,  # applies modifiers; armature modifier is kept as skinning
        export_animations=True,
        export_animation_mode='ACTIONS',
        export_nla_strips=False,  # Export actions instead of NLA strips
        export_yup=True, # Blender uses Z-up, three.js uses Y-up
        export_def_bones=True, # only export deformation bones
        export_frame_range=True # limit to playback range set in timeline (helps with looping)
    )

def main():
    script_dir = get_script_dir()
    output_path = os.path.join(script_dir, OUTPUT_FILE_NAME)
    files_to_skip = {os.path.abspath(output_path)}
    if bpy.data.filepath:
        files_to_skip.add(os.path.abspath(bpy.data.filepath))  # don't import the open .blend into itself

    files = order_with_base_first(collect_animation_files(script_dir, files_to_skip))
    if not files:
        raise RuntimeError(f"No animation files found in {script_dir}")

    base_armature, all_actions = load_base(files[0])
    print(f"Base rig: '{base_armature.name}' from {os.path.basename(files[0])}")
    if not base_armature.visible_get():
        print(f"WARNING: '{base_armature.name}' is hidden, so the GLB will have no skeleton or animation.")

    for path in files[1:]:
        actions = load_animation_file(path)
        all_actions.extend(actions)
        print(f"  {os.path.basename(path)}: {len(actions)} action(s)")

    build_nla_tracks(base_armature, all_actions)
    export_glb(output_path)
    print(f"Exported {len(all_actions)} animation(s) to {output_path}")


main()