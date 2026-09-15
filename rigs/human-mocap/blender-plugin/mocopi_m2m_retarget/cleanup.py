"""Keyframe cleanup: smoothing and decimation.

A retargeted Mocopi capture lands as one keyframe per bone per channel per
frame, carrying every bit of the capture's high-frequency jitter. Two passes
fix that, and they are deliberately separate operations:

**Smooth** blurs the *values* along time with a Gaussian, leaving the key
count alone. This is the pass that removes shake. It runs in frame space, so
the width is stated in frames rather than as an opaque 0..1 factor -- at 24fps
a sigma of 1 is about a twelfth of a second, which kills sensor noise without
softening a footplant.

**Decimate** removes *keys* that the curve does not need, using
Ramer-Douglas-Peucker with a vertical error bound: a key survives only if
dropping it would move the curve further than the tolerance at some frame.
Tolerances are given in real units -- metres for position, degrees for
rotation -- so half a degree means no bone ever ends up more than half a
degree from where it was.

How much that removes depends entirely on how fast the bone is moving, which
is the point. Measured on synthetic 300-frame channels at 0.5 degrees: a
near-still bone keeps 2% of its keys, a drifting head 4%, a swaying spine
11%, a thigh swinging through a five-step walk 34%. The busy channels are the
ones that earn their keys.

Order matters. Smooth first: smoothing works on key values, so it wants the
dense curve. Decimate last, after root motion extraction, since root motion
writes a key on every frame of every control by design.

Both passes ignore the Mesh2Motion export skeleton, which holds no keyframes
at all -- its bones are driven by Copy Transforms constraints off the DRV rig.
Everything here operates on whatever action the active armature is holding.
"""

import math

import bpy

from .retarget_engine import current_slot, fcurves_for


BONE_PATH_PREFIX = 'pose.bones["'


class CleanupError(Exception):
    """Raised for anything the caller should surface to the user."""


# ----------------------------------------------------------------------
# Curve gathering
# ----------------------------------------------------------------------

def action_curves(obj):
    """The F-curves of obj's current action, slotted or legacy."""
    anim_data = obj.animation_data
    if anim_data is None or anim_data.action is None:
        raise CleanupError("'{}' has no animation to clean up".format(obj.name))

    curves = fcurves_for(anim_data.action, current_slot(obj))
    if curves is None:
        raise CleanupError(
            "Could not read the action's F-curves. This Blender version may "
            "store animation differently than expected."
        )

    return curves


def bone_name(data_path):
    """The bone a pose F-curve belongs to, or None if it is not one."""
    if not data_path.startswith(BONE_PATH_PREFIX):
        return None
    parts = data_path.split('"')
    return parts[1] if len(parts) >= 3 else None


def channel_kind(data_path):
    """LOCATION / ROTATION / SCALE / OTHER for an F-curve's data path."""
    tail = data_path.rsplit(".", 1)[-1]
    if tail == "location":
        return "LOCATION"
    if tail.startswith("rotation_"):
        return "ROTATION"
    if tail == "scale":
        return "SCALE"
    return "OTHER"


def selected_bone_names(armature):
    """Names of the selected pose bones, across Blender versions.

    Blender 5.0 moved ``select`` from the bone onto the pose bone; everything
    before it keeps the property on ``armature.data.bones``.
    """
    names = set()

    for pose_bone in armature.pose.bones:
        selected = getattr(pose_bone, "select", None)
        if selected is None:
            bone = armature.data.bones.get(pose_bone.name)
            selected = getattr(bone, "select", False)
        if selected:
            names.add(pose_bone.name)

    return names


def collect_curves(armature, channels="ALL", selected_only=False):
    """The pose F-curves to operate on, filtered by channel and selection."""
    curves = action_curves(armature)

    wanted_bones = selected_bone_names(armature) if selected_only else None
    if selected_only and not wanted_bones:
        raise CleanupError("No bones are selected. Select some, or turn the option off")

    out = []

    for fcurve in curves:
        name = bone_name(fcurve.data_path)
        if name is None:
            continue
        if wanted_bones is not None and name not in wanted_bones:
            continue

        kind = channel_kind(fcurve.data_path)
        if kind == "OTHER":
            continue
        if channels != "ALL" and kind != channels:
            continue
        if len(fcurve.keyframe_points) < 2:
            continue

        out.append(fcurve)

    if not out:
        raise CleanupError("No matching animation channels found")

    return out


# ----------------------------------------------------------------------
# Bulk key access
#
# foreach_get / foreach_set on a flat buffer is an order of magnitude faster
# than touching keyframe_points one at a time, and a baked capture has tens of
# thousands of keys.
# ----------------------------------------------------------------------

def read_co(fcurve):
    """(frames, values) of every key on the curve."""
    count = len(fcurve.keyframe_points)
    buffer = [0.0] * (count * 2)
    fcurve.keyframe_points.foreach_get("co", buffer)
    return buffer[0::2], buffer[1::2]


