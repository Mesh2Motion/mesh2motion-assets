import bpy
from mathutils import Vector


# ============================================================
# CONFIGURATION
# ============================================================

ARM_POLE_DISTANCE = 0.5
LEG_POLE_DISTANCE = 0.7

# Set this to True if you want to replace/rebuild existing poles.
RECREATE_POLES = True

# Move knee poles upward
# without this knees are a bit below the groun
KNEE_POLE_VERTICAL_OFFSET = 0.8


POLES = [
    {
        "pole": "l_elbow_pole",
        "upper": None,              # Automatically uses parent of l_low_arm
        "lower": "l_low_arm",
        "distance": ARM_POLE_DISTANCE,
    },
    {
        "pole": "r_elbow_pole",
        "upper": None,
        "lower": "r_low_arm",
        "distance": ARM_POLE_DISTANCE,
    },
    {
        "pole": "l_knee_pole",
        "upper": None,
        "lower": "l_low_leg",
        "distance": LEG_POLE_DISTANCE,
    },
    {
        "pole": "r_knee_pole",
        "upper": None,
        "lower": "r_low_leg",
        "distance": LEG_POLE_DISTANCE,
    },
]


# ============================================================
# UTILITIES
# ============================================================

def get_armature():
    obj = bpy.context.object

    if obj is None:
        raise RuntimeError("No object selected.")

    if obj.type != 'ARMATURE':
        raise RuntimeError(
            f"Selected object '{obj.name}' is not an armature."
        )

    return obj


def get_bone_names(armature, lower_name):
    """
    Find the upper and lower bones.

    The lower bone's parent is assumed to be the upper bone.
    """

    lower = armature.pose.bones.get(lower_name)

    if lower is None:
        raise RuntimeError(
            f"Could not find bone '{lower_name}'."
        )

    if lower.parent is None:
        raise RuntimeError(
            f"Bone '{lower_name}' has no parent."
        )

    upper = lower.parent

    return upper.name, lower.name


# ============================================================
# GEOMETRY
# ============================================================

def calculate_pole_position(
    point_a,
    point_b,
    point_c,
    distance,
    preferred_direction=None,
):
    """
    Calculate the pole position from three joints.

        A = shoulder / hip
        B = elbow / knee
        C = wrist / ankle

    The pole direction is the component of B that is
    perpendicular to the A -> C line.

    preferred_direction determines which of the two possible
    directions should be selected.

    For a typical Blender humanoid facing -Y:
        preferred_direction = Vector((0, -1, 0))

    Returns:
        pole_position
        bend_direction
    """

    ac = point_c - point_a

    if ac.length < 0.000001:
        return None, None

    ac_normalized = ac.normalized()

    # --------------------------------------------------------
    # Project B onto the A -> C line
    # --------------------------------------------------------

    ab = point_b - point_a

    projection_length = ab.dot(ac_normalized)

    projected_point = (
        point_a +
        ac_normalized * projection_length
    )

    # --------------------------------------------------------
    # Calculate actual bend direction
    # --------------------------------------------------------

    bend_vector = point_b - projected_point

    if bend_vector.length < 0.000001:
        return None, None

    bend_direction = bend_vector.normalized()

    # --------------------------------------------------------
    # Resolve the +/- ambiguity
    # --------------------------------------------------------
    #
    # The bend plane has two possible pole directions:
    #
    #       +bend_direction
    #
    #       -bend_direction
    #
    # We choose the one closest to preferred_direction.
    #

    if preferred_direction is not None:

        preferred = preferred_direction.normalized()

        if bend_direction.dot(preferred) < 0:
            bend_direction.negate()

    # --------------------------------------------------------
    # Position pole
    # --------------------------------------------------------

    pole_position = (
        point_b +
        bend_direction * distance
    )

    return pole_position, bend_direction


# ============================================================
# CREATE POLE BONES
# ============================================================

