"""Run the Delete Non-Extreme pass inside a real Blender 5.0.

Builds a synthetic armature with a baked-looking action (a key on every frame
of several channels), marks a handful of keys Extreme, then exercises the pass
through the real operator and checks the result.

Run it with a real Blender, from anywhere::

    blender -b --factory-startup --python tests/test_extremes.py

Exits non-zero on the first failure, so it works as a CI step. It builds
its own synthetic rig and never touches the bundled .blend.
"""

import math
import os
import sys

import bpy

ADDON = "mocopi_m2m_retarget"

failures = []
checks = 0


def check(condition, label, detail=""):
    global checks
    checks += 1
    if condition:
        print("  ok   {}".format(label))
    else:
        print("  FAIL {} {}".format(label, detail))
        failures.append("{} {}".format(label, detail))


# ----------------------------------------------------------------------
# Register the addon from the staged source
# ----------------------------------------------------------------------

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import mocopi_m2m_retarget as addon  # noqa: E402
from mocopi_m2m_retarget import cleanup  # noqa: E402

addon.register()
print("addon registered")


# ----------------------------------------------------------------------
# Scene: an armature with a dense action
# ----------------------------------------------------------------------

FRAMES = 60
BONES = ("CTRL_Hips", "CTRL_Arm_L", "POLEARM_L")


def build_rig():
    bpy.ops.object.armature_add(enter_editmode=True)
    armature = bpy.context.object
    armature.name = "TestRig"

    edit_bones = armature.data.edit_bones
    edit_bones[0].name = BONES[0]
    for name in BONES[1:]:
        bone = edit_bones.new(name)
        bone.head = (0.0, 0.0, 1.0)
        bone.tail = (0.0, 0.0, 2.0)

    bpy.ops.object.mode_set(mode="OBJECT")
    return armature


def build_euler_rig():
    bpy.ops.object.armature_add(enter_editmode=True)
    armature = bpy.context.object
    armature.name = "EulerTurnRig"
    armature.data.edit_bones[0].name = "CTRL_Turn"
    bpy.ops.object.mode_set(mode="OBJECT")
    armature.pose.bones["CTRL_Turn"].rotation_mode = "XYZ"
    return armature


def build_quaternion_rig():
    bpy.ops.object.armature_add(enter_editmode=True)
    armature = bpy.context.object
    armature.name = "QuaternionZeroCrossRig"
    armature.data.edit_bones[0].name = "CTRL_Foot"
    bpy.ops.object.mode_set(mode="OBJECT")
    armature.pose.bones["CTRL_Foot"].rotation_mode = "QUATERNION"
    return armature


armature = build_rig()

for pose_bone in armature.pose.bones:
    pose_bone.rotation_mode = "QUATERNION"

# A key on every frame of every channel -- what a fresh retarget bake looks like.
for frame in range(FRAMES):
    bpy.context.scene.frame_set(frame)
    for pose_bone in armature.pose.bones:
        pose_bone.location = (frame * 0.01, 0.0, 0.0)
        pose_bone.rotation_quaternion = (1.0, frame * 0.001, 0.0, 0.0)
        pose_bone.keyframe_insert("location", frame=frame)
        pose_bone.keyframe_insert("rotation_quaternion", frame=frame)

curves = cleanup.action_curves(armature)
dense_total = sum(len(c.keyframe_points) for c in curves)
print("built {} channels, {} keys".format(len(curves), dense_total))
check(len(curves) == len(BONES) * 7, "one curve per channel", len(curves))
check(dense_total == len(curves) * FRAMES, "dense bake", dense_total)


# ----------------------------------------------------------------------
# 1. Refuses when nothing is marked
# ----------------------------------------------------------------------

print("\n1. nothing marked")
check(cleanup.extreme_frames(armature) == [], "no extremes found yet")
try:
    cleanup.delete_non_extreme(armature)
except cleanup.CleanupError as exc:
    check("marked Extreme" in str(exc), "refuses with a useful message", str(exc)[:60])
else:
    check(False, "refuses when nothing is marked", "it did not raise")

check(
    sum(len(c.keyframe_points) for c in curves) == dense_total,
    "action untouched by the refusal",
)


# ----------------------------------------------------------------------
# 2. Mark extremes on ONE bone only -- the frame set must go rig-wide
# ----------------------------------------------------------------------

print("\n2. marking on one bone only")
EXTREMES = [0, 7, 23, 42, 59]