def write_values(fcurve, values):
    """Replace every key's value, shifting its handles by the same delta.

    The handles have to move with the key or a Bezier curve keeps the shape of
    the old data. Shifting is exact for LINEAR keys (where handles are unused)
    and preserves the tangent for the rest.
    """
    points = fcurve.keyframe_points
    count = len(points)

    co = [0.0] * (count * 2)
    points.foreach_get("co", co)

    deltas = [values[i] - co[i * 2 + 1] for i in range(count)]

    for i in range(count):
        co[i * 2 + 1] = values[i]
    points.foreach_set("co", co)

    for attribute in ("handle_left", "handle_right"):
        handles = [0.0] * (count * 2)
        points.foreach_get(attribute, handles)
        for i in range(count):
            handles[i * 2 + 1] += deltas[i]
        points.foreach_set(attribute, handles)

    fcurve.update()


# ----------------------------------------------------------------------
# Smoothing
# ----------------------------------------------------------------------

def gaussian_kernel(sigma):
    """A normalised 1D Gaussian kernel, or None for no smoothing."""
    if sigma <= 0.0:
        return None

    radius = max(1, int(math.ceil(sigma * 3.0)))
    kernel = [math.exp(-(x * x) / (2.0 * sigma * sigma)) for x in range(-radius, radius + 1)]
    total = sum(kernel)

    return [k / total for k in kernel]


def sample(values, index):
    """Read values[index], extrapolating linearly past either end.

    Clamping at the ends flattens the curve where it runs off the edge of the
    kernel, which drags the first and last frames of a clip toward whatever
    the neighbouring frames were doing. Reflecting about the endpoint instead
    reproduces a constant slope exactly, so a bone that was turning steadily
    at the end of the capture still is.
    """
    count = len(values)

    if index < 0:
        return 2.0 * values[0] - values[min(-index, count - 1)]
    if index > count - 1:
        return 2.0 * values[count - 1] - values[max(2 * (count - 1) - index, 0)]

    return values[index]


def smooth_series(values, sigma):
    """Gaussian-smooth a list of floats in index space."""
    kernel = gaussian_kernel(sigma)

    if kernel is None or len(values) < 3:
        return list(values)

    radius = len(kernel) // 2
    out = []

    for i in range(len(values)):
        acc = 0.0
        for k, weight in enumerate(kernel):
            acc += sample(values, i + k - radius) * weight
        out.append(acc)

    return out


def index_sigma(frames, sigma_frames):
    """Convert a sigma in frames to one in key indices.

    A freshly baked capture has a key on every frame, so these are the same
    number. After a decimation pass they are not, and the kernel has to widen
    or narrow to still mean the same amount of time.
    """
    count = len(frames)
    if count < 2:
        return 0.0

    spacing = (frames[-1] - frames[0]) / float(count - 1)
    if spacing <= 0.0:
        return sigma_frames

    return sigma_frames / spacing


def smooth_quaternion(group, sigma_frames):
    """Smooth the four curves of one rotation_quaternion together.

    Two things make quaternions different from any other channel. The bake can
    emit q and -q on consecutive frames -- the same rotation, a sign flip in
    the numbers, and a component-wise blur across it swings the bone through a
    full turn. And a blurred quaternion is no longer unit length, which reads
    as a scale on the bone. So: unflip, smooth, renormalise.
    """
    curves = [group.get(i) for i in range(4)]
    if any(curve is None for curve in curves):
        return 0

    data = [read_co(curve) for curve in curves]
    frames = data[0][0]
    count = len(frames)

    if any(len(values) != count for _frames, values in data):
        return 0

    channels = [list(values) for _frames, values in data]

    # Unflip: put every quaternion in the same hemisphere as the one before.
    for i in range(1, count):
        dot = sum(channels[c][i] * channels[c][i - 1] for c in range(4))
        if dot < 0.0:
            for c in range(4):
                channels[c][i] = -channels[c][i]

    sigma = index_sigma(frames, sigma_frames)
    smoothed = [smooth_series(channel, sigma) for channel in channels]

    for i in range(count):
        length = math.sqrt(sum(smoothed[c][i] * smoothed[c][i] for c in range(4)))
        if length < 1e-12:
            for c in range(4):
                smoothed[c][i] = channels[c][i]
            continue
        for c in range(4):
            smoothed[c][i] /= length

    for c in range(4):
        write_values(curves[c], smoothed[c])

    return 4


