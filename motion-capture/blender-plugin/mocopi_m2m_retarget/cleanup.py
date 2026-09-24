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
degree from where it was. That holds only because the surviving keys get
tangents aimed along clamped neighbour chords; see ``chord_handles`` for
what auto-clamped handles did to this guarantee, and for the stepped-signal
ringing that a raw chord tangent traded it for.

How much that removes depends entirely on how fast the bone is moving, which
is the point. Measured on synthetic 300-frame channels at 0.5 degrees: a
near-still bone keeps 2% of its keys, a drifting head 4%, a swaying spine
11%, a thigh swinging through a five-step walk 34%. The busy channels are the
ones that earn their keys.

**Simplify Pole Targets** is the same two passes at a much looser setting,
aimed at the four IK pole bones and nothing else. A pole target is a hint,
not animation: Blender's IK constraint reads its *position* and the pole
angle, and nothing at all reads its rotation. So the rotation channels
collapse to a single key, and the position channels are blurred and decimated
several times harder than a real bone would tolerate. On a dense capture this
is most of the keys on the rig.

**Delete Non-Extreme Keyframes** is the odd one out: it is not error-bounded
and it is not automatic. Blender tags every key with a *type* -- Keyframe,
Breakdown, Moving Hold, Extreme, Jitter -- which the evaluator ignores but
which is how an animator records that a pose is load-bearing. Mark the
extremes by hand, run the pass, and every channel is thinned down to exactly
those frames. It is the only pass here making a judgement about the
performance rather than about the numbers, which is why it is a button and
never an import option: at import time there is nothing marked yet.

Order matters. Smooth first: smoothing works on key values, so it wants the
dense curve. Then root motion, then the pole pass, then decimate last -- root
motion writes a key on every frame of every control by design, including the
poles, so anything that thins them has to come after it. The extreme pass sits
outside that order entirely; it runs whenever the marks are ready.

