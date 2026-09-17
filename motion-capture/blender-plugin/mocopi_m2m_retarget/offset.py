"""Shift the whole character in space, without changing the performance.

A mocopi capture often lands in the wrong place: the character starts a metre
to the side of where the clip wants it, or floats above the floor. That is a
placement problem, not an animation problem, so the fix is a constant offset
applied to every frame rather than anything that touches the motion.

**The offset is typed in, deliberately.** 0.6.0 also had a "centre on the
current frame" mode that computed the horizontal delta to put the hips over
the origin. It was removed in 0.6.1: the character moves around over the
course of a capture, so no single frame is the right one to centre on, and any
automatic answer is a guess at which moment matters. The person watching the
clip knows where it should sit; the tool's job is to apply that, exactly.

**Which bones move, and why it is not "all of them".** Every control on this
rig is a direct child of `DRV_root` -- the hips, the two hand IK targets, the
two foot IK targets, and the four poles -- and everything else either hangs
off one of them (the spine and head chain parent to `CTRL_Hips`) or is
constraint-driven off the `DRV_` bones (the whole export skeleton). So the
nine children of `DRV_root` are exactly the set that carries the character.
Adding an offset to every bone that happens to own a location curve would be
wrong twice over: the nested bones would take the shift once themselves and
again from their parent, and since `pose_bone.location` runs along each bone's
own rest axes, one vector would point in a different direction per bone.

This is the same nine that `root_motion` records and compensates, for the same
reason.

**Why not just move `DRV_root`.** It would work, and it would be less code --
but `root_motion.extract` rebuilds `DRV_root`'s path from scratch and zeroes
it at the first frame, so any offset parked there is silently discarded the
next time root motion runs. Offsetting the children instead survives that: the
extraction reads the hips' world path, which already includes the offset, and
folds it into the root path. The character stays where it was put.

**The offset is world-space.** `pose_bone.location` is not, so each bone's
delta is the world vector rotated into that bone's rest basis. A bone whose
rest orientation differs from the world axes therefore gets different numbers
in its channels for the same visible move, which is correct and is why this
cannot be done by typing the same three numbers onto every channel by hand.

The pass is additive and exactly reversible -- run it again with the negative
vector and the curves return to what they were, to floating-point. It changes
no key's timing and adds no keys, except on a control that had no location
curve at all, where it has to create one.
"""

import bpy
from mathutils import Vector

from .root_motion import ROOT_BONE


class OffsetError(Exception):
    """Raised when the offset cannot be applied."""


def control_bones(armature, root_name=ROOT_BONE):
    """The direct children of DRV_root: the bones that carry the character."""
    root = armature.pose.bones.get(root_name)
    if root is None:
        raise OffsetError(
            "'{}' has no '{}' bone, so this does not look like the "
            "Mesh2Motion rig".format(armature.name, root_name)
        )

    children = [bone for bone in armature.pose.bones if bone.parent == root]
    if not children:
        raise OffsetError("'{}' has no bones parented to it".format(root_name))

    return children


def world_to_bone(armature, pose_bone, delta):
    """A world-space translation expressed in one bone's location channels.

    ``pose_bone.location`` translates along the bone's own rest axes, so the
    world vector has to be rotated into that basis. Rotation only -- the rest
    head offset is a position, not a direction, and including it would move
    the bone to the world origin rather than by the delta.
    """
    basis = armature.matrix_world.to_3x3() @ pose_bone.bone.matrix_local.to_3x3()
    return basis.inverted() @ Vector(delta)


def shift_curves(curves, delta_local):
    """Add a per-axis delta to every key of a bone's location curves.

    Handles move with their keys, exactly as in ``cleanup.write_values`` and
    for the same reason: leaving them where they were would keep the shape of
    the pre-shift curve and bend the motion around the new values.
    """
    moved = 0

    for fcurve in curves:
        shift = delta_local[fcurve.array_index]
        if shift == 0.0:
            continue

        points = fcurve.keyframe_points
        count = len(points)
        if count == 0:
            continue

        for attribute in ("co", "handle_left", "handle_right"):
            buffer = [0.0] * (count * 2)
            points.foreach_get(attribute, buffer)
            for index in range(count):
                buffer[index * 2 + 1] += shift
            points.foreach_set(attribute, buffer)

        fcurve.update()
        moved += 1

    return moved


def apply(armature, offset=(0.0, 0.0, 0.0), frame=None, root_name=ROOT_BONE):
    """Shift the character by a world-space offset. Returns a summary dict.

    ``frame`` only decides where a key goes on a control that had no location
    curve at all; it has no bearing on the offset itself, which applies to
    every frame.
    """
    from . import cleanup

    delta = Vector(offset)

    if delta.length == 0.0:
        raise OffsetError(
            "The offset is 0, 0, 0 -- type a distance into the X / Y / Z "
            "fields above the button first"
        )

    controls = control_bones(armature, root_name=root_name)

    try:
        action_curves = cleanup.action_curves(armature)
    except cleanup.CleanupError as exc:
        raise OffsetError(str(exc))

    by_bone = {}
    for fcurve in action_curves:
        if not fcurve.data_path.endswith(".location"):
            continue
        name = cleanup.bone_name(fcurve.data_path)
        if name is not None:
            by_bone.setdefault(name, []).append(fcurve)

    channels = 0
    created = []

    for pose_bone in controls:
        delta_local = world_to_bone(armature, pose_bone, delta)
        curves = by_bone.get(pose_bone.name)

        if curves:
            channels += shift_curves(curves, delta_local)
            continue

        # No location curve on this control at all. Leaving it behind would
        # break the rig's geometry -- a pole left a metre from its limb points
        # the knee somewhere else entirely -- so give it one key holding the
        # offset. keyframe_insert is used rather than building the curve by
        # hand: creating a channel means creating a slot and channelbag on
        # 4.4+, which is the most version-divergent corner of the API.
        pose_bone.location = pose_bone.location + delta_local
        pose_bone.keyframe_insert(
            data_path="location",
            frame=bpy.context.scene.frame_current if frame is None else frame,
            group="Offset",
        )
        created.append(pose_bone.name)
        channels += 3

    return {
        "bones": len(controls),
        "channels": channels,
        "created": created,
        "delta": tuple(delta),
    }
