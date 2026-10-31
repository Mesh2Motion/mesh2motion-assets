import bpy
import sys
import json

## WHAT IS THIS SCRIPT FOR?
# Normalizes an animation file so its animation starts at frame 0 (an animation rule). Every keyframe, the scene
# frame range, the action's frame range and timeline markers move by the same amount, so playback is unchanged.
# Files already starting at 0 are left alone.
#
## HOW TO RUN
# blender -b <animation.blend> --python shift-animation-to-frame-0.py -- [--save]
# Without --save it only reports what it would do. Prints one RESULT_JSON line.

argv = sys.argv[sys.argv.index('--') + 1:] if '--' in sys.argv else []
scene = bpy.context.scene
armature = next((o for o in bpy.data.objects if o.type == 'ARMATURE' and o.library is None and o.animation_data and o.animation_data.action), None)
info = {'file': bpy.data.filepath}

if armature is None:
    info['skipped'] = 'no local armature with an action'
else:
    action = armature.animation_data.action
    start = action.frame_range[0]
    info.update(action=action.name, action_range=list(action.frame_range), manual_range=action.use_frame_range,
                scene=[scene.frame_start, scene.frame_end])
    shift = -start
    if abs(shift) < 1e-6:
        info['skipped'] = 'already starts at 0'
    else:
        for layer in action.layers:
            for strip in layer.strips:
                for channelbag in strip.channelbags:
                    for fcurve in channelbag.fcurves:
                        for key in fcurve.keyframe_points:
                            key.co.x += shift
                            key.handle_left.x += shift
                            key.handle_right.x += shift
                        fcurve.update()
        if action.use_frame_range:
            action.frame_range = (start + shift, action.frame_range[1] + shift)
        scene.frame_start = int(round(scene.frame_start + shift))
        scene.frame_end = int(round(scene.frame_end + shift))
        for marker in scene.timeline_markers:
            marker.frame = int(round(marker.frame + shift))
        info.update(shift=shift, new_action_range=list(action.frame_range), new_scene=[scene.frame_start, scene.frame_end])
        if '--save' in argv:
            bpy.ops.wm.save_mainfile(compress=True)
            info['saved'] = True

print('RESULT_JSON' + json.dumps(info))
