"""Root motion extraction.

The Mesh2Motion rig has a real root bone: the export skeleton's `root` copies
`DRV_root` (Copy Transforms, World/World), and `DRV_root` normally carries no
animation at all -- which is why every bit of travel ends up baked into the
hips.

This module moves the horizontal travel out of the hips and onto `DRV_root`,
without changing a single frame of the visible pose.

The mechanism:

1. Sample the hips control's world path over the frame range.
2. Smooth the XY of that path and zero it at the first frame, so per-step
   sway stays in the hips and the clip still starts where it started.
3. Record the pose-space matrix of every direct child of `DRV_root` on every
   frame -- the hips, the hand and foot IK controls, and the four poles.
4. Key `DRV_root` with the path.
5. Write the recorded matrices back onto those children. Blender re-solves
   each one against the now-moving parent, so the world result is identical
   and the motion has simply been redistributed.

Step 3/5 is the part that is easy to get wrong: every control on this rig is
a child of `DRV_root`, and after the retarget bake they all hold plain local
keyframes. Animating the root without compensating would travel the character
twice and slide the planted feet.

The export skeleton needs no compensation -- its Copy Transforms constraints
are World/World, so `pelvis` stays pinned to `DRV_hips` regardless of what
`root` does. On export, `root` carries the travel and `pelvis` bakes down to
the residual.
"""

import math

import bpy
from mathutils import Vector


ROOT_BONE = "DRV_root"
HIPS_BONE = "CTRL_Hips"

# Rotation data paths by rotation mode.
ROTATION_PATHS = {
    "QUATERNION": "rotation_quaternion",
    "AXIS_ANGLE": "rotation_axis_angle",
}


class RootMotionError(Exception):
    """Raised when root motion cannot be extracted."""


def gaussian_kernel(sigma):
    """Return a normalised 1D Gaussian kernel, or None for no smoothing."""
    if sigma <= 0.0:
        return None

    radius = max(1, int(math.ceil(sigma * 3.0)))
    kernel = [math.exp(-(x * x) / (2.0 * sigma * sigma)) for x in range(-radius, radius + 1)]
    total = sum(kernel)

    return [k / total for k in kernel]


def sample(values, index):
    """Read values[index], extrapolating linearly past either end.

    Clamping the ends instead (the obvious choice) flattens the path where it
    runs off the edge of the kernel, which costs real travel at the start and
    end of the clip -- the root lags and the hips drift forward to make up the
    difference. Reflecting about the endpoint reproduces a constant velocity
    exactly, so a steady walk keeps every centimetre of its travel.
    """
    count = len(values)

    if index < 0:
        return 2.0 * values[0] - values[min(-index, count - 1)]
    if index > count - 1:
        return 2.0 * values[count - 1] - values[max(2 * (count - 1) - index, 0)]

    return values[index]


def smooth_series(values, sigma):
    """Gaussian-smooth a list of floats."""
    kernel = gaussian_kernel(sigma)

    if kernel is None or len(values) < 2:
        return list(values)

    radius = len(kernel) // 2
    out = []

    for i in range(len(values)):
        acc = 0.0
        for k, weight in enumerate(kernel):
            acc += sample(values, i + k - radius) * weight
        out.append(acc)

    return out


def sigma_for(smoothing, fps):
    """Map a 0..1 smoothing slider onto a Gaussian sigma in frames.

    At 1.0 this is half a second of blur, which flattens the side-to-side
    sway of a walk without noticeably lagging a direction change.
    """
    return max(0.0, smoothing) * fps * 0.5


def rotation_path(pose_bone):
    return ROTATION_PATHS.get(pose_bone.rotation_mode, "rotation_euler")


def extract(armature, frame_start=None, frame_end=None, smoothing=0.5,
            zero_start=True, root_name=ROOT_BONE, hips_name=HIPS_BONE):
    """Move horizontal travel from the hips onto the root bone.

    Returns a dict describing what happened, for the operator to report.
    """
    scene = bpy.context.scene

    if frame_start is None:
        frame_start = scene.frame_start
    if frame_end is None:
        frame_end = scene.frame_end

    if frame_end < frame_start:
        raise RootMotionError("Frame range is empty")

    root = armature.pose.bones.get(root_name)
    if root is None:
        raise RootMotionError("No '{}' bone on {}".format(root_name, armature.name))

    hips = armature.pose.bones.get(hips_name)
    if hips is None:
        raise RootMotionError("No '{}' bone on {}".format(hips_name, armature.name))

    # Every control hangs off the root, so all of them need compensating.
    children = [bone for bone in armature.pose.bones if bone.parent == root]
    if not children:
        raise RootMotionError("'{}' has no child bones to compensate".format(root_name))

    frames = list(range(int(frame_start), int(frame_end) + 1))
    original_frame = scene.frame_current

    # --- 1. Sample the current motion -----------------------------------
    hips_x = []
    hips_y = []
    recorded = []

    for frame in frames:
        scene.frame_set(frame)

        head = hips.matrix.translation
        hips_x.append(head.x)
        hips_y.append(head.y)

        recorded.append({bone.name: bone.matrix.copy() for bone in children})

    # --- 2. Shape the path ----------------------------------------------
    sigma = sigma_for(smoothing, scene.render.fps)
    path_x = smooth_series(hips_x, sigma)
    path_y = smooth_series(hips_y, sigma)

    if zero_start:
        origin_x, origin_y = path_x[0], path_y[0]
        path_x = [x - origin_x for x in path_x]
        path_y = [y - origin_y for y in path_y]

    # Root motion is horizontal only: vertical bob and lean stay in the hips.
    # pose_bone.location is in the bone's own rest space, so the world-space
    # path has to be rotated into it (rotation only -- not the head offset).
    rest_inv = root.bone.matrix_local.to_3x3().inverted()
    root_path = [
        rest_inv @ Vector((path_x[i], path_y[i], 0.0))
        for i in range(len(frames))
    ]

    travel = max(
        (Vector((path_x[i], path_y[i])) - Vector((path_x[0], path_y[0]))).length
        for i in range(len(frames))
    )

    # --- 3. Key the root ------------------------------------------------
    for index, frame in enumerate(frames):
        root.location = root_path[index]
        root.keyframe_insert(data_path="location", frame=frame, group="Root Motion")

    # --- 4. Put every child back where it was ---------------------------
    for index, frame in enumerate(frames):
        scene.frame_set(frame)

        stored = recorded[index]

        for bone in children:
            matrix = stored.get(bone.name)
            if matrix is None:
                continue
            bone.matrix = matrix

        bpy.context.view_layer.update()

        for bone in children:
            if bone.name not in stored:
                continue
            bone.keyframe_insert(data_path="location", frame=frame)
            bone.keyframe_insert(data_path=rotation_path(bone), frame=frame)

    scene.frame_set(original_frame)

    return {
        "frames": len(frames),
        "children": len(children),
        "travel": travel,
        "sigma": sigma,
    }
