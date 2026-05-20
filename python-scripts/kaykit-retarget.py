# Script Objective
# This will be used to retarget the KayKit animation packs to a Mesh2Motion rig
# We will eventually need to do more work as the KayKit bones are much more limited than the M2M rig, 
# but it will bea  good start

# Bone remapping from KayKit bone names to Mesh2Motion
BONE_MAP = {
    # -- Core / Spine --------------------------------------
    "root": "root", # both have root. Just adding for completeness
    "hips":        "pelvis",     # Root of the spine chain
    "spine":    "spine_01",   # Lower spine
    "chest":      "spine_02",   # Upper spine (no BVH equivalent for spine_03)
    "neck":       "head", # KayKit doesn't have a neck bone. Not sure if results will look better mappin to neck or head bone in M2M

    # -- Left Arm ------------------------------------------
    "upperarm.l":     "upperarm_l", # Upper arm (shoulder to elbow)
    "lowerarm.l":   "lowerarm_l", # Forearm (elbow to wrist)
    "wrist.l":      "hand_l",     # KayKit also has a hand.l if that might give better results

    # -- Right Arm -----------------------------------------
    "upperarm.r":     "upperarm_r",
    "lowerarm.r":   "lowerarm_r",
    "wrist.r":      "hand_r",

    # -- Left Leg ------------------------------------------
    "upperleg.l":     "thigh_l",
    "lowerleg.l":      "calf_l",
    "foot.l":      "foot_l",
    "toes.r": "ball_l",

    # -- Right Leg -----------------------------------------
    "upperleg.r":     "thigh_r",
    "lowerleg.r":      "calf_r",
    "foot.r":      "foot_r",
    "toes.r": "ball_r"
}


# Import a GLB file from KayKit that has his rig + animations
# We will use that data to do the retargeting
def import_glb(path):
    # return the armature object from the bpy library
    pass


# pass in one of the animations from the imported armature
# and retarget the animations onto our current armature.
def retarget_animation(action_name):
    """
    Core retargeting function:
    
    we ask a simpler question: "Compared to the T-pose, how much did each bone rotate?"
    
    Answering this will help us just capture the delta and avoid issues with different "bone rolls" between the rigs
    """
    pass