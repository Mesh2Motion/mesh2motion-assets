"""Animation retargeting engine.

Ported from the Rokoko Studio Live for Blender addon, LGPL-3.0,
(c) Rokoko Electronics ApS -- specifically ``core/utils.py`` and
``operators/retargeting.py`` from
https://github.com/Rokoko/rokoko-studio-live-blender

What was kept: the retarget-and-bake machinery. A helper bone is created in a
throwaway copy of the source armature at each target bone's rest position and
parented to the matching source bone, the target bones are constrained to
those helpers, and the result is baked down and cleaned up.

What was dropped: bone auto-detection, the internal naming lists, custom
naming schemes, scene properties and all UI. None of it is needed here --
the source is always a Mocopi BVH skeleton and the target is always the
Mesh2Motion rig, so the bone pairs are supplied explicitly by the caller.

What was added: case-insensitive bone lookup, a per-pair ``copy_location``
override (IK control bones need position, not just rotation), and a VIEW_3D
context override so this survives being called from a file browser operator.
"""

import copy
import math
import time
from collections import namedtuple
from contextlib import contextmanager

import bpy
from mathutils import Matrix, Vector

RETARGET_ID = "_M2M_RETARGET"

# ----------------------------------------------------------------------
# Action / F-curve compatibility
#
# Blender 4.4 introduced slotted actions and 5.0 removed ``Action.fcurves``
# outright, so curves now live on a channelbag belonging to a slot. Everything
# below goes through these two helpers instead of touching ``action.fcurves``.
# ----------------------------------------------------------------------

def _anim_utils():
    try:
        from bpy_extras import anim_utils
    except ImportError:
        return None
    return anim_utils if hasattr(anim_utils, "action_get_channelbag_for_slot") else None


def current_slot(obj):
    """The action slot assigned to obj, or None on pre-4.4 Blender."""
    anim_data = obj.animation_data
    if anim_data is None:
        return None
    return getattr(anim_data, "action_slot", None)


def fcurves_for(action, slot=None):
    """The F-curve collection of an action, slotted or legacy.

    Supports .new(), .find(), .remove() and iteration on every version.
    """
    if action is None:
        return None

    if slot is not None:
        anim_utils = _anim_utils()
        if anim_utils is not None:
            channelbag = anim_utils.action_get_channelbag_for_slot(action, slot)
            if channelbag is not None:
                return channelbag.fcurves

    # Pre-4.4, or a legacy action.
    return getattr(action, "fcurves", None)

# copy_location: True forces a COPY_LOCATION constraint on the target bone,
# False forbids one, None leaves it to the root-bone detection below.
BonePair = namedtuple("BonePair", "source target copy_location")
BonePair.__new__.__defaults__ = (None,)


class RetargetError(Exception):
    """Raised for anything the caller should surface to the user."""


# ----------------------------------------------------------------------
# Small helpers
# ----------------------------------------------------------------------

def set_active(obj):
    obj.select_set(True)
    obj.hide_set(False)
    bpy.context.view_layer.objects.active = obj


def vec_roll_to_mat3(vec, roll):
    target = Vector((0, 0.1, 0))
    nor = vec.normalized()
    axis = target.cross(nor)

    if axis.dot(axis) > 0.0000000001:
        axis.normalize()
        theta = target.angle(nor)
        b_matrix = Matrix.Rotation(theta, 3, axis)
    else:
        updown = 1 if target.dot(nor) > 0 else -1
        b_matrix = Matrix.Scale(updown, 3)
        b_matrix[2][2] = 1.0

    r_matrix = Matrix.Rotation(roll, 3, nor)
    return r_matrix @ b_matrix


def mat3_to_vec_roll(mat):
    vecmat = vec_roll_to_mat3(mat.col[1], 0)
    rollmat = vecmat.inverted() @ mat
    return math.atan2(rollmat[0][2], rollmat[2][2])


