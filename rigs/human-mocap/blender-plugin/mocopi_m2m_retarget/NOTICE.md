# Third-party code

`retarget_engine.py` is a port of the retargeting code from
**Rokoko Studio Live for Blender**, © Rokoko Electronics ApS, licensed
LGPL-3.0-or-later.

Source: https://github.com/Rokoko/rokoko-studio-live-blender
Files drawn from: `core/utils.py`, `operators/retargeting.py`

Only the retarget-and-bake machinery was taken. The bone auto-detection,
internal naming lists, custom naming schemes, scene properties and UI were
left behind — this addon retargets a known Mocopi skeleton onto a known
Mesh2Motion rig, so the bone pairs come from `assets/mocopi-to-m2m-bone-map.json`
instead of being guessed.

Changes made to the ported code:

- Bone pairs are passed in as arguments rather than read from
  `context.scene.rsl_retargeting_bone_list`.
- Bone lookup falls back to a case-insensitive match.
- Per-pair `copy_location` override, so IK control bones get a COPY_LOCATION
  constraint whether or not they are detected as hierarchy roots. The final
  fcurve merge honours the same set.
- The whole operation runs inside a VIEW_3D context override, so it works when
  invoked from a file browser operator.
- Errors raise `RetargetError` instead of calling `self.report`.
- `RETARGET_ID` renamed from `_RSL_RETARGET` to `_M2M_RETARGET`.

This addon as a whole is distributed under GPL-3.0-or-later, which LGPL-3.0
code may be combined into.
