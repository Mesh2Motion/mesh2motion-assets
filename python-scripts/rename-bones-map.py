import bpy

# Get the active armature
obj = bpy.context.object

if obj.type != 'ARMATURE':
    raise Exception("Select an armature object")

armature = obj.data

# Your bone mapping (source -> target)
bone_map = {
    "TODO_root": "root",
    "TODO_pelvis": "pelvis",
    "TODO_spine_01": "spine_01",
    "TODO_spine_02": "spine_02",
    "TODO_spine_03": "spine_03",
    "TODO_neck_01": "neck_01",
    "TODO_head": "head",
    "TODO_head_leaf": "head_leaf",
    "TODO_clavicle_l": "clavicle_l",
    "TODO_upperarm_l": "upperarm_l",
    "TODO_lowerarm_l": "lowerarm_l",
    "TODO_clavicle_r": "clavicle_r",
    "TODO_upperarm_r": "upperarm_r",
    "TODO_lowerarm_r": "lowerarm_r",
    "TODO_thigh_l": "thigh_l",
    "TODO_calf_l": "calf_l",
    "TODO_foot_l": "foot_l",
    "TODO_ball_l": "ball_l",
    "TODO_ball_leaf_l": "ball_leaf_l",
    "TODO_thigh_r": "thigh_r",
    "TODO_calf_r": "calf_r",
    "TODO_foot_r": "foot_r",
    "TODO_ball_r": "ball_r",
    "TODO_ball_leaf_r": "ball_leaf_r",

    # left hand and fingers
    "TODO_hand_l": "hand_l",
    "TODO_index_01_l": "index_01_l",
    "TODO_index_02_l": "index_02_l",
    "TODO_index_03_l": "index_03_l",
    "TODO_index_04_leaf_l": "index_04_leaf_l",
    "TODO_middle_01_l": "middle_01_l",
    "TODO_middle_02_l": "middle_02_l",
    "TODO_middle_03_l": "middle_03_l",
    "TODO_middle_04_leaf_l": "middle_04_leaf_l",
    "TODO_pinky_01_l": "pinky_01_l",
    "TODO_pinky_02_l": "pinky_02_l",
    "TODO_pinky_03_l": "pinky_03_l",
    "TODO_pinky_04_leaf_l": "pinky_04_leaf_l",
    "TODO_ring_01_l": "ring_01_l",
    "TODO_ring_02_l": "ring_02_l",
    "TODO_ring_03_l": "ring_03_l",
    "TODO_ring_04_leaf_l": "ring_04_leaf_l",
    "TODO_thumb_01_l": "thumb_01_l",
    "TODO_thumb_02_l": "thumb_02_l",
    "TODO_thumb_03_l": "thumb_03_l",
    "TODO_thumb_04_leaf_l": "thumb_04_leaf_l",

    # right hand and fingers
    "TODO_hand_r": "hand_r",
    "TODO_index_01_r": "index_01_r",
    "TODO_index_02_r": "index_02_r",
    "TODO_index_03_r": "index_03_r",
    "TODO_index_04_leaf_r": "index_04_leaf_r",
    "TODO_middle_01_r": "middle_01_r",
    "TODO_middle_02_r": "middle_02_r",
    "TODO_middle_03_r": "middle_03_r",
    "TODO_middle_04_leaf_r": "middle_04_leaf_r",
    "TODO_pinky_01_r": "pinky_01_r",
    "TODO_pinky_02_r": "pinky_02_r",
    "TODO_pinky_03_r": "pinky_03_r",
    "TODO_pinky_04_leaf_r": "pinky_04_leaf_r",
    "TODO_ring_01_r": "ring_01_r",
    "TODO_ring_02_r": "ring_02_r",
    "TODO_ring_03_r": "ring_03_r",
    "TODO_ring_04_leaf_r": "ring_04_leaf_r",
    "TODO_thumb_01_r": "thumb_01_r",
    "TODO_thumb_02_r": "thumb_02_r",
    "TODO_thumb_03_r": "thumb_03_r",
    "TODO_thumb_04_leaf_r": "thumb_04_leaf_r"
}

# --- STEP 1: rename to temporary names ---
temp_map = {}

for bone in armature.bones:
    if bone.name in bone_map:
        temp_name = "TEMP_" + bone.name
        temp_map[temp_name] = bone_map[bone.name]
        bone.name = temp_name

# --- STEP 2: rename to final names ---
for bone in armature.bones:
    if bone.name in temp_map:
        bone.name = temp_map[bone.name]

print("Bone renaming complete.")