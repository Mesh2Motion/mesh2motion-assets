"""Run the character-offset pass inside a real Blender 5.0.

Builds a rig whose controls have deliberately non-world rest orientations, so
the world-space-to-bone-space conversion is actually exercised rather than
accidentally passing on an identity basis.

Run it with a real Blender, from anywhere::

    blender -b --factory-startup --python tests/test_offset.py

Exits non-zero on the first failure, so it works as a CI step. It builds its
own synthetic rig and never touches the bundled .blend.
"""

import math
import os
import sys

import bpy
from mathutils import Vector

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
from mocopi_m2m_retarget import cleanup, offset as offset_tool  # noqa: E402

addon.register()

FRAMES = 40

# The nine real controls. Each is given a different rest orientation below, so
# no two of them take the same channel numbers for the same world move.
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
        angle = index * math.tau / len(CONTROLS)
        bone.head = (index * 0.1, 0.0, 1.0)
        bone.tail = (
            index * 0.1 + 0.2 * math.cos(angle),
            0.2 * math.sin(angle),
            1.0 + 0.1 * math.sin(angle * 2.0),
        )
        bone.roll = angle
        bone.parent = root

    # A child of CTRL_Hips, standing in for the spine chain: it must inherit
    # the shift rather than receiving its own.
    spine = edit_bones.new("DRV_spine.001")
    spine.head = (0.0, 0.0, 1.2)
    spine.tail = (0.0, 0.0, 1.4)
    spine.parent = edit_bones["CTRL_Hips"]

    bpy.ops.object.mode_set(mode="OBJECT")
    return armature


armature = build_rig()
# A non-identity armature transform, so matrix_world is not silently ignorable.
armature.rotation_euler = (0.0, 0.0, math.radians(35.0))
armature.location = (0.4, -0.2, 0.0)

for pose_bone in armature.pose.bones:
    pose_bone.rotation_mode = "QUATERNION"

scene = bpy.context.scene
scene.frame_start = 0
scene.frame_end = FRAMES - 1

for frame in range(FRAMES):
    scene.frame_set(frame)
    phase = frame / 20.0 * math.tau
    for index, pose_bone in enumerate(armature.pose.bones):
        if pose_bone.name == "DRV_root":
            continue
        pose_bone.location = (
            math.sin(phase) * 0.05,
            frame * 0.01,
            math.cos(phase + index) * 0.02,
        )
        pose_bone.keyframe_insert("location", frame=frame)
        pose_bone.keyframe_insert("rotation_quaternion", frame=frame)

bpy.context.view_layer.objects.active = armature
curves = cleanup.action_curves(armature)
print("rig: {} channels, {} keys".format(
    len(curves), sum(len(c.keyframe_points) for c in curves)))


def world_positions():
    """Every bone's world position on every frame."""
    out = []
    for frame in range(FRAMES):
        scene.frame_set(frame)
        out.append({
            bone.name: (armature.matrix_world @ bone.matrix).translation.copy()
            for bone in armature.pose.bones
        })
    return out


def key_count():
    return sum(len(c.keyframe_points) for c in cleanup.action_curves(armature))


# ----------------------------------------------------------------------
# 1. A zero offset is refused rather than silently doing nothing
# ----------------------------------------------------------------------

print("\n1. zero offset")
try:
    offset_tool.apply(armature, offset=(0.0, 0.0, 0.0))
except offset_tool.OffsetError as exc:
    check("0, 0, 0" in str(exc) and "fields" in str(exc),
          "zero offset refused, pointing at the fields", str(exc)[:70])
else:
    check(False, "zero offset refused", "it did not raise")


# ----------------------------------------------------------------------
# 2. The offset lands in world space, on every bone, on every frame
# ----------------------------------------------------------------------

print("\n2. offset (0, 0, -0.2)")
DELTA = Vector((0.0, 0.0, -0.2))

before = world_positions()
keys_before = key_count()

# The spine child is animated in its own right, so the invariant is that its
# channels are left exactly as they were -- not that they are zero.
spine_curves = [c for c in cleanup.action_curves(armature)
                if cleanup.bone_name(c.data_path) == "DRV_spine.001"
                and c.data_path.endswith(".location")]
spine_before = [[tuple(p.co) for p in c.keyframe_points] for c in spine_curves]

bpy.context.view_layer.objects.active = armature
result = bpy.ops.m2m.offset_character(offset=DELTA[:])
check(result == {"FINISHED"}, "operator FINISHED", result)

after = world_positions()

worst = 0.0
for frame in range(FRAMES):
    for name, position in before[frame].items():
        if name == "DRV_root":
            continue
        worst = max(worst, (after[frame][name] - (position + DELTA)).length)
check(worst < 1e-5, "every bone moved by exactly the world delta", worst)

check(
    (after[0]["DRV_root"] - before[0]["DRV_root"]).length < 1e-9,
    "DRV_root itself untouched",
)
check(key_count() == keys_before, "no keys added or removed", key_count())

# The channel numbers must differ per bone -- if they don't, the bone-space
# conversion is a no-op and this test is not testing anything.
scene.frame_set(0)
locals_seen = {
    tuple(round(v, 4) for v in offset_tool.world_to_bone(armature, bone, DELTA))
    for bone in armature.pose.bones if bone.name in CONTROLS
}
check(len(locals_seen) == len(CONTROLS),
      "each control took different channel values", len(locals_seen))


# ----------------------------------------------------------------------
# 3. Nested bones inherit rather than doubling up
# ----------------------------------------------------------------------