def create_pole_bones(armature):
    """
    Create the four pole bones.

    The bones are initially positioned using the rest pose.
    Their actual animation will be baked later.
    """

    bpy.context.view_layer.objects.active = armature
    armature.select_set(True)

    bpy.ops.object.mode_set(mode='EDIT')

    edit_bones = armature.data.edit_bones

    created = []

    for info in POLES:

        pole_name = info["pole"]
        lower_name = info["lower"]

        lower = edit_bones.get(lower_name)

        if lower is None:
            print(
                f"WARNING: '{lower_name}' does not exist. "
                f"Skipping '{pole_name}'."
            )
            continue

        # ----------------------------------------------------
        # Delete existing pole
        # ----------------------------------------------------

        existing = edit_bones.get(pole_name)

        if existing:

            if RECREATE_POLES:
                edit_bones.remove(existing)
            else:
                print(
                    f"Pole '{pole_name}' already exists. "
                    f"Skipping."
                )
                continue

        # ----------------------------------------------------
        # Create bone
        # ----------------------------------------------------

        pole = edit_bones.new(pole_name)

        # Parent to lower limb bone.
        #
        # This is useful because the pole will remain part of
        # the FK hierarchy while its location is baked.
        pole.parent = lower
        pole.use_connect = False

        # ----------------------------------------------------
        # Calculate rest-pose pole
        # ----------------------------------------------------

        if lower.parent:
            upper = lower.parent

            a = upper.head
            b = lower.head
            c = lower.tail

            pole_position, bend_direction = (
                calculate_pole_position(
                    a,
                    b,
                    c,
                    info["distance"]
                )
            )

            if pole_position is None:
                # Straight limb fallback.
                #
                # Use a direction based on the bone's local Y
                # axis / roll.
                print(
                    f"WARNING: '{lower_name}' is nearly straight "
                    f"in rest pose. Using fallback pole direction."
                )

                limb = (lower.tail - lower.head).normalized()

                fallback = Vector((0, 0, 1))

                if abs(limb.dot(fallback)) > 0.9:
                    fallback = Vector((0, 1, 0))

                bend_direction = (
                    fallback -
                    limb * fallback.dot(limb)
                ).normalized()

                pole_position = (
                    lower.head +
                    bend_direction * info["distance"]
                )

        else:
            pole_position = (
                lower.head +
                Vector((0, 1, 0)) *
                info["distance"]
            )

        pole.head = pole_position

        # Point the pole bone back toward the joint.
        pole.tail = (
            pole_position +
            (lower.head - pole_position).normalized() *
            min(info["distance"] * 0.25, 0.2)
        )

        created.append(pole_name)

        print(
            f"Created '{pole_name}' "
            f"parented to '{lower_name}'"
        )

    bpy.ops.object.mode_set(mode='POSE')

    return created


# ============================================================
# BONE ORIENTATION
# ============================================================

def calculate_pole_rotation(armature, pole_pb):
    """
    Rotate the pole bone so it points toward the elbow/knee.

    This is primarily useful for visualization.

    The IK solver generally cares about the pole bone's
    POSITION, not its rotation.
    """

    return


# ============================================================
# BAKE POLE ANIMATION
# ============================================================