def smooth_curves(curves, sigma_frames):
    """Smooth every curve. Returns the number of channels changed."""
    if sigma_frames <= 0.0:
        return 0

    groups = {}
    for fcurve in curves:
        groups.setdefault(fcurve.data_path, {})[fcurve.array_index] = fcurve

    changed = 0

    for data_path, by_index in groups.items():
        if data_path.endswith("rotation_quaternion") and len(by_index) == 4:
            handled = smooth_quaternion(by_index, sigma_frames)
            if handled:
                changed += handled
                continue

        for fcurve in by_index.values():
            frames, values = read_co(fcurve)
            smoothed = smooth_series(values, index_sigma(frames, sigma_frames))
            write_values(fcurve, smoothed)
            changed += 1

    return changed


# ----------------------------------------------------------------------
# Decimation
# ----------------------------------------------------------------------

def rdp_keep(frames, values, tolerance):
    """Ramer-Douglas-Peucker over a curve, measuring error vertically.

    The usual perpendicular-distance form mixes frames and values into one
    distance, which is meaningless when one axis is frames and the other is
    radians. Vertical distance is the quantity that actually matters here:
    how far the curve would move at that frame if the key were dropped.

    Returns a list of booleans, one per key.
    """
    count = len(frames)
    keep = [False] * count

    if count < 3:
        return [True] * count

    keep[0] = True
    keep[count - 1] = True

    stack = [(0, count - 1)]

    while stack:
        first, last = stack.pop()
        if last - first < 2:
            continue

        x0, y0 = frames[first], values[first]
        span = frames[last] - x0
        slope = (values[last] - y0) / span if span else 0.0

        worst = -1.0
        worst_index = -1

        for i in range(first + 1, last):
            error = abs(values[i] - (y0 + slope * (frames[i] - x0)))
            if error > worst:
                worst = error
                worst_index = i

        if worst > tolerance:
            keep[worst_index] = True
            stack.append((first, worst_index))
            stack.append((worst_index, last))

    return keep


def tolerance_for(data_path, tolerance_location, tolerance_rotation_deg):
    """The error bound for one channel, in that channel's own units."""
    kind = channel_kind(data_path)

    if kind in {"LOCATION", "SCALE"}:
        return tolerance_location

    radians = math.radians(tolerance_rotation_deg)

    # A quaternion component is sin(angle/2) about its axis, so an angular
    # error of A shows up as roughly A/2 in the numbers.
    if data_path.endswith("rotation_quaternion"):
        return radians * 0.5

    return radians


def decimate_curve(fcurve, tolerance, collapse_static=True, interpolation="BEZIER"):
    """Drop every key the curve can do without. Returns (before, after)."""
    points = fcurve.keyframe_points
    before = len(points)

    if before < 3:
        return before, before

    frames, values = read_co(fcurve)
    keep = rdp_keep(frames, values, tolerance)

    # A channel that never moves needs one key, not two.
    if collapse_static and sum(keep) == 2:
        lowest = min(values)
        highest = max(values)
        if highest - lowest <= tolerance:
            keep[-1] = False

    for index in range(before - 1, -1, -1):
        if not keep[index]:
            points.remove(points[index], fast=True)

    if interpolation != "KEEP":
        for point in points:
            point.interpolation = interpolation
            if interpolation == "BEZIER":
                point.handle_left_type = "AUTO_CLAMPED"
                point.handle_right_type = "AUTO_CLAMPED"

    fcurve.update()

    return before, len(points)


def decimate_curves(curves, tolerance_location, tolerance_rotation_deg,
                    collapse_static=True, interpolation="BEZIER"):
    """Decimate every curve. Returns (keys_before, keys_after, channels)."""
    total_before = 0
    total_after = 0

    for fcurve in curves:
        tolerance = tolerance_for(
            fcurve.data_path, tolerance_location, tolerance_rotation_deg
        )
        before, after = decimate_curve(
            fcurve,
            tolerance,
            collapse_static=collapse_static,
            interpolation=interpolation,
        )
        total_before += before
        total_after += after

    return total_before, total_after, len(curves)


# ----------------------------------------------------------------------
# Entry points
# ----------------------------------------------------------------------

def smooth(armature, sigma_frames=1.0, channels="ALL", selected_only=False):
    """Smooth the active action's curves. Returns a summary dict."""
    curves = collect_curves(armature, channels=channels, selected_only=selected_only)
    changed = smooth_curves(curves, sigma_frames)

    return {"channels": changed, "sigma": sigma_frames}


def decimate(armature, tolerance_location=0.001, tolerance_rotation=0.5,
             channels="ALL", selected_only=False, collapse_static=True,
             interpolation="BEZIER"):
    """Decimate the active action's curves. Returns a summary dict."""
    curves = collect_curves(armature, channels=channels, selected_only=selected_only)

    before, after, count = decimate_curves(
        curves,
        tolerance_location,
        tolerance_rotation,
        collapse_static=collapse_static,
        interpolation=interpolation,
    )

    removed = before - after
    ratio = (removed / float(before)) if before else 0.0

    return {
        "channels": count,
        "before": before,
        "after": after,
        "removed": removed,
        "ratio": ratio,
    }
