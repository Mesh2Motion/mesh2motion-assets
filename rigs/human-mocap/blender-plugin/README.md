# Mocopi to Mesh2Motion (Blender extension)

Wraps the retargeting workflow from `../README.md` into one button — import,
rig append, prep scripts, retarget and bake.

## Install

1. `python build.py` — copies the current `../scripts/*.py` into the addon
   package and writes `dist/mocopi_m2m_retarget-0.4.0.zip`.
2. In Blender 4.2+, drag the zip into the window, or
   **Edit > Preferences > Add-ons > Install from Disk**.
3. Enable **Mocopi to Mesh2Motion**.

The panel appears in the 3D viewport sidebar (`N`) under the **Mesh2Motion** tab.
The Rokoko addon is no longer needed.

## What the button does

**Load Mocopi BVH File**:

1. Opens a file picker for a `.bvh` capture and imports it at **0.01** scale.
   Scene FPS and frame range are set from the capture — the frame range
   matters, because the pole bone bake in `add-ik-bones.py` runs over
   `frame_start..frame_end`.
2. Appends the **Rig** and **Custom Bone Shapes** collections from the
   bundled `human-mocopi-rig-setup.blend`. Appended, not linked: library
   overrides cannot enter edit mode. Collections already in the scene are
   skipped, so re-importing does not pile up `Rig.001`.
3. Selects the imported BVH skeleton and runs `scripts/retarget-master.py`,
   which expands the lower arms and builds the elbow / knee pole bones.
4. Retargets the capture onto the Mesh2Motion armature in the `Rig`
   collection, using `assets/mocopi-to-m2m-bone-map.json`, and bakes the
   result to an action named `<capture> Retarget`. The BVH skeleton is
   hidden, not deleted, unless you untick **Keep BVH Skeleton**.
5. Smooths the baked curves, to take the sensor jitter out of the capture.
   See below.
6. Extracts root motion: the horizontal travel moves out of the hips and onto
   `DRV_root`, which the export skeleton's `root` bone copies. See below.
7. Simplifies the four IK pole targets, which are hints rather than animation
   and do not need a key on every frame. See below.
8. Optionally decimates the rest of the result, dropping every keyframe the
   curves can do without. Off by default.

Every step has a checkbox in the file browser sidebar, so you can stop after
the import, or after the prep, and drive the rest by hand.

## Root motion

The rig already has a real root bone — the export skeleton's `root` has a
Copy Transforms constraint (World/World) from `DRV_root` — but `DRV_root`
normally carries no animation, which is why all the travel ends up baked into
`pelvis`. **Extract Root Motion** moves it where it belongs, without changing
a single frame of the visible pose:

1. Sample the world path of `CTRL_Hips` over the scene frame range.
2. Smooth its XY and zero it at the first frame, so per-step sway stays in the
   hips and the clip still starts where it started.
3. Record the pose-space matrix of every direct child of `DRV_root` on every
   frame — the hips, the four IK controls and the four poles.
4. Key `DRV_root` with the path.
5. Write those matrices back. Blender re-solves each against the now-moving
   parent, so the world result is identical and the motion has simply been
   redistributed.

Step 3/5 is the part that is easy to get wrong. Every control on this rig is a
child of `DRV_root`, and after the retarget bake they all hold plain local
keyframes — animating the root without compensating travels the character
twice and slides the planted feet out from under it.

The export skeleton needs no compensation at all. Its constraints are
World/World, so `pelvis` stays pinned to `DRV_hips` whatever `root` does. On
export — glTF and FBX both sample the constrained pose — `root` carries the
travel and `pelvis` bakes down to the residual bob and sway relative to it.

Only XY moves onto the root. Vertical bob, lean and all rotation stay in the
hips, so the root never leaves the ground plane. **Smoothing** is a 0..1
slider: 0 follows the capture exactly, 1 is half a second of Gaussian blur.
0.5 removes essentially all of a walk's side-to-side sway. Travel is preserved
exactly at any setting — the filter extrapolates past the ends of the clip
rather than clamping, which would otherwise flatten the path at the start and
finish and cost real distance.

The panel button runs the same thing on an already-baked take, so you can
re-run it at a different smoothing without re-importing. It is not additive:
running it twice stacks a second path onto the first, so undo before
retrying.

## Smoothing and decimation

Three passes, deliberately separate, on whatever action the active armature is
holding. All three are panel buttons under **Cleanup** and all three are
checkboxes on the import operator. Order matters: **smooth, then root motion,
then poles, then decimate** — which is the order the import runs them in.

### Smooth Keyframes

Blurs the curve *values* along time with a Gaussian. Key count is unchanged;
this is the pass that removes shake.

The width is stated in **frames** rather than as a 0..1 factor, because that
is the number that means something: roughly half of it is the shortest motion
that survives. At 30fps, measured against a known-clean signal buried in
uniform noise:

| Smoothing | Result |
|---|---|
| 0.5 | 1.3× cleaner — barely worth the pass |
| **1.0** | 2.2× cleaner — the default. Sensor noise goes, footplants stay |
| 1.5 | 2.7× cleaner — about as far as it pays |
| 3.0 | 2.5× — past the turn; it is now removing signal as fast as noise |
| 6.0 | 0.8× — *worse* than the raw capture. The capture is a float |

Quaternion channels are handled as a unit rather than four independent
curves, for two reasons. The bake can emit `q` on one frame and `-q` on the
next — the same rotation, opposite numbers — and a component-wise blur across
that flip swings the bone through a full turn. And a blurred quaternion is no
longer unit length, which shows up as a scale on the bone. So the four curves
are un-flipped, smoothed together, and renormalised per frame.

Like the root motion filter, the kernel extrapolates past the ends of the clip
rather than clamping, so a bone still turning on the last frame keeps turning
instead of being dragged flat.

### Simplify Pole Targets

The four pole bones — `POLEARM_L/R`, `POLE_Leg_L/R` — come out of the bake with
a key on every frame of every channel, the same as any other control. Almost
none of it is doing anything.

A pole target is a hint, not animation. Blender's IK constraint reads the
pole's **position** and the pole angle; nothing anywhere reads its
orientation, and on this rig nothing is parented to a pole either. So:

- **Rotation collapses to a single keyframe.** Not decimated to a tolerance —
  collapsed, because there is nothing there to preserve.
- **Position is blurred and decimated at a much looser bound**: 4 frames and
  4mm by default, against 1 frame and 1mm for the rig proper. The pole sits
  half a metre out from the limb and only has to point the elbow or knee the
  right way, so a few millimetres of error there is invisible where the same
  number on a foot control would not be.

On a mock 300-frame capture the four poles came down from **8,400 keys to
240** — 97% — with every surviving position curve within 8.5mm of the raw
capture, most of which is the sensor noise the blur removed on purpose. With
the blur turned off (smoothing 0) the same pass gets 86%: the noise is what
was forcing the extra keys.

It runs **after** root motion for the same reason decimation does — root
motion re-keys every frame of every control hanging off `DRV_root`, poles
included — and it is on by default, since unlike decimation there is no
judgement call about what is being thrown away.

### Decimate Keyframes

Removes *keys* the curve does not need — Ramer-Douglas-Peucker, with the error
measured **vertically**: a key survives only if dropping it would move the
curve further than the tolerance at some frame. (The textbook
perpendicular-distance form mixes frames and radians into one number, which
means nothing here.)

Tolerances are in real units, so they can be reasoned about:

- **Rotation**, degrees, default **0.5** — no bone ever ends up more than half
  a degree from where it was. Quaternion channels get half of it, since a
  component is `sin(angle/2)`.
- **Position**, metres, default **0.001** — applies to the IK controls and the
  root path.

How much comes off depends entirely on how fast the bone is moving, which is
the point. Measured on synthetic 300-frame channels at 0.5°:

| Channel | Keys kept |
|---|---|
| Near-still bone | 2% |
| Drifting head | 4% |
| Swaying spine | 11% |
| Thigh through a five-step walk | 34% |

A whole mock capture — hips, head, poles, quaternion and euler and location
channels together — came down from 2,200 keys to 204, with every surviving
curve verified to sit within tolerance of the original at every dropped frame.

Surviving keys are set to auto-clamped Bezier by default; sparse keys on
LINEAR read as a series of straight segments. Channels that never move at all
collapse to a single key.

Run it **after** root motion, not before — root motion writes a key on every
frame of every control, by design.

## The bone map

`assets/mocopi-to-m2m-bone-map.json` is an explicit list of source/target
pairs — no name guessing, because both skeletons are known:

| Mocopi | Mesh2Motion | |
|---|---|---|
| `root` | `CTRL_Hips` | position |
| `torso_1` `torso_2` `torso_3` | `DRV_spine.001` `DRV_spine_002` `DRV_spine.003` | |
| `neck_2` | `DRV_neck` | |
| `head` | `DRV_head` | |
| `l_hand` `r_hand` | `CTRL_Arm_L` `CTRL_Arm_R` | position |
| `l_foot` `r_foot` | `CTRL_ILLEG_L` `CTRL_ILLEG_R` | position |
| `l_elbow_pole` `r_elbow_pole` | `POLEARM_L` `POLEARM_R` | position |
| `l_knee_pole` `r_knee_pole` | `POLE_Leg_L` `POLE_Leg_R` | position |

The four pole bones come from `add-ik-bones.py`, so step 3 has to run before
step 4.

`copy_location` per entry decides whether the target bone gets a
COPY_LOCATION constraint as well as COPY_ROTATION:

- `true` — always. IK control bones need this or the rig never moves.
- `false` — never, rotation only.
- omitted — the engine decides: bones at the top of the target hierarchy get
  position, everything below inherits it.

