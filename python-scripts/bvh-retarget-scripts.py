# List of joints in the BVH file

## Run this in blender's script tab and open the Window > show console to see results
import bpy

# Run this once for your armature, once for the imported BVH armature
obj = bpy.data.objects.get("05_01")  # change this
if obj and obj.type == 'ARMATURE':
    for bone in obj.pose.bones:
        print(bone.name)


## Workflow
# open the Pose mesh2motion rig file. Then run the following script. This will load the BVH file at a frame do the retargeting for a specific frame

import bpy
import os
from mathutils import Matrix

# --- CONFIG -------------------------------------------------------------------
BVH_PATH        = "C:\\Users\\scott\\Downloads\\cmuconvert-daz-01-09\\05\\05_05.bvh"  # change this
TARGET_ARMATURE = "Armature"                # Mesh2Motion armature name
POSE_FRAME      = 213  # which frame of the BVH to grab (0 should be T-pose)
OUTPUT_FRAME    = 0                         # which frame to key it on your rig (Mesh2Motion target frame)
TPOSE_FRAME     = 0                         # frame 0 is the T-pose in BVH files
# ------------------------------------------------------------------------------

BONE_MAP = {
    # -- Core / Spine --------------------------------------
    "hip":        "pelvis",
    "abdomen":    "spine_01",
    "chest":      "spine_02",
    "neck":       "neck_01",
    "head":       "head",

    # -- Left Arm ------------------------------------------
    "lCollar":    "clavicle_l",
    "lShldr":     "upperarm_l",
    "lForeArm":   "lowerarm_l",
    "lHand":      "hand_l",

    # -- Left Hand Fingers ---------------------------------
    "lThumb1":    "thumb_01_l",
    "lThumb2":    "thumb_02_l",
    "lIndex1":    "index_01_l",
    "lIndex2":    "index_02_l",
    "lMid1":      "middle_01_l",
    "lMid2":      "middle_02_l",
    "lRing1":     "ring_01_l",
    "lRing2":     "ring_02_l",
    "lPinky1":    "pinky_01_l",
    "lPinky2":    "pinky_02_l",

    # -- Right Arm -----------------------------------------
    "rCollar":    "clavicle_r",
    "rShldr":     "upperarm_r",
    "rForeArm":   "lowerarm_r",
    "rHand":      "hand_r",

    # -- Right Hand Fingers --------------------------------
    "rThumb1":    "thumb_01_r",
    "rThumb2":    "thumb_02_r",
    "rIndex1":    "index_01_r",
    "rIndex2":    "index_02_r",
    "rMid1":      "middle_01_r",
    "rMid2":      "middle_02_r",
    "rRing1":     "ring_01_r",
    "rRing2":     "ring_02_r",
    "rPinky1":    "pinky_01_r",
    "rPinky2":    "pinky_02_r",

    # -- Left Leg ------------------------------------------
    "lThigh":     "thigh_l",
    "lShin":      "calf_l",
    "lFoot":      "foot_l",

    # -- Right Leg -----------------------------------------
    "rThigh":     "thigh_r",
    "rShin":      "calf_r",
    "rFoot":      "foot_r",
}


def import_bvh(path):
    before = set(bpy.data.objects.keys())
    bpy.ops.import_anim.bvh(
        filepath=path,
        rotate_mode='QUATERNION',
        axis_forward='-Z',
        axis_up='Y'
    )
    after = set(bpy.data.objects.keys())
    for name in (after - before):
        obj = bpy.data.objects[name]
        if obj.type == 'ARMATURE':
            return obj
    return None


def get_pose_matrix(armature, bone_name, frame):
    """Get a bone's local pose matrix at a specific frame."""
    bpy.context.scene.frame_set(frame)
    bpy.context.view_layer.update()
    bone = armature.pose.bones.get(bone_name)
    if not bone:
        return None
    # Local pose matrix = the rotation/translation relative to parent
    return bone.matrix_basis.copy()


def zero_out_root(target_arm, frame):
    root_bone = target_arm.pose.bones.get("root")
    if root_bone:
        root_bone.rotation_mode = 'QUATERNION'
        root_bone.rotation_quaternion = (1, 0, 0, 0)
        root_bone.location = (0, 0, 0)
        root_bone.keyframe_insert(data_path="rotation_quaternion", frame=frame)
        root_bone.keyframe_insert(data_path="location", frame=frame)
        print("  ✓ root bone zeroed out")


def apply_retargeted_pose(source_arm, target_arm, tpose_frame, pose_frame, output_frame):

    bpy.ops.object.select_all(action='DESELECT')
    target_arm.select_set(True)
    bpy.context.view_layer.objects.active = target_arm
    bpy.ops.object.mode_set(mode='POSE')

    applied = []
    skipped = []

    for bvh_name, tgt_name in BONE_MAP.items():

        src_bone = source_arm.pose.bones.get(bvh_name)
        tgt_bone = target_arm.pose.bones.get(tgt_name)

        if not src_bone:
            skipped.append(f"source not found: {bvh_name}")
            continue
        if not tgt_bone:
            skipped.append(f"target not found: {tgt_name}")
            continue

        # -- Get the bone's local matrix at T-pose and at the target pose --
        bpy.context.scene.frame_set(tpose_frame)
        bpy.context.view_layer.update()
        mat_tpose = src_bone.matrix_basis.copy()

        bpy.context.scene.frame_set(pose_frame)
        bpy.context.view_layer.update()
        mat_pose = src_bone.matrix_basis.copy()

        # -- Delta = "what rotation was applied on top of the T-pose" ------
        # mat_tpose * delta = mat_pose
        # therefore: delta = mat_tpose.inverted() * mat_pose
        delta = mat_tpose.inverted() @ mat_pose

        # -- Apply that delta on top of the target bone's current rest -----
        # Since target is already in T-pose, we just apply the delta directly
        _, rot, _ = delta.decompose()
        rot.normalize()

        bpy.context.scene.frame_set(output_frame)
        tgt_bone.rotation_mode = 'QUATERNION'
        tgt_bone.rotation_quaternion = rot
        tgt_bone.keyframe_insert(data_path="rotation_quaternion", frame=output_frame)

        applied.append(bvh_name)

    zero_out_root(target_arm, output_frame)
    bpy.ops.object.mode_set(mode='OBJECT')

    print(f"\n  ✓ Applied: {len(applied)} bones")
    if skipped:
        print(f"  ⚠ Skipped ({len(skipped)}):")
        for s in skipped:
            print(f"      {s}")


def cleanup_bvh_armature(bvh_arm):
    bpy.ops.object.select_all(action='DESELECT')
    bvh_arm.select_set(True)
    bpy.ops.object.delete()
    print("  ✓ BVH armature cleaned up")


# --- MAIN ---------------------------------------------------------------------
print("\n" + "="*50)
print("BVH Retarget → Mesh2Motion (T-pose delta method)")
print("="*50)

target = bpy.data.objects.get(TARGET_ARMATURE)
if not target:
    print(f"ERROR: Target armature '{TARGET_ARMATURE}' not found.")
else:
    print(f"Importing: {BVH_PATH}")
    bvh_arm = import_bvh(BVH_PATH)

    if not bvh_arm:
        print("ERROR: BVH import failed.")
    else:
        print(f"Computing delta: frame {TPOSE_FRAME} → frame {POSE_FRAME}")
        apply_retargeted_pose(bvh_arm, target, TPOSE_FRAME, POSE_FRAME, OUTPUT_FRAME)
        cleanup_bvh_armature(bvh_arm)
        print(f"\nDone! Pose keyed at frame {OUTPUT_FRAME}")