def find_pose_bone(armature, name):
    """Look up a pose bone, falling back to a case-insensitive match."""
    bone = armature.pose.bones.get(name)
    if bone is not None:
        return bone

    lowered = name.lower()
    for candidate in armature.pose.bones:
        if candidate.name.lower() == lowered:
            return candidate

    return None


def set_bone_selected(armature, bone_name, selected=True):
    """Select a bone for baking, across Blender versions.

    Blender 5.0 removed ``Bone.select`` (and ``select_head`` / ``select_tail``)
    and put a ``select`` property on the pose bone instead. Everything before
    that keeps it on the bone. ``nla.bake(only_selected=True)`` reads whichever
    one the version uses, so this has to set the right one.
    """
    pose_bone = armature.pose.bones.get(bone_name)
    if pose_bone is not None and hasattr(pose_bone, "select"):
        pose_bone.select = selected
        return True

    bone = armature.data.bones.get(bone_name)
    if bone is not None and hasattr(bone, "select"):
        bone.select = selected
        return True

    return False


@contextmanager
def view3d_override():
    """Run inside a VIEW_3D context.

    ``duplicate_move`` and ``nla.bake`` both want a real 3D viewport. When the
    operator is launched from the file browser the active area is not one, so
    borrow the first VIEW_3D we can find.
    """
    if getattr(bpy.context, "area", None) is not None and bpy.context.area.type == "VIEW_3D":
        yield
        return

    for window in bpy.context.window_manager.windows:
        for area in window.screen.areas:
            if area.type != "VIEW_3D":
                continue
            region = next((r for r in area.regions if r.type == "WINDOW"), None)
            if region is None:
                continue
            with bpy.context.temp_override(window=window, area=area, region=region):
                yield
            return

    # No viewport open. Try anyway rather than refusing outright.
    yield


# ----------------------------------------------------------------------
# Pair resolution
# ----------------------------------------------------------------------

def resolve_pairs(armature_source, armature_target, pairs):
    """Match the requested pairs against bones that actually exist.

    Returns (resolved, missing_source, missing_target). Names in the resolved
    list are the real, correctly-cased bone names.
    """
    resolved = []
    missing_source = []
    missing_target = []

    for pair in pairs:
        bone_source = find_pose_bone(armature_source, pair.source)
        bone_target = find_pose_bone(armature_target, pair.target)

        if bone_source is None:
            missing_source.append(pair.source)
            continue
        if bone_target is None:
            missing_target.append(pair.target)
            continue

        resolved.append(BonePair(bone_source.name, bone_target.name, pair.copy_location))

    return resolved, missing_source, missing_target


def find_root_bones(armature_target, pairs):
    """Find the mapped bones that sit highest in the target hierarchy.

    Walk down from every parentless bone until a mapped bone is hit; those are
    the ones whose world position has to be copied, not just their rotation.
    """
    target_names = {pair.target for pair in pairs}

    pending = [bone for bone in armature_target.pose.bones if not bone.parent]
    roots = []

    while pending:
        bone = pending.pop(0)
        if bone.name in target_names:
            roots.append(bone.name)
        else:
            pending.extend(bone.children)

    return roots


def find_duplicate_targets(pairs):
    seen = {}
    for pair in pairs:
        seen[pair.target] = seen.get(pair.target, 0) + 1
    return [name for name, count in seen.items() if count > 1]


# ----------------------------------------------------------------------
# Pose / scale helpers
# ----------------------------------------------------------------------

def get_and_reset_pose_rotations(armature):
    bpy.ops.object.select_all(action="DESELECT")
    set_active(armature)
    bpy.ops.object.mode_set(mode="POSE")

    pose_rotations = {}
    for bone in armature.pose.bones:
        if bone.rotation_mode == "QUATERNION":
            pose_rotations[bone.name] = copy.deepcopy(bone.rotation_quaternion)
            bone.rotation_quaternion = (1, 0, 0, 0)
        else:
            pose_rotations[bone.name] = copy.deepcopy(bone.rotation_euler)
            bone.rotation_euler = (0, 0, 0)

    bpy.ops.object.mode_set(mode="OBJECT")
    return pose_rotations


