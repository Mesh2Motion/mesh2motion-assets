import bpy

# simple script to bulk rename actions
# useful when normalizing action names for viewing
for action in bpy.data.actions:
    action.name = action.name.replace("[RM]", "RM")