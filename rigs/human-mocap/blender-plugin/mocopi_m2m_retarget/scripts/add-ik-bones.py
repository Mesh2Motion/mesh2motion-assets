import bpy
from mathutils import Vector


# ============================================================
# CONFIGURATION
# ============================================================

ARM_POLE_DISTANCE = 0.5
LEG_POLE_DISTANCE = 0.7

# Set this to True if you want to replace/rebuild existing poles.
RECREATE_POLES = True

# Extra vertical lift for knee poles, in case you want them to sit
# slightly above/below the actual knee height for visibility. The
# direction-based placement below already puts the pole AT knee
# height, so 0 is a sane default - nudge it a little if you want.
KNEE_POLE_VERTICAL_OFFSET = 0.0

# World-space directions the poles sit in, relative to the elbow/
# knee joint at rest. If these end up mirrored (front <-> back)
# on your rig, just flip the sign.
ELBOW_POLE_DIRECTION = Vector((0, 1, 0))   # behind the elbow
KNEE_POLE_DIRECTION = Vector((0, -1, 0))    # in front of the knee

POLES = [
    {"pole": "l_elbow_pole", "lower": "l_low_arm", "distance": ARM_POLE_DISTANCE, "direction": ELBOW_POLE_DIRECTION},
    {"pole": "r_elbow_pole", "lower": "r_low_arm", "distance": ARM_POLE_DISTANCE, "direction": ELBOW_POLE_DIRECTION},
    {"pole": "l_knee_pole",  "lower": "l_low_leg", "distance": LEG_POLE_DISTANCE, "direction": KNEE_POLE_DIRECTION, "knee": True},
    {"pole": "r_knee_pole",  "lower": "r_low_leg", "distance": LEG_POLE_DISTANCE, "direction": KNEE_POLE_DIRECTION, "knee": True},
]


# ============================================================
# UTILITIES
# ============================================================

def get_armature():
    obj = bpy.context.object
    if obj is None or obj.type != 'ARMATURE':
        raise RuntimeError("Select an armature first.")
    return obj


# ============================================================
# CREATE POLE BONES + LOCK IN THEIR OFFSET FROM THE UPPER LIMB
# ============================================================

def create_pole_bones(armature):
    """
    Create each pole bone at its rest-pose position, then record
    that position as a fixed offset in the UPPER limb bone's local
    (rest) space. During baking we just re-project that fixed
    offset through the upper limb's animated matrix each frame -
    so the pole rigidly follows the shoulder/hip, and elbow/knee
    bend has no say in where it ends up.
    """

    bpy.context.view_layer.objects.active = armature
    bpy.ops.object.mode_set(mode='EDIT')
    edit_bones = armature.data.edit_bones

    offsets = {}

    for info in POLES:
        pole_name, lower_name = info["pole"], info["lower"]
        lower = edit_bones.get(lower_name)

        if lower is None:
            print(f"WARNING: '{lower_name}' not found, skipping '{pole_name}'.")
            continue

        existing = edit_bones.get(pole_name)
        if existing:
            if not RECREATE_POLES:
                print(f"'{pole_name}' already exists, skipping.")
                continue
            edit_bones.remove(existing)

        upper = lower.parent
        if upper is None:
            print(f"WARNING: '{lower_name}' has no parent, skipping '{pole_name}'.")
            continue

        pole_pos = lower.head + info["direction"].normalized() * info["distance"]

        pole = edit_bones.new(pole_name)
        pole.parent = upper
        pole.use_connect = False
        pole.head = pole_pos
        pole.tail = pole_pos + (lower.head - pole_pos).normalized() * min(info["distance"] * 0.25, 0.2)

        offsets[pole_name] = {
            "upper": upper.name,
            "offset": upper.matrix.inverted() @ pole_pos,
            # Pole's own rest orientation relative to its parent (now
            # the upper arm/leg, same bone the offset above is built
            # from), so the two stay consistent frame to frame.
            "rest_local": upper.matrix.inverted() @ pole.matrix,
            "knee": info.get("knee", False),
        }

        print(f"Created '{pole_name}' (follows '{upper.name}')")

    bpy.ops.object.mode_set(mode='POSE')
    return offsets


# ============================================================
# BAKE POLE ANIMATION
# ============================================================

def bake_pole_animation(armature, offsets):
    """
    For every frame, re-project each pole's fixed rest-space offset
    through its upper limb's current pose matrix and keyframe the
    result. No elbow/knee/wrist/ankle position is read here at all.
    """

    scene = bpy.context.scene
    original_frame = scene.frame_current

    print(f"Baking pole animation: frames {scene.frame_start} -> {scene.frame_end}")

    for frame in range(scene.frame_start, scene.frame_end + 1):
        scene.frame_set(frame)

        for pole_name, info in offsets.items():
            pole_pb = armature.pose.bones.get(pole_name)
            upper_pb = armature.pose.bones.get(info["upper"])

            if pole_pb is None or upper_pb is None:
                continue

            world_pos = upper_pb.matrix @ info["offset"]

            if info["knee"]:
                world_pos.z += KNEE_POLE_VERTICAL_OFFSET

            parent = pole_pb.parent
            if parent:
                local_point = parent.matrix.inverted() @ world_pos
                pole_pb.location = info["rest_local"].inverted() @ local_point
            else:
                pole_pb.location = world_pos

            pole_pb.keyframe_insert(data_path="location", frame=frame, group="Pole Bones")

    scene.frame_set(original_frame)
    print("Pole animation bake complete.")


# ============================================================
# CONFIGURE POLE BONES
# ============================================================

def configure_pole_bones(armature):
    for info in POLES:
        pole_pb = armature.pose.bones.get(info["pole"])
        if pole_pb is None:
            continue

        pole_pb.color.palette = 'THEME04'
        pole_pb.lock_scale[0] = True
        pole_pb.lock_scale[1] = True
        pole_pb.lock_scale[2] = True


# ============================================================
# MAIN
# ============================================================

def main():
    armature = get_armature()
    print(f"Armature: {armature.name}")

    offsets = create_pole_bones(armature)
    bake_pole_animation(armature, offsets)
    configure_pole_bones(armature)

    print("Created:", ", ".join(offsets.keys()) if offsets else "(none)")


main()