def clean_animation(armature_source):
    """Drop object-level transform curves so auto scaling is not fighting them."""
    deletable = ("location", "rotation_euler", "rotation_quaternion", "scale")

    curves = fcurves_for(
        armature_source.animation_data.action, current_slot(armature_source)
    )
    if curves is None:
        return

    for fcurve in list(curves):
        if fcurve.data_path in deletable:
            curves.remove(fcurve)


def scale_armature(armature_source, armature_target, pairs, root_bones):
    """Scale the source armature so its height matches the target's."""
    source_min = source_min_root = None
    target_min = target_min_root = None

    for pair in pairs:
        bone_source = armature_source.pose.bones.get(pair.source)
        bone_target = armature_target.pose.bones.get(pair.target)
        if bone_source is None or bone_target is None:
            continue

        source_z = (armature_source.matrix_world @ bone_source.head)[2]
        target_z = (armature_target.matrix_world @ bone_target.head)[2]

        if pair.target in root_bones:
            if source_min_root is None or source_min_root > source_z:
                source_min_root = source_z
            if target_min_root is None or target_min_root > target_z:
                target_min_root = target_z

        if source_min is None or source_min > source_z:
            source_min = source_z
        if target_min is None or target_min > target_z:
            target_min = target_z

    if source_min_root is None or target_min_root is None:
        return

    source_height = source_min_root - source_min
    target_height = target_min_root - target_min

    if not source_height or not target_height:
        print("[mocopi-m2m] No scaling needed")
        return

    armature_source.scale *= target_height / source_height


def read_anim_start_end(armature):
    action = armature.animation_data.action
    curves = fcurves_for(action, current_slot(armature))

    frame_start = frame_end = None

    if curves is not None:
        for fcurve in curves:
            for key in fcurve.keyframe_points:
                frame = key.co.x
                if frame_start is None or frame < frame_start:
                    frame_start = frame
                if frame_end is None or frame > frame_end:
                    frame_end = frame

    if frame_start is None:
        # Fall back to whatever Blender reports for the action as a whole.
        frame_range = getattr(action, "frame_range", None)
        if frame_range is not None:
            frame_start, frame_end = frame_range[0], frame_range[1]

    return frame_start, frame_end


def copy_rest_pose(armature_source):
    """Duplicate the source armature with its transforms applied.

    The copy carries the same animation via COPY_TRANSFORMS constraints, which
    lets the helper bones be built in a clean rest space.
    """
    bpy.context.scene.tool_settings.use_keyframe_insert_auto = False

    bpy.ops.object.mode_set(mode="OBJECT")
    bpy.ops.object.select_all(action="DESELECT")
    set_active(armature_source)
    bpy.ops.object.mode_set(mode="OBJECT")

    bpy.ops.object.duplicate_move(
        OBJECT_OT_duplicate={"linked": False, "mode": "TRANSLATION"},
        TRANSFORM_OT_translate={
            "value": (0, 0, 0),
            "constraint_axis": (False, True, False),
            "mirror": False,
            "snap": False,
            "remove_on_cancel": False,
            "release_confirm": False,
        },
    )

    armature_copy = bpy.context.object
    armature_copy.name = armature_source.name + "_copy"

    bpy.ops.object.select_all(action="DESELECT")
    set_active(armature_copy)
    bpy.ops.object.mode_set(mode="OBJECT")
    bpy.ops.object.mode_set(mode="POSE")

    # Unlink the action while applying the pose, otherwise Blender warns.
    action_tmp = armature_copy.animation_data.action
    armature_copy.animation_data.action = None
    bpy.ops.pose.armature_apply()
    armature_copy.animation_data.action = action_tmp

    for bone in armature_copy.pose.bones:
        constraint = bone.constraints.new("COPY_TRANSFORMS")
        constraint.name = bone.name
        constraint.target = armature_source
        constraint.subtarget = bone.name

    bpy.ops.object.mode_set(mode="OBJECT")
    return armature_copy