print("\n3. the spine child")
spine_delta = after[0]["DRV_spine.001"] - before[0]["DRV_spine.001"]
check((spine_delta - DELTA).length < 1e-5,
      "child of CTRL_Hips moved once, not twice", spine_delta[:])

check(len(spine_curves) == 3,
      "the spine child has animated location curves of its own to spare",
      len(spine_curves))
spine_after = [[tuple(p.co) for p in c.keyframe_points] for c in spine_curves]
check(spine_before == spine_after, "and its own channels were not touched")


# ----------------------------------------------------------------------
# 4. Exactly reversible
# ----------------------------------------------------------------------

print("\n4. reversibility")
bpy.ops.m2m.offset_character(offset=(-DELTA).to_tuple())
restored = world_positions()

worst = 0.0
for frame in range(FRAMES):
    for name, position in before[frame].items():
        worst = max(worst, (restored[frame][name] - position).length)
check(worst < 1e-5, "negative offset restores the original", worst)
check(key_count() == keys_before, "still no key count change", key_count())


# ----------------------------------------------------------------------
# 5. The scene property the panel's X/Y/Z fields write into
# ----------------------------------------------------------------------

print("\n5. the panel's offset property")
from mocopi_m2m_retarget import OFFSET_PROPERTY  # noqa: E402

check(hasattr(scene, OFFSET_PROPERTY),
      "scene.{} registered".format(OFFSET_PROPERTY))

setattr(scene, OFFSET_PROPERTY, (0.25, -0.1, 0.05))
check(tuple(round(v, 4) for v in getattr(scene, OFFSET_PROPERTY))
      == (0.25, -0.1, 0.05), "round-trips the typed values",
      tuple(getattr(scene, OFFSET_PROPERTY)))

# What the panel button does: hand the scene values to the operator.
panel_offset = Vector(getattr(scene, OFFSET_PROPERTY))
positions_before = world_positions()
result = bpy.ops.m2m.offset_character(offset=panel_offset[:])
check(result == {"FINISHED"}, "operator FINISHED from the panel values", result)

positions_after = world_positions()
worst = 0.0
for frame in range(FRAMES):
    for name, position in positions_before[frame].items():
        if name == "DRV_root":
            continue
        worst = max(
            worst, (positions_after[frame][name] - (position + panel_offset)).length
        )
check(worst < 1e-5, "moved by exactly what the fields said", worst)


# ----------------------------------------------------------------------
# 6. Repeated offsets accumulate, so nudging works
# ----------------------------------------------------------------------

print("\n6. accumulation")
nudge_start = world_positions()
for _ in range(4):
    bpy.ops.m2m.offset_character(offset=(0.1, 0.0, 0.0))
nudge_end = world_positions()

moved = nudge_end[0]["CTRL_Hips"] - nudge_start[0]["CTRL_Hips"]
check((moved - Vector((0.4, 0.0, 0.0))).length < 1e-5,
      "four 0.1m nudges land at 0.4m", moved[:])
check(key_count() == keys_before, "nudging adds no keys", key_count())

bpy.ops.m2m.offset_character(offset=(-0.4, 0.0, 0.0))


# ----------------------------------------------------------------------
# 7. A control with no location curve gets one
# ----------------------------------------------------------------------

print("\n7. a control with no position curve")
for fcurve in list(cleanup.action_curves(armature)):
    if (cleanup.bone_name(fcurve.data_path) == "POLEARM_L"
            and fcurve.data_path.endswith(".location")):
        cleanup.action_curves(armature).remove(fcurve)

remaining = [c for c in cleanup.action_curves(armature)
             if cleanup.bone_name(c.data_path) == "POLEARM_L"
             and c.data_path.endswith(".location")]
check(not remaining, "POLEARM_L location curves removed for the test",
      len(remaining))

scene.frame_set(5)
pole_before = (armature.matrix_world
               @ armature.pose.bones["POLEARM_L"].matrix).translation.copy()

result = bpy.ops.m2m.offset_character(offset=(0.5, 0.0, 0.0))
check(result == {"FINISHED"}, "operator still FINISHED", result)

created = [c for c in cleanup.action_curves(armature)
           if cleanup.bone_name(c.data_path) == "POLEARM_L"
           and c.data_path.endswith(".location")]
check(len(created) == 3, "three location curves created", len(created))

scene.frame_set(5)
bpy.context.view_layer.update()
pole_after = (armature.matrix_world
              @ armature.pose.bones["POLEARM_L"].matrix).translation
check((pole_after - (pole_before + Vector((0.5, 0.0, 0.0)))).length < 1e-5,
      "the curve-less pole moved with the rest",
      (pole_after - pole_before)[:])


# ----------------------------------------------------------------------
# 8. Refuses on a rig that is not this rig
# ----------------------------------------------------------------------

print("\n8. wrong rig")
bpy.ops.object.armature_add()
stranger = bpy.context.object
stranger.name = "NotTheRig"
try:
    offset_tool.control_bones(stranger)
except offset_tool.OffsetError as exc:
    check("DRV_root" in str(exc), "names the bone it wanted", str(exc)[:60])
else:
    check(False, "refuses a rig with no DRV_root", "it did not raise")

valid_icons = {
    item.identifier
    for item in bpy.types.UILayout.bl_rna.functions["operator"]
    .parameters["icon"].enum_items
}
check("CON_LOCLIKE" in valid_icons, "panel icon CON_LOCLIKE exists")

print("\n{} checks, {} failures".format(checks, len(failures)))
for line in failures:
    print("  " + line)

sys.exit(1 if failures else 0)
