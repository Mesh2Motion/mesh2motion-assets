"""Force the addon's sidebar panel to actually draw, in a real UI.

Background mode never calls ``draw()``, so a bad icon name, a bad property
name, a ``bl_category`` typo or a bad operator-property assignment in the panel
all go unnoticed by the other three suites -- and any of them raises at draw
time and takes the whole sidebar with it. This one opens a window, selects the
Mesh2Motion tab, populates the offset fields the way a user would, and forces a
redraw with draw errors collected rather than swallowed.

**This is the one suite that needs a display**, so it does not run under
``-b``. On a desktop just run it; on a headless box use a virtual display::

    blender --factory-startup --python tests/test_panel_draw.py
    xvfb-run -a blender --factory-startup --python tests/test_panel_draw.py

Exits non-zero on failure. It builds its own throwaway rig.
"""

import os
import sys
import traceback

import bpy

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import mocopi_m2m_retarget as addon  # noqa: E402

addon.register()
print("registered")

# Something for the operators to poll against.
bpy.ops.object.armature_add(enter_editmode=True)
armature = bpy.context.object
armature.data.edit_bones[0].name = "DRV_root"
bpy.ops.object.mode_set(mode="OBJECT")
armature.animation_data_create()
armature.animation_data.action = bpy.data.actions.new("A")
for bone in armature.pose.bones:
    bone.keyframe_insert("location", frame=1)

# Type something into the panel's fields, the way the user would.
bpy.context.scene.m2m_offset = (0.0, 0.0, -0.2)

panel = bpy.types.M2M_PT_mocopi_panel
errors = []
drawn = {"count": 0}

original_draw = panel.draw


def counting_draw(self, context):
    try:
        original_draw(self, context)
        drawn["count"] += 1
    except Exception:
        errors.append(traceback.format_exc())


panel.draw = counting_draw

area = None
for window in bpy.context.window_manager.windows:
    for candidate in window.screen.areas:
        if candidate.type == "VIEW_3D":
            area = candidate
            break

if area is None:
    print("FAIL no VIEW_3D area -- is this really a UI session?")
    sys.exit(1)

for region in area.regions:
    if region.type == "UI":
        break
else:
    with bpy.context.temp_override(area=area):
        bpy.ops.wm.context_toggle(data_path="space_data.show_region_ui")

for space in area.spaces:
    if space.type == "VIEW_3D":
        space.show_region_ui = True


def pump(times=4):
    for _ in range(times):
        bpy.ops.wm.redraw_timer(type="DRAW_WIN_SWAP", iterations=1)


# The sidebar's tab list does not exist until the region has drawn once, so
# the category can only be selected after a first pump.
area.tag_redraw()
pump()

sidebar = [r for r in area.regions if r.type == "UI"][0]
sidebar.active_panel_category = "Mesh2Motion"
print("active tab: {!r}".format(sidebar.active_panel_category))

if sidebar.active_panel_category != "Mesh2Motion":
    print("FAIL could not select the Mesh2Motion tab -- bl_category mismatch?")
    sys.exit(1)

drawn["count"] = 0
del errors[:]
area.tag_redraw()
pump()

print("draw() ran {} time(s)".format(drawn["count"]))

if errors:
    print("FAIL panel raised while drawing:")
    for text in errors:
        print(text)
    sys.exit(1)

if drawn["count"] == 0:
    print("FAIL panel never drew -- the sidebar tab was not visible")
    sys.exit(1)

print("ok   sidebar tab is named Mesh2Motion")
print("ok   panel drew cleanly with the offset fields populated")
print("\n2 checks, 0 failures")
sys.exit(0)