# ----------------------------------------------------------------------
# Bake
# ----------------------------------------------------------------------

def should_drop_curve(fcurve, location_bones):
    """Curves that never make it into the final action.

    Scale is never retargeted, and location is only kept for bones that are
    actually driven by position -- everything else inherits it through the
    hierarchy and would fight the rig if it had its own location keys.
    """
    if fcurve.data_path.endswith("scale"):
        return True

    if fcurve.data_path.endswith("location"):
        parts = fcurve.data_path.split('"')
        if len(parts) != 3:
            return True
        if parts[1] not in location_bones:
            return True

    return False


def bake_animation(armature_source, armature_target, location_bones):
    """Bake the constrained target armature down to keyframes.

    Baked in chunks: several short bakes are far faster than one long one.
    The first chunk's action becomes the final one and the rest are appended
    onto its curves, which avoids having to build an action (and, on Blender
    4.4+, a slot and channelbag) from scratch.
    """
    frame_split = 25

    frame_start, frame_end = read_anim_start_end(armature_source)
    if frame_start is None or frame_end is None:
        raise RetargetError("Source animation has no keyframes")
    frame_start, frame_end = int(frame_start), int(frame_end)

    set_active(armature_target)

    chunks = []  # (action, slot)
    current_step = 0
    steps = int((frame_end - frame_start) / frame_split) + 1

    window_manager = bpy.context.window_manager
    window_manager.progress_begin(current_step, steps)
    start_time = time.time()

    try:
        bpy.ops.object.mode_set(mode="POSE")

        for frame in range(frame_start, frame_end + 2, frame_split):
            start = frame
            end = min(frame + frame_split - 1, frame_end)
            if start > end:
                continue

            bpy.ops.nla.bake(
                frame_start=start,
                frame_end=end,
                visual_keying=True,
                only_selected=True,
                use_current_action=False,
                bake_types={"POSE"},
            )

            action = armature_target.animation_data.action
            if action is None:
                continue

            action.name = "M2M_RETARGETING_" + str(frame)
            chunks.append((action, current_slot(armature_target)))

            current_step += 1
            if steps != current_step:
                window_manager.progress_update(current_step)

        bpy.ops.object.mode_set(mode="OBJECT")

        if not chunks:
            return None

        action_final, slot_final = chunks[0]
        curves_final = fcurves_for(action_final, slot_final)

        if curves_final is None:
            raise RetargetError(
                "Could not read the baked action's F-curves. "
                "This Blender version may store animation differently than expected."
            )

        # Throw away the curves that should not survive.
        for fcurve in list(curves_final):
            if should_drop_curve(fcurve, location_bones):
                curves_final.remove(fcurve)

        # Append every later chunk onto the first one's curves.
        remaining = chunks[1:]
        for fcurve in curves_final:
            for keyframe in fcurve.keyframe_points:
                keyframe.interpolation = "LINEAR"

            sources = []
            extra_keys = 0
            for action, slot in remaining:
                curves = fcurves_for(action, slot)
                if curves is None:
                    continue
                source_curve = curves.find(
                    data_path=fcurve.data_path, index=fcurve.array_index
                )
                if source_curve is None:
                    continue
                sources.append(source_curve)
                extra_keys += len(source_curve.keyframe_points)

            if not extra_keys:
                continue

            index = len(fcurve.keyframe_points)
            fcurve.keyframe_points.add(extra_keys)

            for source_curve in sources:
                for keyframe in source_curve.keyframe_points:
                    target_keyframe = fcurve.keyframe_points[index]
                    target_keyframe.co.x = keyframe.co.x
                    target_keyframe.co.y = keyframe.co.y
                    target_keyframe.interpolation = "LINEAR"
                    index += 1

            fcurve.update()

        # Drop keyframes that sit between two identical neighbours.
        for fcurve in curves_final:
            if len(fcurve.keyframe_points) <= 2:
                continue

            kp_pre_pre = fcurve.keyframe_points[0]
            kp_pre = fcurve.keyframe_points[1]
            to_delete = []

            for keyframe in fcurve.keyframe_points[2:]:
                if round(kp_pre_pre.co.y, 5) == round(kp_pre.co.y, 5) == round(keyframe.co.y, 5):
                    to_delete.append(kp_pre)
                kp_pre_pre = kp_pre
                kp_pre = keyframe

            for keyframe in reversed(to_delete):
                fcurve.keyframe_points.remove(keyframe)

        # Put the combined action back on the target before the rest are freed.
        anim_data = armature_target.animation_data
        anim_data.action = action_final

        if hasattr(anim_data, "action_slot"):
            if slot_final is not None:
                try:
                    anim_data.action_slot = slot_final
                except (TypeError, RuntimeError):
                    pass
            if anim_data.action_slot is None and getattr(anim_data, "action_suitable_slots", None):
                anim_data.action_slot = anim_data.action_suitable_slots[0]

        action_final.use_fake_user = True

        for action, _slot in remaining:
            bpy.data.actions.remove(action)

        print("[mocopi-m2m] Retargeting time:", round(time.time() - start_time, 2), "seconds")

        return action_final

    finally:
        window_manager.progress_end()


