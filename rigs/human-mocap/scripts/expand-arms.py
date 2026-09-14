import bpy

# grab the arms and slightly extend. This helps them line up better
# for the mesh2motion rig
armature = bpy.context.active_object

bpy.ops.object.mode_set(mode='EDIT')

for lower_arm_name, hand_name in [
    ("l_low_arm", "l_hand"),
    ("r_low_arm", "r_hand")
]:
    lower_arm = armature.data.edit_bones.get(lower_arm_name)
    hand = armature.data.edit_bones.get(hand_name)

    if lower_arm and hand:
        direction = (lower_arm.tail - lower_arm.head).normalized()
        lower_arm.tail = lower_arm.head + direction * (lower_arm.length * 1.15)

        hand.use_connect = True