# this is used in the human-pose folder in the rigs folder
# the idea is we will take a snapshot of a keyframe from a BVH file by retargeting a single frame
# this script doesn't change much, so it will already be in the human pose blender file with tweaking
# the config sections to add more poses


import bpy          # Blender's Python API - gives access to all Blender data and operators
import os           # Standard Python file/path utilities
from mathutils import Matrix  # Blender's math library for 4x4 matrices, vectors, quaternions

# --- CONFIG -------------------------------------------------------------------
# These are the only values you should need to change between runs.
# Keeping them at the top makes the script easy to reuse without digging
# into the functions below.

BVH_PATH        = "C:\\Users\\scott\\Downloads\\cmuconvert-daz-01-09\\05\\05_05.bvh"
TARGET_ARMATURE = "Armature"   # The name of your Mesh2Motion rig in the Blender outliner
POSE_FRAME      = 329        # The BVH frame you want to extract as a pose
OUTPUT_FRAME    = 0            # The timeline frame on your rig where the pose will be keyed
TPOSE_FRAME     = 0            # CMU BVH files always start with a T-pose on frame 0.
                               # This is our "zero rotation" reference point.
# ------------------------------------------------------------------------------

# BONE_MAP is a dictionary that pairs BVH bone names (left/keys) with their
# corresponding Mesh2Motion bone names (right/values).
#
# Why this is needed: The two skeletons were built independently and use
# completely different naming conventions. This map is the bridge that tells
# the script "when you read lShldr from the BVH, write it to upperarm_l on
# the target". Without this, the script has no way to know which bones
# correspond to each other.
#
# Bones intentionally omitted from this map:
#   - rButtock / lButtock: No clean equivalent in Mesh2Motion rig
#   - leftEye / rightEye:  No eye bones in Mesh2Motion rig
#   - All _leaf bones:     These are zero-length endpoint markers, not real bones
#   - spine_03, ball_l/r:  BVH has no equivalent segments for these
#   - finger _03 segments: BVH fingers only go 2 segments deep