Bone lookup is case-insensitive. Anything in the map that does not exist on
either armature is reported as a warning and skipped rather than failing the
whole bake. The map is read from `assets/mocopi-to-m2m-bone-map.json` at import
time; edit it in the repo and rebuild.

### If the spine drifts

`DRV_spine.001` sits at the top of the mapped `DRV_` chain (its parents
`DRV_root` and `DRV_hips` are unmapped), so the engine treats it as a
hierarchy root and copies `torso_1`'s world position onto it — on top of the
position `CTRL_Hips` is already supplying. That matches what the Rokoko addon
does, so the result should look the same as before. If the spine detaches or
drifts, add `"copy_location": false` to that entry.

## Layout

```
blender-plugin/
├── build.py                      # sync sources + zip
├── dist/                         # built zips
└── mocopi_m2m_retarget/
    ├── blender_manifest.toml
    ├── NOTICE.md                 # Rokoko attribution
    ├── __init__.py               # operators + panel
    ├── retarget_engine.py        # ported Rokoko retargeter
    ├── root_motion.py            # hips travel -> DRV_root
    ├── cleanup.py                # keyframe smoothing, poles, decimation
    ├── bone_map.py               # bone map loading + validation
    ├── assets/                   # addon-owned, never overwritten
    │   ├── human-mocopi-rig-setup.blend        # edit this one in Blender
    │   ├── color-palette.png                   # texture, sits beside the .blend
    │   ├── mocopi-to-m2m-bone-map.json         # the map the addon uses
    │   └── mocopi-to-m2m-rokoko-mapping.json   # old Rokoko scheme, reference only
    └── scripts/
        ├── retarget-master.py    # maintained here
        ├── expand-arms.py        # copied by build.py
        └── add-ik-bones.py       # copied by build.py
```

## Who owns what

This matters, because getting it wrong silently destroys work — it already did
once, when a build copied an older `.blend` over a texture fix saved into the
addon.

| Files | Owner | How to edit |
|---|---|---|
| `../scripts/expand-arms.py`, `add-ik-bones.py` | **human-mocap** | Edit there, re-run `build.py` |
| `mocopi_m2m_retarget/scripts/retarget-master.py` | **the addon** | Edit in place — it resolves siblings from `__file__` |
| `mocopi_m2m_retarget/assets/*` — the `.blend`, the bone map, textures | **the addon** | Edit in place. Open `assets/human-mocopi-rig-setup.blend` in Blender and save it there |
| everything else in `mocopi_m2m_retarget/` | **the addon** | Edit in place |

The `.blend` lives in the addon because its texture paths resolve relative to
wherever the file actually sits. Editing it anywhere else and copying it in
breaks them again.

Anything dropped into `assets/` is zipped as-is — no list to update. `build.py`
prints `copied` / `kept` / `bundled` per file so it's clear which rule applied.

### The overwrite guard

`build.py` refuses to copy over a destination newer than its source:

```
BUILD FAILED

Refusing to overwrite newer file:
    ...\mocopi_m2m_retarget\scripts\add-ik-bones.py
  is newer than its source
    ...\human-mocap\scripts\add-ik-bones.py
```

That means you edited the addon's copy of a file that human-mocap owns. Move
the change back to the source, or delete the newer copy, then build again. It
also fails if an addon-owned asset has gone missing, rather than shipping a
zip without it.

## Retargeting engine

`retarget_engine.py` is a port of the Rokoko Studio Live addon's retargeter
(LGPL-3.0, © Rokoko Electronics ApS). See `mocopi_m2m_retarget/NOTICE.md` for
attribution and the list of changes. The auto-detection, naming schemes and UI
were all dropped; the mechanism — helper bones in a throwaway copy of the
source armature, constraints on the target, chunked bake, curve stitch and
cleanup — is intact.

## Blender version

Blender 4.4 introduced slotted actions and 5.0 removed `Action.fcurves`
entirely, so `retarget_engine.py` reads and writes F-curves through
`fcurves_for(action, slot)`, which uses
`bpy_extras.anim_utils.action_get_channelbag_for_slot` when it exists and
falls back to the legacy `action.fcurves` otherwise.

The chunked bake also no longer builds its final action from scratch — the
first chunk's action is kept and the later chunks are appended onto its
curves. That sidesteps creating a slot and channelbag by hand, which is the
part that differs most between versions.

5.0 also removed `Bone.select` and moved selection onto the pose bone, so
`set_bone_selected()` sets whichever one the running version exposes. This
matters more than it looks: `nla.bake(only_selected=True)` reads bone
selection, so getting it wrong bakes nothing rather than erroring. The
retarget now counts how many bones it managed to select and fails loudly if
that is zero.

## Errors

Failures report in the Blender status bar; the full traceback goes to the
system console (**Window > Toggle System Console** on Windows).
