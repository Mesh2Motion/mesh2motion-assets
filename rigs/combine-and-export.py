import bpy
import os

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


# Define the directory containing the Blender RIG files
base_dir  = r"C:\git\ModelMotionizer\static\blender\rigs"

# Define the rig type/folder
rig_type = "fox"


# DO NOT NEED TO CHANGE BELOW THIS LINE
#----------------------------------------------------

# Define the output file path. It will be saved in the same directory as the input files
blend_dir = os.path.join(base_dir, rig_type)
output_file = os.path.join(blend_dir, f"{rig_type}-animations.glb")


# Iterate over each Blender file
for file in os.listdir(blend_dir):
    if file.endswith(".blend") and "animation" in file.lower():
        # Append actions from the file
        filepath = os.path.join(blend_dir, file)
        with bpy.data.libraries.load(filepath) as (data_from, data_to):
            data_to.actions = data_from.actions

# Export the combined actions to a GLB file
# select just the armature and mesh objects we want to export
export_objects = [obj for obj in bpy.context.scene.objects if obj.type in ['ARMATURE', 'MESH']]
for obj in export_objects:
    obj.select_set(True)


bpy.ops.export_scene.gltf(
    filepath=output_file,
    export_format="GLB",
    export_animations=True,
    export_nla_strips=False,  # Export actions instead of NLA strips
    use_selection=True,
    export_yup=True, # Blender uses Z-up, three.js uses Y-up
    export_def_bones=True, # only export deformation bones
    use_visible=True, # hides bone shape collection objects
    export_frame_range=True, # limit to playback range set in timeline (helps with looping)
)