# ----------------------------------------------------------------------
# Main entry point
# ----------------------------------------------------------------------

def retarget(armature_source, armature_target, pairs, auto_scale=True, use_pose="REST"):
    """Retarget the source armature's animation onto the target armature.

    ``pairs`` is a sequence of BonePair. Returns a summary dict with the
    resolved pair count, the bones that got location copied, and anything in
    the mapping that did not match a real bone.
    """
    if not armature_source.animation_data or not armature_source.animation_data.action:
        raise RetargetError(
            "No animation on '{}'. Import the BVH first.".format(armature_source.name)
        )

    if armature_source is armature_target:
        raise RetargetError("Source and target armature are the same")

    resolved, missing_source, missing_target = resolve_pairs(
        armature_source, armature_target, pairs
    )

    if not resolved:
        raise RetargetError(
            "None of the mapped bones exist on both armatures. "
            "Check the bone map against the rigs."
        )

    duplicates = find_duplicate_targets(resolved)
    if duplicates:
        raise RetargetError(
            "Each target bone may only be used once. Duplicates: " + ", ".join(duplicates)
        )

    root_bones = find_root_bones(armature_target, resolved)
    if not root_bones:
        raise RetargetError("No root bone found in the target armature")

    # Bones whose world position is copied, not just their rotation. IK control
    # bones must be in here or the rig never moves.
    location_bones = set(root_bones)
    for pair in resolved:
        if pair.copy_location is True:
            location_bones.add(pair.target)
        elif pair.copy_location is False:
            location_bones.discard(pair.target)

    source_action_name = armature_source.animation_data.action.name

    with view3d_override():
        # Both armatures into object mode and posed.
        set_active(armature_target)
        bpy.ops.object.mode_set(mode="OBJECT")
        set_active(armature_source)
        bpy.ops.object.mode_set(mode="OBJECT")

        armature_source.data.pose_position = "POSE"
        armature_target.data.pose_position = "POSE"

        if use_pose == "REST":
            get_and_reset_pose_rotations(armature_source)
            get_and_reset_pose_rotations(armature_target)

        source_scale = None
        if auto_scale:
            clean_animation(armature_source)
            source_scale = copy.deepcopy(armature_source.scale)
            scale_armature(armature_source, armature_target, resolved, root_bones)

        # Work on a copy of the source so transforms can be applied freely.
        armature_source_original = armature_source
        armature_source = copy_rest_pose(armature_source)

        # Stash the target's transforms, then apply them.
        rotation_mode = armature_target.rotation_mode
        armature_target.rotation_mode = "QUATERNION"
        rotation = copy.deepcopy(armature_target.rotation_quaternion)
        location = copy.deepcopy(armature_target.location)

        bpy.ops.object.select_all(action="DESELECT")
        set_active(armature_target)
        bpy.ops.object.transform_apply(location=True, rotation=True, scale=True)

        # Record every target bone's rest position in the source's space.
        bpy.ops.object.mode_set(mode="EDIT")
        bone_transforms = {}
        source_matrix_inv = armature_source.matrix_world.inverted()
        for bone in bpy.context.object.data.edit_bones:
            bone.select = False
            bone_transforms[bone.name] = (
                source_matrix_inv @ bone.head.copy(),
                source_matrix_inv @ bone.tail.copy(),
                mat3_to_vec_roll(source_matrix_inv.to_3x3() @ bone.matrix.to_3x3()),
            )
        bpy.ops.object.mode_set(mode="OBJECT")

        # Recreate each target bone inside the source armature, parented to
        # its source bone, so it inherits the capture's motion.
        bpy.ops.object.select_all(action="DESELECT")
        set_active(armature_source)
        bpy.ops.object.mode_set(mode="EDIT")

        for pair in resolved:
            bone_source = armature_source.data.edit_bones.get(pair.source)
            bone_new = armature_source.data.edit_bones.new(pair.target + RETARGET_ID)
            bone_new.head, bone_new.tail, bone_new.roll = bone_transforms[pair.target]
            bone_new.parent = bone_source

        bpy.ops.object.mode_set(mode="OBJECT")
        bpy.ops.object.select_all(action="DESELECT")

        # Constrain the target bones to those helpers and select them for baking.
        selected_count = 0
        for pair in resolved:
            bone_target = armature_target.pose.bones.get(pair.target)

            constraint = bone_target.constraints.new("COPY_ROTATION")
            constraint.name += RETARGET_ID
            constraint.target = armature_source
            constraint.subtarget = pair.target + RETARGET_ID

            if pair.target in location_bones:
                constraint = bone_target.constraints.new("COPY_LOCATION")
                constraint.name += RETARGET_ID
                constraint.target = armature_source
                constraint.subtarget = pair.source

            if set_bone_selected(armature_target, pair.target, True):
                selected_count += 1

        # nla.bake only touches selected bones, so nothing selected means a
        # silently empty result rather than an error.
        if not selected_count:
            raise RetargetError(
                "Could not select any target bones for baking. "
                "This Blender version may expose bone selection differently."
            )

        action_final = bake_animation(armature_source, armature_target, location_bones)

        # Throw away the helper armature.
        bpy.ops.object.select_all(action="DESELECT")
        set_active(armature_source)
        if armature_source.animation_data and armature_source.animation_data.action:
            bpy.data.actions.remove(armature_source.animation_data.action)
        bpy.ops.object.delete()

        armature_source = armature_source_original

        if action_final is not None:
            action_final.name = source_action_name + " Retarget"

        # Strip the temporary constraints back off the target.
        for bone in armature_target.pose.bones:
            for constraint in list(bone.constraints):
                if RETARGET_ID in constraint.name:
                    bone.constraints.remove(constraint)

        bpy.ops.object.select_all(action="DESELECT")
        set_active(armature_target)

        # Put the target's transforms back the way they were.
        armature_target.rotation_quaternion = rotation
        armature_target.location = location
        armature_target.rotation_quaternion.w = -armature_target.rotation_quaternion.w
        bpy.ops.object.transform_apply(location=False, rotation=True, scale=False)
        armature_target.rotation_quaternion = rotation
        armature_target.rotation_mode = rotation_mode

        if source_scale is not None:
            armature_source.scale = source_scale

        bpy.ops.object.select_all(action="DESELECT")

    return {
        "pairs": len(resolved),
        "location_bones": sorted(location_bones),
        "missing_source": missing_source,
        "missing_target": missing_target,
        "action": action_final.name if action_final is not None else None,
    }