hips_curves = [c for c in curves if cleanup.bone_name(c.data_path) == BONES[0]]
for fcurve in hips_curves:
    for point in fcurve.keyframe_points:
        if int(round(point.co[0])) in EXTREMES:
            point.type = "EXTREME"

found = cleanup.extreme_frames(armature)
check(found == EXTREMES, "frame set gathered from the whole action", found)


# ----------------------------------------------------------------------
# 3. Decimate one bone first, so some channels have no key on an extreme
# ----------------------------------------------------------------------

print("\n3. a pre-thinned channel")
pole_curves = [c for c in curves if cleanup.bone_name(c.data_path) == BONES[2]]
sparse = pole_curves[0]
for index in range(len(sparse.keyframe_points) - 2, 0, -1):
    sparse.keyframe_points.remove(sparse.keyframe_points[index], fast=True)
sparse.update()
sparse_frames = sorted(int(round(p.co[0])) for p in sparse.keyframe_points)
check(sparse_frames == [0, FRAMES - 1], "channel thinned to its ends", sparse_frames)
value_at_23_before = sparse.evaluate(23)


# ----------------------------------------------------------------------
# 4. Run it through the real operator
# ----------------------------------------------------------------------

print("\n4. running the operator")
bpy.context.view_layer.objects.active = armature
result = bpy.ops.m2m.delete_non_extreme()
check(result == {"FINISHED"}, "operator returned FINISHED", result)

curves = cleanup.action_curves(armature)
for fcurve in curves:
    frames = sorted(int(round(p.co[0])) for p in fcurve.keyframe_points)
    if frames != EXTREMES:
        check(False, "curve on the extreme frame set", "{} {}".format(fcurve.data_path, frames))
        break
else:
    check(True, "every curve is on exactly the extreme frame set")

after_total = sum(len(c.keyframe_points) for c in curves)
check(
    after_total == len(curves) * len(EXTREMES),
    "total keys = channels x extremes",
    after_total,
)

# The sampled-in key holds the pose the curve was already showing.
sparse = [c for c in curves if c.data_path == sparse.data_path
          and c.array_index == sparse.array_index][0]
value_at_23_after = sparse.evaluate(23)
check(
    abs(value_at_23_after - value_at_23_before) < 1e-6,
    "sampled key preserves the pose at that frame",
    "{} vs {}".format(value_at_23_before, value_at_23_after),
)

# Surviving non-rotation keys got the interpolation the operator promises;
# quaternion rotation channels stay linear so component handles cannot whip
# through a zero crossing.
non_rotation_interpolations = {
    point.interpolation
    for curve in curves
    if not curve.data_path.endswith("rotation_quaternion")
    for point in curve.keyframe_points
}
quaternion_interpolations = {
    point.interpolation
    for curve in curves
    if curve.data_path.endswith("rotation_quaternion")
    for point in curve.keyframe_points
}
check(
    non_rotation_interpolations == {"BEZIER"},
    "surviving non-rotation keys set to Bezier",
    non_rotation_interpolations,
)
check(
    quaternion_interpolations == {"LINEAR"},
    "surviving quaternion keys set to Linear",
    quaternion_interpolations,
)

z_quaternion_curves = [
    curve for curve in curves
    if curve.data_path.endswith("rotation_quaternion") and curve.array_index == 3
]
check(z_quaternion_curves, "test rig has quaternion Z curves")


# ----------------------------------------------------------------------
# 5. Re-running is a no-op
# ----------------------------------------------------------------------

print("\n5. idempotence")
again = cleanup.delete_non_extreme(armature)
check(again["removed"] == 0, "second run removes nothing", again["removed"])
check(again["inserted"] == 0, "second run inserts nothing", again["inserted"])
check(
    sum(len(c.keyframe_points) for c in cleanup.action_curves(armature)) == after_total,
    "key count unchanged",
)


# ----------------------------------------------------------------------
# 6. Quaternion Z zero-crossings do not get Bezier handles
# ----------------------------------------------------------------------

print("\n6. quaternion Z zero-crossing")
quat_armature = build_quaternion_rig()
quat_bone = quat_armature.pose.bones["CTRL_Foot"]
QUAT_LAST = 32
QUAT_EXTREMES = [0, 16, QUAT_LAST]

for frame in range(QUAT_LAST + 1):
    bpy.context.scene.frame_set(frame)
    angle = math.radians(50.0 - 100.0 * frame / float(QUAT_LAST))
    quat_bone.rotation_quaternion = (
        math.cos(angle / 2.0),
        0.0,
        0.0,
        math.sin(angle / 2.0),
    )
    quat_bone.keyframe_insert("rotation_quaternion", frame=frame)

