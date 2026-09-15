# Mocopi to Mesh2Motion (Blender extension)

Wraps the retargeting workflow from `../README.md` into one button — import,
rig append, prep scripts, retarget and bake.

## Install

1. `python build.py` — copies the current `../scripts/*.py` into the addon
   package and writes `dist/mocopi_m2m_retarget-0.1.3.zip`.
2. In Blender 4.2+, drag the zip into the window, or
   **Edit > Preferences > Add-ons > Install from Disk**.
3. Enable **Mocopi to Mesh2Motion**.

The panel appears in the 3D viewport sidebar (`N`) under the **Mocopi** tab.
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

Every step has a checkbox in the file browser sidebar, so you can stop after
the import, or after the prep, and drive the rest by hand.

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
whole bake. **Open Bone Map** in the panel loads the JSON into Blender's text
editor; edits take effect on the next run.

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