Both passes ignore the Mesh2Motion export skeleton, which holds no keyframes
at all -- its bones are driven by Copy Transforms constraints off the DRV rig.
Everything here operates on whatever action the active armature is holding.
"""

import math

import bpy
from mathutils import Euler, Quaternion

from .retarget_engine import current_slot, fcurves_for


BONE_PATH_PREFIX = 'pose.bones["'

# The four IK pole targets on the Mesh2Motion rig, as add-ik-bones.py and the
# bone map name them. Matched case-insensitively, but not guessed at: both
# skeletons are known, which is the same argument that let the retarget engine
# drop Rokoko's name detection.
POLE_BONES = ("POLEARM_L", "POLEARM_R", "POLE_Leg_L", "POLE_Leg_R")


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


def collect_curves(armature, channels="ALL", selected_only=False, bones=None,
                   no_match_message=None):
    """The pose F-curves to operate on, filtered by channel and selection.

    ``bones`` restricts the result to an explicit set of bone names, matched
    case-insensitively -- the pole pass uses it. ``no_match_message`` replaces
    the generic error when nothing survives the filters, so a caller that
    asked for specific bones can say which ones it wanted.
    """
    curves = action_curves(armature)

    wanted_bones = selected_bone_names(armature) if selected_only else None
    if selected_only and not wanted_bones:
        raise CleanupError("No bones are selected. Select some, or turn the option off")

    named = {name.lower() for name in bones} if bones is not None else None

    out = []

    for fcurve in curves:
        name = bone_name(fcurve.data_path)
        if name is None:
            continue
        if wanted_bones is not None and name not in wanted_bones:
            continue
        if named is not None and name.lower() not in named:
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
        raise CleanupError(no_match_message or "No matching animation channels found")

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


def chord_handles(points):
    """Aim every Bezier handle along a clamped chord tangent.

    This is what keeps the decimation tolerance honest. Two failure modes had
    to be dodged, and they pull in opposite directions.

    **Auto-clamped flattens the ends.** ``AUTO_CLAMPED`` zeroes the handles of
    any key that is a local extreme among its neighbours, and a key with only
    one neighbour -- either end of the curve -- always qualifies. A channel
    that decimates to two keys therefore gets two flat handles and the segment
    comes out as a full ease-in/ease-out S-curve, not the straight line the RDP
    pass measured its error against. On a straight 0 -> 2.4m ramp over 120
    frames at a 1mm tolerance that was 229mm off at the quarter points -- 229x
    what was asked for. Plain ``AUTO`` behaves identically. It is worst where
    decimation does best, since the channels that collapse to two or three
    keys are the near-still and slow-drifting ones.

    **A raw chord tangent overshoots corners.** Taking the tangent straight
    from the chord through both neighbours (Catmull-Rom's) fixes the ramp
    exactly, but it rings on a stepped signal: 27mm on a 200mm staircase at
    the same 1mm tolerance, because the tangent at a plateau key points along
    the step it is about to take.

    So the tangent is the chord, clamped the way a monotone cubic clamps it
    (Fritsch-Carlson): zero at a local extreme, and never steeper than three
    times the shallower of the two adjacent secants. On a straight line the
    secants are equal and the clamp does not bind, so the result is exact. On
    a plateau the secants disagree and the tangent goes flat, which is the one
    thing ``AUTO_CLAMPED`` got right. The ends take their one-sided secant
    rather than zero, which is the thing it got wrong.

    Handles must be FREE for Blender to leave them alone; ``fcurve.update()``
    recomputes the AUTO family but not this one.

    For the two error-bounded passes this is a correctness fix. For
    ``delete_non_extreme`` it is a look choice instead -- there is no bound to
    hold between two poses an animator marked, and flat handles would give the
    held-extreme feel of traditional blocking. It shares the code path for
    consistency; splitting it is a one-line change if that look is wanted.
    """
    count = len(points)

    if count == 0:
        return

    if count == 1:
        point = points[0]
        frame, value = point.co
        point.handle_left_type = "FREE"
        point.handle_right_type = "FREE"
        point.handle_left = (frame - 1.0, value)
        point.handle_right = (frame + 1.0, value)
        return

    frames = [point.co[0] for point in points]
    values = [point.co[1] for point in points]

    def secant(lower, upper):
        span = frames[upper] - frames[lower]
        return ((values[upper] - values[lower]) / span) if span else 0.0

    for index, point in enumerate(points):
        if index == 0:
            slope = secant(0, 1)
        elif index == count - 1:
            slope = secant(count - 2, count - 1)
        else:
            before = secant(index - 1, index)
            after = secant(index, index + 1)

            if before * after <= 0.0:
                # A local extreme, or a flat neighbour: anything but zero here
                # overshoots on the way in or on the way out.
                slope = 0.0
            else:
                slope = secant(index - 1, index + 1)
                limit = 3.0 * min(abs(before), abs(after))
                if abs(slope) > limit:
                    slope = limit if slope > 0.0 else -limit

        left = (frames[index] - frames[max(0, index - 1)]) / 3.0
        right = (frames[min(count - 1, index + 1)] - frames[index]) / 3.0
        if index == 0:
            left = right
        if index == count - 1:
            right = left

        point.handle_left_type = "FREE"
        point.handle_right_type = "FREE"
        point.handle_left = (frames[index] - left, values[index] - slope * left)
        point.handle_right = (frames[index] + right, values[index] + slope * right)


def set_interpolation(points, interpolation):
    """Set every surviving key's interpolation, unless asked to leave it.

    Sparse keys left on LINEAR read as a series of straight segments, which is
    worse to look at than the jitter that was just removed -- so BEZIER is the
    default, with ``chord_handles`` keeping it inside the stated tolerance.
    """
    if interpolation == "KEEP":
        return

    for point in points:
        point.interpolation = interpolation

    if interpolation == "BEZIER":
        chord_handles(points)


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

    set_interpolation(points, interpolation)
    fcurve.update()

    return before, len(points)


def collapse_curve(fcurve, interpolation="BEZIER"):
    """Reduce a curve to its first keyframe. Returns (before, after).

    Used for channels that are known not to be read at all, where decimating
    to a tolerance would be pretending there is something to preserve. The
    first key is kept rather than an average, for the same reason
    ``decimate_curve``'s static collapse keeps it: it is the value the clip
    already starts on.
    """
    points = fcurve.keyframe_points
    before = len(points)

    if before < 2:
        return before, before

    for index in range(before - 1, 0, -1):
        points.remove(points[index], fast=True)

    set_interpolation(points, interpolation)
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
# Extremes
# ----------------------------------------------------------------------

EXTREME_KEY_TYPE = "EXTREME"
ROTATION_GUARD_MAX_ANGLE = math.radians(120.0)


def extreme_frames(armature):
    """Sorted frames carrying an Extreme-typed key, anywhere in the action.

    Gathered across the **whole** action rather than the caller's filtered
    curve set. Marking a pose in the Dope Sheet is meant to pin that frame for
    the rig, not only for whichever channel the mark happened to land on -- so
    a mark on the hips keeps frame 24 on the elbows too.

    Frames are rounded to integers. A bake lands on whole frames, and matching
    one float frame against another is a way to lose a key to 1e-7.

    Types are read one key at a time rather than through ``foreach_get``. The
    type is an enum, so it is the one property in this module whose bulk-read
    buffer is version-dependent -- and getting it wrong here would not be
    slow, it would delete the wrong keys.
    """
    frames = set()

    for fcurve in action_curves(armature):
        for point in fcurve.keyframe_points:
            if point.type == EXTREME_KEY_TYPE:
                frames.add(int(round(point.co[0])))

    return sorted(frames)


def snap_curve_to_frames(fcurve, frames, interpolation="BEZIER"):
    """Reduce a curve to exactly ``frames``. Returns (before, after, inserted).

    Where the curve has no key on a wanted frame it is sampled there first, so
    every channel comes out on the same frame set holding the pose it was
    already showing at that frame. Without this, a channel that had been
    decimated away from the extreme frames would either lose everything or
    have to be skipped, and the rig would come out on ragged frame sets.

    Sampling is done up front, because ``evaluate`` has to read the curve as
    it stands -- before anything has been inserted into it or removed from it.

    Keyframe *types* are left alone. A key the sampling step inserts arrives
    as a plain Keyframe rather than an Extreme, which keeps the pass from
    overwriting how the animator tagged their own keys. Deletion is by frame
    and not by type, so re-running is still a no-op.
    """
    points = fcurve.keyframe_points
    before = len(points)

    if before == 0:
        return 0, 0, 0

    wanted = set(frames)
    present = {int(round(point.co[0])) for point in points}

    sampled = [(frame, fcurve.evaluate(frame)) for frame in sorted(wanted - present)]

    for frame, value in sampled:
        points.insert(frame, value, options={"FAST"})

    if sampled:
        fcurve.update()

    for index in range(len(points) - 1, -1, -1):
        if int(round(points[index].co[0])) not in wanted:
            points.remove(points[index], fast=True)

    set_interpolation(points, interpolation)
    fcurve.update()

    return before, len(points), len(sampled)


def rotation_order(armature, data_path):
    """Euler order for a pose-bone rotation curve, defaulting to Blender's XYZ."""
    name = bone_name(data_path)
    if name is None:
        return "XYZ"

    pose_bone = armature.pose.bones.get(name)
    mode = getattr(pose_bone, "rotation_mode", "XYZ") if pose_bone else "XYZ"
    return mode if mode in {"XYZ", "XZY", "YXZ", "YZX", "ZXY", "ZYX"} else "XYZ"


def align_euler_group(armature, data_path, by_index, interpolation=None):
    """Choose equivalent Euler values that interpolate continuously."""
    curves = [by_index.get(i) for i in range(3)]
    if any(curve is None for curve in curves):
        return False

    point_sets = [list(curve.keyframe_points) for curve in curves]
    count = len(point_sets[0])
    if count == 0 or any(len(points) != count for points in point_sets):
        return False

    frames = [[int(round(point.co[0])) for point in points] for points in point_sets]
    if any(channel_frames != frames[0] for channel_frames in frames[1:]):
        return False

    order = rotation_order(armature, data_path)
    values = [[], [], []]
    previous = None

    for index in range(count):
        euler = Euler(tuple(points[index].co[1] for points in point_sets), order)
        if previous is not None:
            euler.make_compatible(previous)
        previous = euler.copy()

        for channel in range(3):
            values[channel].append(euler[channel])

    for channel, curve in enumerate(curves):
        write_values(curve, values[channel])
        if interpolation is not None:
            set_interpolation(curve.keyframe_points, interpolation)
        curve.update()

    return True


def align_euler_curves(armature, curves, interpolation=None):
    """Repair Euler channels before and after frame snapping."""
    groups = {}
    for fcurve in curves:
        if fcurve.data_path.endswith("rotation_euler"):
            groups.setdefault(fcurve.data_path, {})[fcurve.array_index] = fcurve

    for data_path, by_index in groups.items():
        align_euler_group(armature, data_path, by_index, interpolation)


def align_quaternion_group(by_index, interpolation=None):
    """Choose quaternion signs that interpolate through the short arc."""
    curves = [by_index.get(index) for index in range(4)]
    if any(curve is None for curve in curves):
        return False

    point_sets = [list(curve.keyframe_points) for curve in curves]
    count = len(point_sets[0])
    if count == 0 or any(len(points) != count for points in point_sets):
        return False

    frames = [[int(round(point.co[0])) for point in points] for points in point_sets]
    if any(channel_frames != frames[0] for channel_frames in frames[1:]):
        return False

    values = [[], [], [], []]
    previous = None

    for index in range(count):
        quaternion = Quaternion(tuple(points[index].co[1] for points in point_sets))
        if quaternion.magnitude < 1e-12:
            if previous is None:
                quaternion = Quaternion((1.0, 0.0, 0.0, 0.0))
            else:
                quaternion = previous.copy()
        else:
            quaternion.normalize()

        if previous is not None:
            dot = sum(quaternion[channel] * previous[channel] for channel in range(4))
            if dot < 0.0:
                quaternion = Quaternion(tuple(-quaternion[channel] for channel in range(4)))

        previous = quaternion.copy()
        for channel in range(4):
            values[channel].append(quaternion[channel])

    for channel, curve in enumerate(curves):
        write_values(curve, values[channel])
        if interpolation is not None:
            set_interpolation(curve.keyframe_points, interpolation)
        curve.update()

    return True


def align_quaternion_curves(curves, interpolation=None):
    """Repair quaternion groups before and after frame snapping."""
    groups = {}
    for fcurve in curves:
        if fcurve.data_path.endswith("rotation_quaternion"):
            groups.setdefault(fcurve.data_path, {})[fcurve.array_index] = fcurve

    for by_index in groups.values():
        align_quaternion_group(by_index, interpolation)


def quaternion_angle(a, b):
    """Shortest angular distance between two quaternions."""
    dot = abs(sum(a[index] * b[index] for index in range(4)))
    return 2.0 * math.acos(min(1.0, max(-1.0, dot)))


def rotation_guard_orientation(armature, data_path, by_index, frame):
    """Rotation at ``frame`` as a normalised quaternion, or None."""
    if data_path.endswith("rotation_euler"):
        curves = [by_index.get(index) for index in range(3)]
        if any(curve is None for curve in curves):
            return None

        order = rotation_order(armature, data_path)
        return Euler(tuple(curve.evaluate(frame) for curve in curves), order).to_quaternion()

    if data_path.endswith("rotation_quaternion"):
        curves = [by_index.get(index) for index in range(4)]
        if any(curve is None for curve in curves):
            return None

        quaternion = Quaternion(tuple(curve.evaluate(frame) for curve in curves))
        if quaternion.magnitude < 1e-12:
            return None
        quaternion.normalize()
        return quaternion

    return None


def rotation_guard_frames(armature, curves, frames,
                          max_angle=ROTATION_GUARD_MAX_ANGLE):
    """Unmarked source frames needed to keep large turns well sampled."""
    if len(frames) < 2:
        return []

    groups = {}
    for fcurve in curves:
        if fcurve.data_path.endswith(("rotation_euler", "rotation_quaternion")):
            groups.setdefault(fcurve.data_path, {})[fcurve.array_index] = fcurve

    wanted = set(frames)
    guarded = set()

    for data_path, by_index in groups.items():
        source_frames = set()
        for fcurve in by_index.values():
            source_frames.update(int(round(point.co[0])) for point in fcurve.keyframe_points)

        for start, end in zip(frames, frames[1:]):
            samples = [frame for frame in sorted(source_frames) if start <= frame <= end]
            if start not in samples:
                samples.insert(0, start)
            if end not in samples:
                samples.append(end)
            samples = sorted(set(samples))

            previous = rotation_guard_orientation(armature, data_path, by_index, samples[0])
            if previous is None:
                continue

            travelled = 0.0
            for frame in samples[1:]:
                current = rotation_guard_orientation(armature, data_path, by_index, frame)
                if current is None:
                    break

                travelled += quaternion_angle(previous, current)
                if travelled >= max_angle and frame not in wanted:
                    guarded.add(frame)
                    travelled = 0.0

                previous = current

    return sorted(guarded)


# ----------------------------------------------------------------------
# Entry points
# ----------------------------------------------------------------------

def delete_scale(armature):
    """Delete every scale F-curve from the active action."""
    curves = action_curves(armature)
    scale_curves = [
        fcurve for fcurve in curves
        if channel_kind(fcurve.data_path) == "SCALE"
    ]
    keys = sum(len(fcurve.keyframe_points) for fcurve in scale_curves)

    for fcurve in scale_curves:
        curves.remove(fcurve)

    return {"channels": len(scale_curves), "keys": keys}


def smooth(armature, sigma_frames=1.0, channels="ALL", selected_only=False):
    """Smooth the active action's curves. Returns a summary dict."""
    curves = collect_curves(armature, channels=channels, selected_only=selected_only)
    changed = smooth_curves(curves, sigma_frames)

    return {"channels": changed, "sigma": sigma_frames}


def simplify_poles(armature, sigma_frames=6.0, tolerance=0.01,
                   interpolation="BEZIER", bones=POLE_BONES):
    """Thin the IK pole targets hard. Returns a summary dict.

    The four poles are hints rather than animation, and they are treated as
    such:

    * **Rotation collapses to one key.** A Blender IK constraint uses a pole
      target's position and the pole angle. Its orientation is not read by
      anything, and on the Mesh2Motion rig no bone is parented to a pole, so
      the rotation curves are carrying nothing.
    * **Position is blurred and decimated at a loose bound.** The pole sits
      half a metre out from the limb and only has to point the elbow or knee
      the right way, so a centimetre of error is invisible where the same
      number on a foot control would not be.

    Run it after root motion, which re-keys every frame of every control that
    hangs off ``DRV_root`` -- the poles included.
    """
    curves = collect_curves(
        armature,
        bones=bones,
        no_match_message=(
            "No animated pole targets found on '{}' (looked for {})".format(
                armature.name, ", ".join(bones)
            )
        ),
    )

    rotation = [c for c in curves if channel_kind(c.data_path) == "ROTATION"]
    motion = [c for c in curves if channel_kind(c.data_path) != "ROTATION"]

    before = sum(len(c.keyframe_points) for c in curves)

    for fcurve in rotation:
        collapse_curve(fcurve, interpolation=interpolation)

    if motion and sigma_frames > 0.0:
        smooth_curves(motion, sigma_frames)

    for fcurve in motion:
        decimate_curve(
            fcurve, tolerance, collapse_static=True, interpolation=interpolation
        )

    after = sum(len(c.keyframe_points) for c in curves)
    removed = before - after

    return {
        "bones": sorted({bone_name(c.data_path) for c in curves}),
        "channels": len(curves),
        "rotation_channels": len(rotation),
        "before": before,
        "after": after,
        "removed": removed,
        "ratio": (removed / float(before)) if before else 0.0,
    }


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


def delete_non_extreme(armature, channels="ALL", selected_only=False,
                       interpolation="BEZIER"):
    """Thin the action down to the frames marked Extreme. Returns a summary dict.

    This pass has no tolerance and no strength, because it is not deciding
    anything -- the frame set is whatever the animator marked. Refusing when
    nothing is marked is the important guard: an empty frame set would take
    the whole action with it.
    """
    frames = extreme_frames(armature)

    if not frames:
        raise CleanupError(
            "No keyframes are marked Extreme, so there is nothing to keep. "
            "Select the poses you want in the Dope Sheet, set Key > Keyframe "
            "Type > Extreme (R), then run this again"
        )

    curves = collect_curves(armature, channels=channels, selected_only=selected_only)

    before = 0
    after = 0
    inserted = 0
    extreme_count = len(frames)

    align_quaternion_curves(curves)
    align_euler_curves(armature, curves)
    guarded_frames = rotation_guard_frames(armature, curves, frames)
    frames = sorted(set(frames) | set(guarded_frames))

    for fcurve in curves:
        curve_before, curve_after, curve_inserted = snap_curve_to_frames(
            fcurve, frames, interpolation=interpolation
        )
        before += curve_before
        after += curve_after
        inserted += curve_inserted

    align_quaternion_curves(curves, "LINEAR")
    align_euler_curves(armature, curves, interpolation)

    total = before + inserted
    removed = total - after

    return {
        "channels": len(curves),
        "frames": len(frames),
        "extremes": extreme_count,
        "first": frames[0],
        "last": frames[-1],
        "before": before,
        "after": after,
        "inserted": inserted,
        "guarded": len(guarded_frames),
        "removed": removed,
        "ratio": (removed / float(total)) if total else 0.0,
    }