BONE_MAP = {
    # -- Core / Spine --------------------------------------
    "hip":        "pelvis",     # Root of the spine chain
    "abdomen":    "spine_01",   # Lower spine
    "chest":      "spine_02",   # Upper spine (no BVH equivalent for spine_03)
    "neck":       "neck_01",
    "head":       "head",

    # -- Left Arm ------------------------------------------
    "lCollar":    "clavicle_l", # Collarbone - connects shoulder to spine
    "lShldr":     "upperarm_l", # Upper arm (shoulder to elbow)
    "lForeArm":   "lowerarm_l", # Forearm (elbow to wrist)
    "lHand":      "hand_l",     # Wrist/hand root

    # -- Left Hand Fingers ---------------------------------
    # BVH fingers go 2 segments deep (1, 2), Mesh2Motion goes 3 (_01, _02, _03)
    # The third segment (_03) will remain in rest pose since BVH has no data for it
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
    """
    Import a BVH file into the current Blender scene and return its armature.

    BVH (BioVision Hierarchy) is a text-based motion capture format. It stores
    a skeleton as a hierarchy of joints, each with channel data (rotation/position)
    per frame. Blender's built-in BVH importer converts this into an armature
    with keyframed bone rotations.

    The axis settings below (axis_forward='-Z', axis_up='Y') tell Blender how
    to interpret the BVH coordinate system. CMU BVH files use Y-up convention,
    so we set axis_up='Y' to match. Getting these wrong causes the whole skeleton
    to import facing the wrong direction.

    We detect the newly created armature by comparing the scene's object list
    before and after the import — whatever is new and is an ARMATURE type is
    our BVH skeleton.
    """
    # Snapshot the scene before import so we can identify the new object
    before = set(bpy.data.objects.keys())

    bpy.ops.import_anim.bvh(
        filepath=path,
        rotate_mode='QUATERNION',  # Store rotations as quaternions, not Euler angles.
                                   # Quaternions avoid gimbal lock and interpolate better,
                                   # which matters when we read matrix_basis later.
        axis_forward='-Z',         # BVH forward axis maps to Blender's -Z
        axis_up='Y'                # BVH up axis maps to Blender's Y
    )

    # Find what was added to the scene by the import
    after = set(bpy.data.objects.keys())
    for name in (after - before):
        obj = bpy.data.objects[name]
        if obj.type == 'ARMATURE':
            return obj  # Return the first (and typically only) new armature

    return None  # Import failed or produced no armature


def zero_out_root(target_arm, frame):
    """
    Reset the root bone to identity (no rotation, no translation) and keyframe it.

    Why: The BVH skeleton has no 'root' bone — its hip bone IS the root.
    Our Mesh2Motion rig has an extra 'root' bone above the pelvis that controls
    the whole skeleton's world position. Since the BVH gives us no data for this
    bone, we explicitly zero it out so it doesn't inherit stale transforms from
    a previous operation or a prior keyframe on the timeline.

    Setting rotation_quaternion to (1, 0, 0, 0) is the quaternion identity —
    it means "no rotation". Setting location to (0, 0, 0) keeps the character
    at the scene origin.
    """
    root_bone = target_arm.pose.bones.get("root")
    if root_bone:
        root_bone.rotation_mode = 'QUATERNION'
        root_bone.rotation_quaternion = (1, 0, 0, 0)  # Identity quaternion = no rotation
        root_bone.location = (0, 0, 0)                 # Place at scene origin
        root_bone.keyframe_insert(data_path="rotation_quaternion", frame=frame)
        root_bone.keyframe_insert(data_path="location", frame=frame)
        print("  ✓ root bone zeroed out")
    else:
        print("  — no root bone found, skipping")


def apply_retargeted_pose(source_arm, target_arm, tpose_frame, pose_frame, output_frame):
    """
    Core retargeting function. Transfers a pose from the BVH armature onto the
    Mesh2Motion armature using the T-pose delta method.

    THE KEY IDEA — T-POSE DELTA METHOD:
    -------------------------------------
    Previous approaches tried to directly convert rotations between the two rigs,
    which failed because each rig has different internal bone axis orientations
    (i.e. the "zero rotation" state of each bone points in a different direction).

    Instead, we ask a simpler question:
        "Compared to the T-pose, how much did each bone rotate?"

    That rotation difference (the "delta") is pure movement intent — it's the
    same regardless of which skeleton it came from. Since both skeletons start
    from a T-pose, we can apply that same delta to the target skeleton and get
    a matching pose.

    Mathematically:
        mat_tpose * delta = mat_pose
        therefore: delta = mat_tpose.inverted() @ mat_pose

    This works because matrix_basis stores each bone's transform relative to
    its parent in local space. At T-pose, each bone has some baseline matrix.
    At any other frame, that matrix includes the T-pose baseline PLUS the
    intentional rotation. By dividing out the baseline (multiplying by its
    inverse), we're left with only the intentional rotation.
    """

    # -- Set up Pose Mode on the target armature ----------------------------
    # We must be in Pose Mode to read and write pose bone transforms.
    # Deselect everything first to avoid accidentally affecting other objects.
    bpy.ops.object.select_all(action='DESELECT')
    target_arm.select_set(True)
    bpy.context.view_layer.objects.active = target_arm
    bpy.ops.object.mode_set(mode='POSE')

    applied = []  # Tracks successfully retargeted bones for the summary report
    skipped = []  # Tracks bones that couldn't be processed and why

    # -- Process each bone pair in the map ---------------------------------
    for bvh_name, tgt_name in BONE_MAP.items():

        # Look up the actual pose bone objects by name
        # pose.bones gives us PoseBone objects which have transform data,
        # as opposed to data.bones which gives the static rest-pose EditBone
        src_bone = source_arm.pose.bones.get(bvh_name)
        tgt_bone = target_arm.pose.bones.get(tgt_name)

        # Gracefully skip bones that don't exist rather than crashing.
        # This handles cases where a BVH file has fewer bones than expected,
        # or where bone names in the map don't exactly match the rig.
        if not src_bone:
            skipped.append(f"source not found: {bvh_name}")
            continue
        if not tgt_bone:
            skipped.append(f"target not found: {tgt_name}")
            continue

        # -- Step 1: Read the source bone's matrix_basis at T-pose ---------
        # matrix_basis is the bone's local transform relative to its parent.
        # At frame 0 (T-pose), this represents the bone's "neutral" orientation —
        # the baseline we'll subtract out to isolate intentional movement.
        # .copy() is critical here — without it, the variable just holds a
        # reference to the matrix, which would update as we change frames.
        bpy.context.scene.frame_set(tpose_frame)
        bpy.context.view_layer.update()  # Ensure the pose is fully evaluated
        mat_tpose = src_bone.matrix_basis.copy()

        # -- Step 2: Read the source bone's matrix_basis at the pose frame --
        # Same bone, different frame. This matrix encodes both the T-pose
        # baseline AND the intentional rotation we want to transfer.
        bpy.context.scene.frame_set(pose_frame)
        bpy.context.view_layer.update()
        mat_pose = src_bone.matrix_basis.copy()

        # -- Step 3: Compute the delta (rotation intent) --------------------
        # We want to isolate what changed between T-pose and the target pose.
        # Matrix multiplication is not commutative, so order matters:
        #   mat_tpose @ delta = mat_pose
        #   delta = mat_tpose.inverted() @ mat_pose
        #
        # Think of it like: if you walked 5 steps forward (tpose) and ended
        # up 8 steps from start (pose), you must have taken 3 extra steps (delta).
        # The inverse "undoes" the T-pose, leaving only the extra movement.
        delta = mat_tpose.inverted() @ mat_pose

        # -- Step 4: Extract just the rotation component from the delta -----
        # decompose() splits a 4x4 matrix into (location, rotation, scale).
        # We only want rotation — we don't want to transfer the BVH skeleton's
        # bone lengths or translation offsets onto our differently-proportioned rig.
        # rot comes back as a Quaternion (since we imported with rotate_mode='QUATERNION').
        _, rot, _ = delta.decompose()

        # Normalize the quaternion to ensure it's a valid unit rotation.
        # Floating point operations can introduce tiny errors that accumulate,
        # and a non-unit quaternion would produce skewing/scaling artifacts.
        rot.normalize()

        # -- Step 5: Apply the rotation to the target bone and keyframe it --
        # We set the frame before writing so the keyframe lands in the right place.
        bpy.context.scene.frame_set(output_frame)

        # Set rotation mode to QUATERNION to match how we're storing the rotation.
        # If this doesn't match the bone's current mode, Blender will convert but
        # it's safer and clearer to set it explicitly.
        tgt_bone.rotation_mode = 'QUATERNION'
        tgt_bone.rotation_quaternion = rot

        # keyframe_insert writes the current value of the property as a keyframe
        # at the given frame. Without this, the rotation would be applied visually
        # but not saved — it would disappear when you scrub the timeline.
        tgt_bone.keyframe_insert(data_path="rotation_quaternion", frame=output_frame)

        applied.append(bvh_name)

    # -- Zero out the root bone ---------------------------------------------
    # Done after the main loop since it's a special case (no BVH source bone)
    zero_out_root(target_arm, output_frame)

    # Return to Object Mode — it's good practice to not leave Blender in
    # Pose Mode after a script runs, as it can interfere with subsequent operations
    bpy.ops.object.mode_set(mode='OBJECT')

    # -- Print summary to console -------------------------------------------
    print(f"\n  ✓ Applied: {len(applied)} bones")
    if skipped:
        print(f"  ⚠ Skipped ({len(skipped)}):")
        for s in skipped:
            print(f"      {s}")


def cleanup_bvh_armature(bvh_arm):
    """
    Delete the temporary BVH armature from the scene.

    The BVH armature was only needed as a data source — we've already extracted
    the rotation delta we needed. Leaving it in the scene would clutter the
    outliner and could interfere with subsequent script runs (e.g. the 'before'
    snapshot in import_bvh would include it, confusing the new-object detection).
    """
    bpy.ops.object.select_all(action='DESELECT')
    bvh_arm.select_set(True)
    bpy.ops.object.delete()
    print("  ✓ BVH armature cleaned up")


# --- MAIN ---------------------------------------------------------------------
# This block runs when you execute the script in Blender's scripting tab.
# The overall flow is:
#   1. Find the target armature in the scene
#   2. Import the BVH file as a temporary armature
#   3. Compute and apply the retargeted pose
#   4. Clean up the temporary BVH armature
#   5. Report results to the console

print("\n" + "="*50)
print("BVH Retarget → Mesh2Motion (T-pose delta method)")
print("="*50)

# Verify the target armature exists before doing anything else.
# bpy.data.objects.get() returns None if the name isn't found,
# which is safer than direct dict access which would raise a KeyError.
target = bpy.data.objects.get(TARGET_ARMATURE)
if not target:
    print(f"ERROR: Target armature '{TARGET_ARMATURE}' not found.")
    print(f"       Check the name in the Outliner and update TARGET_ARMATURE.")
else:
    print(f"Importing: {BVH_PATH}")
    bvh_arm = import_bvh(BVH_PATH)

    if not bvh_arm:
        print("ERROR: BVH import failed.")
        print("       Check that BVH_PATH is correct and the file exists.")
    else:
        print(f"Computing delta: frame {TPOSE_FRAME} → frame {POSE_FRAME}")
        apply_retargeted_pose(bvh_arm, target, TPOSE_FRAME, POSE_FRAME, OUTPUT_FRAME)
        cleanup_bvh_armature(bvh_arm)
        print(f"\nDone! Pose keyed at frame {OUTPUT_FRAME}")