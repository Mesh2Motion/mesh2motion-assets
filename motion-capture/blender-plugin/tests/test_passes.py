"""Run smooth / simplify_poles / decimate inside a real Blender 5.0.

These three had only ever been tested against a mock of the animation API.
This exercises them through the real operators, at the 0.4.1 defaults, on a
noisy synthetic capture -- and checks the root-motion pass while a rig with a
real DRV_root hierarchy is on hand.

Run it with a real Blender, from anywhere::

    blender -b --factory-startup --python tests/test_passes.py

Exits non-zero on the first failure, so it works as a CI step. It builds
its own synthetic rig and never touches the bundled .blend.
"""

import math
import random
import os
import sys

import bpy

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


sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import mocopi_m2m_retarget as addon  # noqa: E402
from mocopi_m2m_retarget import cleanup, root_motion  # noqa: E402

addon.register()

FRAMES = 120
random.seed(7)

# The nine controls that hang off DRV_root on the real rig, plus the DRV chain
# the export skeleton copies from.
CONTROLS = (
    "CTRL_Hips", "CTRL_Arm_L", "CTRL_Arm_R", "CTRL_ILLEG_L", "CTRL_ILLEG_R",
    "POLEARM_L", "POLEARM_R", "POLE_Leg_L", "POLE_Leg_R",
)


def build_rig():
    bpy.ops.object.armature_add(enter_editmode=True)
    armature = bpy.context.object
    armature.name = "TestRig"

    edit_bones = armature.data.edit_bones
    root = edit_bones[0]
    root.name = "DRV_root"
    root.head = (0.0, 0.0, 0.0)
    root.tail = (0.0, 0.2, 0.0)

    for index, name in enumerate(CONTROLS):
        bone = edit_bones.new(name)
        bone.head = (index * 0.1, 0.0, 1.0)
        bone.tail = (index * 0.1, 0.2, 1.0)
        bone.parent = root

    bpy.ops.object.mode_set(mode="OBJECT")
    return armature


armature = build_rig()
for pose_bone in armature.pose.bones:
    pose_bone.rotation_mode = "QUATERNION"

# A walk-ish capture: steady forward travel, a per-step weave, sensor noise on
# top, and a deliberate quaternion sign flip mid-clip.
scene = bpy.context.scene
scene.frame_start = 0
scene.frame_end = FRAMES - 1

for frame in range(FRAMES):
    scene.frame_set(frame)
    phase = frame / 30.0 * math.tau
    for index, pose_bone in enumerate(armature.pose.bones):
        noise = lambda s: random.uniform(-s, s)
        if pose_bone.name == "DRV_root":
            continue
        pose_bone.location = (
            math.sin(phase) * 0.03 + noise(0.0015),
            frame * 0.02 + noise(0.0015),
            1.0 + noise(0.0015),
        )
        angle = math.sin(phase + index) * 0.4 + noise(0.01)
        sign = -1.0 if frame >= FRAMES // 2 else 1.0
        pose_bone.rotation_quaternion = (
            sign * math.cos(angle / 2.0),
            sign * math.sin(angle / 2.0),
            0.0,
            0.0,
        )
        pose_bone.scale = (1.0, 1.0, 1.0)
        pose_bone.keyframe_insert("location", frame=frame)
        pose_bone.keyframe_insert("rotation_quaternion", frame=frame)
        pose_bone.keyframe_insert("scale", frame=frame)

bpy.context.view_layer.objects.active = armature
curves = cleanup.action_curves(armature)
dense = sum(len(c.keyframe_points) for c in curves)
print("capture: {} channels, {} keys".format(len(curves), dense))


def world_path():
    """CTRL_Hips' world Y per frame -- what must not change."""
    out = []
    for frame in range(FRAMES):
        scene.frame_set(frame)
        out.append(
            (armature.matrix_world @ armature.pose.bones["CTRL_Hips"].matrix)
            .translation.copy()
        )
    return out


# ----------------------------------------------------------------------
# 1. Scale cleanup: scale curves disappear, other curves survive
# ----------------------------------------------------------------------

print("\n1. delete scale keyframes")
curves = cleanup.action_curves(armature)
scale_keys = sum(
    len(c.keyframe_points)
    for c in curves
    if cleanup.channel_kind(c.data_path) == "SCALE"
)
non_scale_channels = sum(
    1 for c in curves if cleanup.channel_kind(c.data_path) != "SCALE"
)
check(scale_keys > 0, "test rig has scale keyframes", scale_keys)

result = bpy.ops.m2m.delete_scale_keyframes()
check(result == {"FINISHED"}, "operator FINISHED", result)
curves = cleanup.action_curves(armature)
check(
    not any(cleanup.channel_kind(c.data_path) == "SCALE" for c in curves),
    "all scale curves removed",
)
check(len(curves) == non_scale_channels, "non-scale curves preserved", len(curves))


# ----------------------------------------------------------------------
# 2. Smooth: values change, key count does not
# ----------------------------------------------------------------------

print("\n2. smooth at the default 1 frame")
before_count = sum(len(c.keyframe_points) for c in curves)
result = bpy.ops.m2m.smooth_keyframes()
check(result == {"FINISHED"}, "operator FINISHED", result)
curves = cleanup.action_curves(armature)
check(
    sum(len(c.keyframe_points) for c in curves) == before_count,
    "key count unchanged by smoothing",
)

# Quaternions still unit length despite the mid-clip sign flip.
worst = 0.0
for frame in range(FRAMES):
    scene.frame_set(frame)
    for pose_bone in armature.pose.bones:
        if pose_bone.name == "DRV_root":
            continue
        worst = max(worst, abs(pose_bone.rotation_quaternion.magnitude - 1.0))