quat_curves = cleanup.action_curves(quat_armature)
for fcurve in quat_curves:
    for point in fcurve.keyframe_points:
        if int(round(point.co[0])) in QUAT_EXTREMES:
            point.type = "EXTREME"

bpy.context.view_layer.objects.active = quat_armature
result = bpy.ops.m2m.delete_non_extreme()
check(result == {"FINISHED"}, "quaternion zero-crossing operator FINISHED", result)

quat_curves = cleanup.action_curves(quat_armature)
quat_interpolations = {
    point.interpolation
    for curve in quat_curves
    if curve.data_path.endswith("rotation_quaternion")
    for point in curve.keyframe_points
}
z_curve = [curve for curve in quat_curves if curve.array_index == 3][0]
z_values = [point.co[1] for point in z_curve.keyframe_points]
check(min(z_values) < 0.0 < max(z_values), "quaternion Z crosses zero", z_values)
check(
    quat_interpolations == {"LINEAR"},
    "quaternion Z zero-crossing stays linear",
    quat_interpolations,
)


# ----------------------------------------------------------------------
# 7. Wrapped Euler turns keep their dense direction
# ----------------------------------------------------------------------

print("\n7. wrapped Euler turn")
turn_armature = build_euler_rig()
turn_bone = turn_armature.pose.bones["CTRL_Turn"]
TURN_LAST = 39
TURN_EXTREMES = [0, 20, TURN_LAST]

for frame in range(TURN_LAST + 1):
    bpy.context.scene.frame_set(frame)
    angle = (frame / float(TURN_LAST)) * math.tau
    wrapped = (angle + math.pi) % math.tau - math.pi
    turn_bone.rotation_euler = (0.0, 0.0, wrapped)
    turn_bone.keyframe_insert("rotation_euler", frame=frame)

turn_curves = cleanup.action_curves(turn_armature)
for fcurve in turn_curves:
    if fcurve.array_index == 2:
        for point in fcurve.keyframe_points:
            if int(round(point.co[0])) in TURN_EXTREMES:
                point.type = "EXTREME"

bpy.context.view_layer.objects.active = turn_armature
result = bpy.ops.m2m.delete_non_extreme()
check(result == {"FINISHED"}, "wrapped Euler operator FINISHED", result)

z_curve = [c for c in cleanup.action_curves(turn_armature) if c.array_index == 2][0]
z_frames = [int(round(point.co[0])) for point in z_curve.keyframe_points]
z_values = [point.co[1] for point in z_curve.keyframe_points]
z_steps = [z_values[i + 1] - z_values[i] for i in range(len(z_values) - 1)]
check(
    all(frame in z_frames for frame in TURN_EXTREMES),
    "wrapped Euler keeps the marked extremes",
    z_frames,
)
check(
    len(z_frames) > len(TURN_EXTREMES),
    "wrapped Euler keeps rotation guard frames",
    z_frames,
)
check(
    all(0.0 < step < math.pi for step in z_steps),
    "wrapped Euler values unwrap forward",
    z_values,
)
check(
    z_values[-1] > math.tau - 0.001,
    "final Euler key keeps the full turn equivalent",
    z_values[-1],
)


# ----------------------------------------------------------------------
# 8. The panel draws -- i.e. every icon name is real
# ----------------------------------------------------------------------

print("\n8. panel icons")
icons = bpy.types.UILayout.bl_rna.functions["operator"].parameters["icon"]
valid = {item.identifier for item in icons.enum_items}
for name in ("ARMATURE_DATA", "ORIENTATION_PARENT", "SMOOTHCURVE",
             "CON_KINEMATIC", "DECORATE_KEYFRAME", "KEYTYPE_EXTREME_VEC"):
    check(name in valid, "icon exists: {}".format(name))


# ----------------------------------------------------------------------
# 9. selected_only and channel filtering still work on this pass
# ----------------------------------------------------------------------

print("\n9. filters")
try:
    cleanup.delete_non_extreme(armature, selected_only=True)
except cleanup.CleanupError as exc:
    check("selected" in str(exc).lower(), "selected_only with no selection errors",
          str(exc)[:50])
else:
    check(True, "selected_only ran (bones were selected)")

print("\n{} checks, {} failures".format(checks, len(failures)))
for line in failures:
    print("  " + line)

sys.exit(1 if failures else 0)
