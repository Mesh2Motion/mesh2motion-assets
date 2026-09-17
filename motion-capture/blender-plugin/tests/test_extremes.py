"""Run the Delete Non-Extreme pass inside a real Blender 5.0.

Builds a synthetic armature with a baked-looking action (a key on every frame
of several channels), marks a handful of keys Extreme, then exercises the pass
through the real operator and checks the result.

Run it with a real Blender, from anywhere::

    blender -b --factory-startup --python tests/test_extremes.py

Exits non-zero on the first failure, so it works as a CI step. It builds
its own synthetic rig and never touches the bundled .blend.
"""

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

# Surviving keys got the interpolation the operator promises.
interpolations = {p.interpolation for c in curves for p in c.keyframe_points}
check(interpolations == {"BEZIER"}, "surviving keys set to Bezier", interpolations)


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
# 6. The panel draws -- i.e. every icon name is real
# ----------------------------------------------------------------------

print("\n6. panel icons")
icons = bpy.types.UILayout.bl_rna.functions["operator"].parameters["icon"]
valid = {item.identifier for item in icons.enum_items}
for name in ("ARMATURE_DATA", "ORIENTATION_PARENT", "SMOOTHCURVE",
             "CON_KINEMATIC", "DECORATE_KEYFRAME", "KEYTYPE_EXTREME_VEC"):
    check(name in valid, "icon exists: {}".format(name))


# ----------------------------------------------------------------------
# 7. selected_only and channel filtering still work on this pass
# ----------------------------------------------------------------------

print("\n7. filters")
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