def bake_pole_animation(armature):
    """
    Calculate pole positions on every frame from the
    animated FK skeleton and keyframe the pole bones.

    This is the important part of the process.
    """

    scene = bpy.context.scene

    frame_start = scene.frame_start
    frame_end = scene.frame_end

    print()
    print("========================================")
    print("BAKING POLE ANIMATION")
    print("========================================")
    print(
        f"Frames: {frame_start} -> {frame_end}"
    )

    # --------------------------------------------------------
    # Save current frame
    # --------------------------------------------------------

    original_frame = scene.frame_current

    # --------------------------------------------------------
    # Cache bone relationships
    # --------------------------------------------------------

    pole_info = []

    for info in POLES:

        pole_pb = armature.pose.bones.get(info["pole"])
        lower_pb = armature.pose.bones.get(info["lower"])

        if pole_pb is None:
            print(
                f"WARNING: Pole '{info['pole']}' not found."
            )
            continue

        if lower_pb is None:
            continue

        if lower_pb.parent is None:
            continue

        upper_pb = lower_pb.parent

        pole_info.append({
            "pole": pole_pb,
            "upper": upper_pb,
            "lower": lower_pb,
            "distance": info["distance"],
        })

    # --------------------------------------------------------
    # Bake every frame
    # --------------------------------------------------------

    for frame in range(frame_start, frame_end + 1):

        scene.frame_set(frame)

        for info in pole_info:

            pole_pb = info["pole"]
            upper_pb = info["upper"]
            lower_pb = info["lower"]

            # ------------------------------------------------
            # Get animated FK joint positions.
            #
            # PoseBone.head/tail are in armature-object space.
            # ------------------------------------------------

            a = Vector(upper_pb.head)
            b = Vector(lower_pb.head)
            c = Vector(lower_pb.tail)

            pole_position, bend_direction = (
                calculate_pole_position(
                    a,
                    b,
                    c,
                    info["distance"],
                    preferred_direction=Vector((0, -1, 0))
                )
            )

            if pole_position is None:
                # Limb is completely/near completely straight.
                # Keep the previous pole position instead of
                # letting it jump unpredictably.
                continue
            
            # add offset to knee position to better align with
            # the knee height
            if "knee" in info["pole"].name:
                pole_position.y += KNEE_POLE_VERTICAL_OFFSET
            

            # ------------------------------------------------
            # Convert desired armature-space position into
            # the pole bone's parent-relative location.
            # ------------------------------------------------

            parent = pole_pb.parent

            if parent:

                # Convert desired location into the parent's
                # pose space.
                parent_inverse = (
                    parent.matrix.inverted()
                )

                local_position = (
                    parent_inverse @
                    Vector(pole_position)
                )

                pole_pb.location = local_position

            else:
                pole_pb.location = pole_position

            # ------------------------------------------------
            # Keyframe only location.
            # ------------------------------------------------

            pole_pb.keyframe_insert(
                data_path="location",
                frame=frame,
                group="Pole Bones"
            )

    # --------------------------------------------------------
    # Restore frame
    # --------------------------------------------------------

    scene.frame_set(original_frame)

    print("Pole animation bake complete.")


# ============================================================
# CLEAN UP / ADD IK-FRIENDLY CONSTRAINTS
# ============================================================

def configure_pole_bones(armature):
    """
    Configure the pole bones for use as IK targets.

    We deliberately don't add a Damped Track constraint here.

    The pole's LOCATION is what an IK pole target needs.
    Its rotation isn't important to the IK solver.
    """

    for info in POLES:

        pole_pb = armature.pose.bones.get(info["pole"])

        if pole_pb is None:
            continue

        # Display as a custom-shaped-ish helper bone.
        # This also makes it easier to select in Blender.
        pole_pb.color.palette = 'THEME04'

        # Prevent accidental scale changes.
        pole_pb.lock_scale[0] = True
        pole_pb.lock_scale[1] = True
        pole_pb.lock_scale[2] = True


# ============================================================
# MAIN
# ============================================================

def main():

    armature = get_armature()

    print()
    print("========================================")
    print("FK → POLE BONE RETARGET")
    print("========================================")
    print(f"Armature: {armature.name}")

    # --------------------------------------------------------
    # Create bones
    # --------------------------------------------------------

    create_pole_bones(armature)

    # --------------------------------------------------------
    # Bake animated positions
    # --------------------------------------------------------

    bake_pole_animation(armature)

    # --------------------------------------------------------
    # Configure
    # --------------------------------------------------------

    configure_pole_bones(armature)

    print()
    print("========================================")
    print("DONE")
    print("========================================")
    print()
    print("Created:")
    print("  l_elbow_pole")
    print("  r_elbow_pole")
    print("  l_knee_pole")
    print("  r_knee_pole")
    print()
    print("These bones contain baked pole positions")
    print("derived from the FK animation.")
    print()


main()