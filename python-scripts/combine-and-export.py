import bpy
import os
import sys

## WHAT IS THIS SCRIPT FOR?
# Each BLEND file only has one animation action. This script consolidates all these actions and
# creates one GLB file that the Mesh2Motion tool can load.
# This will make loading faster since it will be one HTTP request
# if there gets to be a ton of animations, we can revisit this to split things apart.

## INSTRUCTIONS ON HOW TO RUN
# 1. Open the rig-bird.blend file in Blender. This process should work for all the rig files
# 2. Open a "Text Editor" view and click New to create a new text area
# 3. Copy and paste this script into the text area
# 4. Update the path below to point to the rig you want to create a GLB for
# 5. Click the "Run Script" (play) button
# 6. You can put the GLB file in the static > animations folder overwriting the old rig one


current_file_path = bpy.data.filepath  # e.g., "C:\git\mesh2motion-assets\rigs\rig-kaiju.blend"
base_dir = os.path.dirname(current_file_path)  # e.g., "C:\git\mesh2motion-assets\rigs"
blend_filename = os.path.basename(current_file_path)  # e.g., "rig-kaiju.blend"
rig_type = blend_filename.replace("rig-", "").replace(".blend", "")  # e.g., "kaiju"



# DO NOT NEED TO CHANGE BELOW THIS LINE
#----------------------------------------------------

# Define the output file path. It will be saved in the same directory as the input files
blend_dir = os.path.join(base_dir, rig_type)
output_file = os.path.join(blend_dir, f"{rig_type}-animations.glb")


# Iterate over each Blender file, including sub-folders (e.g. human/combat/melee)
for folder, _, files in os.walk(blend_dir):
    for file in files:
        if file.endswith(".blend") and "animation" in file.lower():
            # Append actions from the file
            filepath = os.path.join(folder, file)
            with bpy.data.libraries.load(filepath) as (data_from, data_to):
                data_to.actions = data_from.actions


# assign all actions to the armature so they get exported. This is newer in Blender 5.0
# and is kind of hacky the way you have to combine the actions. When you import the 
# action data, they are not assigned to any object and Blender won't export them. 
# So we have to assign them to the armature object and then they will show up in the GLB export.
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
    # (a hitch), so export the action without that last frame when it is cyclic, or starts at frame 0 (many loops
    # aren't flagged cyclic; the old 1..N rig scene range used to hide their repeat by dropping frame 0 instead).
    start, end = action.frame_range
    if not action.use_cyclic and start != 0:
        return
    curves = [fc for layer in action.layers for strip in layer.strips for cb in strip.channelbags for fc in cb.fcurves]
    if curves and all(abs(fc.evaluate(start) - fc.evaluate(end)) < 1e-4 for fc in curves):
        action.use_frame_range = True
        action.frame_range = (start, end - 1)
        print(f"Trimmed repeated loop frame: {action.name} ({start:.0f}..{end - 1:.0f})")


armature = next((obj for obj in bpy.data.objects if obj.type == 'ARMATURE'), None)
if armature:
    # Ensure animation_data exists on the armature object
    anim_data = armature.animation_data_create()

    property_defaults = {f'["{k}"]': float(v) for k, v in armature.items() if isinstance(v, (int, float))}
    for pose_bone in armature.pose.bones:
        property_defaults.update({f'pose.bones["{pose_bone.name}"]["{k}"]': float(v)
                                  for k, v in pose_bone.items() if isinstance(v, (int, float))})

    for action in bpy.data.actions:
        if "EmptyAction" in action.name:
            print(f"Skipping invalid action: {action.name}")
            continue
        # RIG_ actions are part of the rig (e.g. the dragon's wing fold pose read by Action constraints), not animations
        if action.name.startswith("RIG_"):
            print(f"Skipping rig action: {action.name}")
            continue

        pin_unkeyed_properties(action, property_defaults)
        trim_repeated_loop_frame(action)

        track = anim_data.nla_tracks.new()
        track.name = action.name

        start_frame = int(round(action.frame_range[0]))  # Blender 5.0 expects an int for NLA strip start frame
        track.strips.new(action.name, start_frame, action)

    # export_frame_range clips every action to this file's scene range (e.g. 1..80 in rig-dragon.blend), which
    # dropped frame 0 and cut long actions short. Cover all exported actions so each keeps its own frame range.
    exported = [track.strips[0].action for track in anim_data.nla_tracks]
    if exported:
        bpy.context.scene.frame_start = int(min(a.frame_range[0] for a in exported))
        bpy.context.scene.frame_end = int(max(a.frame_range[1] for a in exported))
else:
    print("No armature found in the scene. Cannot assign actions for export.")

# Export the combined actions to a GLB file
# select just the armature and mesh objects we want to export
export_objects = [obj for obj in bpy.context.scene.objects if obj.type in ['ARMATURE', 'MESH']]
for obj in export_objects:
    obj.select_set(True)


bpy.ops.export_scene.gltf(
    filepath=output_file,
    export_format="GLB",
    export_animations=True,
    export_animation_mode='ACTIONS',
    export_nla_strips=False,  # Export actions instead of NLA strips
    export_anim_single_armature=False,  # only the NLA-track actions above; otherwise RIG_ actions get exported too
    use_selection=True,
    export_apply=True, # apply modifiers to mesh (like subdivision) before export
    export_yup=True, # Blender uses Z-up, three.js uses Y-up
    export_def_bones=True, # only export deformation bones
    use_visible=True, # hides bone shape collection objects
    export_frame_range=True, # limit to playback range set in timeline (helps with looping)
)