check(worst < 1e-4, "quaternions renormalised", worst)

# The sign flip was not smoothed across: no bone swings through a full turn.
biggest_step = 0.0
previous = None
for frame in range(FRAMES):
    scene.frame_set(frame)
    current = armature.pose.bones["CTRL_Arm_L"].rotation_quaternion.copy()
    if previous is not None:
        dot = abs(sum(a * b for a, b in zip(current, previous)))
        biggest_step = max(biggest_step, math.degrees(2.0 * math.acos(min(1.0, dot))))
    previous = current
check(biggest_step < 25.0, "no full-turn swing at the sign flip", biggest_step)


# ----------------------------------------------------------------------
# 3. Root motion: travel moves to DRV_root, world pose unchanged
# ----------------------------------------------------------------------

print("\n3. extract root motion")
before_path = world_path()
result = bpy.ops.m2m.extract_root_motion()
check(result == {"FINISHED"}, "operator FINISHED", result)

after_path = world_path()
drift = max((a - b).length for a, b in zip(before_path, after_path))
check(drift < 2e-4, "visible world pose unchanged", drift)

def root_world_y(frame):
    scene.frame_set(frame)
    return (
        armature.matrix_world @ armature.pose.bones["DRV_root"].matrix
    ).translation.y


root_travel = root_world_y(FRAMES - 1) - root_world_y(0)
hips_local = armature.pose.bones["CTRL_Hips"]
check(root_travel > 1.5, "root carries the forward travel", root_travel)


# ----------------------------------------------------------------------
# 4. Simplify poles at the new 0.4.1 defaults (6 frames / 10mm)
# ----------------------------------------------------------------------

print("\n4. simplify poles at 6 frames / 10mm")
pole_before = sum(
    len(c.keyframe_points)
    for c in cleanup.action_curves(armature)
    if cleanup.bone_name(c.data_path) in cleanup.POLE_BONES
)
non_pole_before = {
    (c.data_path, c.array_index): len(c.keyframe_points)
    for c in cleanup.action_curves(armature)
    if cleanup.bone_name(c.data_path) not in cleanup.POLE_BONES
}

result = bpy.ops.m2m.simplify_poles()
check(result == {"FINISHED"}, "operator FINISHED", result)

pole_curves = [
    c for c in cleanup.action_curves(armature)
    if cleanup.bone_name(c.data_path) in cleanup.POLE_BONES
]
pole_after = sum(len(c.keyframe_points) for c in pole_curves)
ratio = 1.0 - pole_after / float(pole_before)
print("     poles {} -> {} keys ({:.1f}% removed)".format(
    pole_before, pole_after, ratio * 100.0))
check(ratio > 0.95, "pole reduction over 95%", "{:.1f}%".format(ratio * 100.0))

rotation_counts = {
    len(c.keyframe_points) for c in pole_curves
    if cleanup.channel_kind(c.data_path) == "ROTATION"
}
check(rotation_counts == {1}, "pole rotation down to one key", rotation_counts)

non_pole_after = {
    (c.data_path, c.array_index): len(c.keyframe_points)
    for c in cleanup.action_curves(armature)
    if cleanup.bone_name(c.data_path) not in cleanup.POLE_BONES
}
check(non_pole_before == non_pole_after, "nothing else touched")


# ----------------------------------------------------------------------
# 5. Decimate at the defaults, and check the error bound holds
# ----------------------------------------------------------------------

print("\n5. decimate at 0.5 deg / 1mm")
sampled = {}
for fcurve in cleanup.action_curves(armature):
    key = (fcurve.data_path, fcurve.array_index)
    sampled[key] = [fcurve.evaluate(f) for f in range(FRAMES)]

before_count = sum(len(c.keyframe_points) for c in cleanup.action_curves(armature))
result = bpy.ops.m2m.decimate_keyframes()
check(result == {"FINISHED"}, "operator FINISHED", result)

after_count = sum(len(c.keyframe_points) for c in cleanup.action_curves(armature))
print("     rig {} -> {} keys ({:.1f}% removed)".format(
    before_count, after_count, (1 - after_count / float(before_count)) * 100.0))
check(after_count < before_count, "keys were removed", after_count)

worst = {"LOCATION": 0.0, "ROTATION": 0.0}
for fcurve in cleanup.action_curves(armature):
    key = (fcurve.data_path, fcurve.array_index)
    if key not in sampled:
        continue
    kind = cleanup.channel_kind(fcurve.data_path)
    if kind not in worst:
        continue
    for frame in range(FRAMES):
        worst[kind] = max(worst[kind], abs(fcurve.evaluate(frame) - sampled[key][frame]))

# These two assert the error bound decimate advertises. It holds only because
# set_interpolation() aims each survivor's handles along the chord through its
# neighbours. With auto-clamped handles -- what shipped before 0.5.1 -- a
# channel decimated down to two keys came back 229x the tolerance off: Blender
# flattens the handles of a key that has only one neighbour, so the segment
# became an S-curve instead of the line RDP measured against. See
# cleanup.chord_handles.
#
# The ceiling here is looser than the 1mm / 0.5 deg tolerance on purpose --
# this capture is smoothed and pole-simplified first, so the baseline being
# compared against is not the raw tolerance.
print("     worst deviation: {:.4f}m position, {:.4f} rad quat component".format(
    worst["LOCATION"], worst["ROTATION"]))
check(worst["LOCATION"] < 0.01, "position stayed within 10mm", worst["LOCATION"])
check(worst["ROTATION"] < 0.02, "rotation stayed within ~2 deg", worst["ROTATION"])


print("\n{} checks, {} failures".format(checks, len(failures)))
for line in failures:
    print("  " + line)

sys.exit(1 if failures else 0)
