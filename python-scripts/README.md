# Python scripts

Contains random Python scripts to help me manage the files and animations


# Blender CLI command
Make sure the blender executable is in the envirnoment variables to run the blender command. If you are on Windows, it will be somewhere like this:

    C:\Program Files\Blender Foundation\Blender 5.0


You can test to see if it works by opening a command line and typing the following:

    blender --version



## Library Override

This happened when I wanted to re-arrange the rig and animation files. Moving the rig to a different location broken all the referenced library override files. I needed a way to go into every animation file quickly and update the references.



Open the library-override-fix script file. Change the original and new locations for the file. Make sure that the Blender executable is in your environment variable so you can run the "blender" command from a command prompt. This script was originally designed to move the rig up one folder level. You will need to tweak the code if you want it to do something else. Adding this here for reference.



Navigate to the location.

    blender -b --python library-override-fix.py


## Combine animations

Go into this folder and run this command. 

    blender -b "C:\\git\\mesh2motion-assets\\rigs\\rig-human.blend" --python "C:\\git\\mesh2motion-assets\\python-scripts\\combine-and-export.py"


## Start an animation at frame 0

Animations must start at frame 0. This moves every key, the scene and action frame ranges and the timeline markers back by the same amount, so playback doesn't change. Without `--save` it only reports what it would do.

    blender -b "<animation.blend>" --python shift-animation-to-frame-0.py -- --save


## Testing animations

Good website where you can test out bones to make sure only DEF bones are getting exported as well as action names.

https://sandbox.babylonjs.com/



## Human arm IK/FK snapping

The human rig arms can be animated with IK (`CTRL_Arm_*`, `POLEARM_*`) or FK (`FK_upper_arm_*`, `FK_forearm_*`, `FK_hand_*`). The `FK_L` / `FK_R` custom properties on the armature object (Properties → Object → Custom Properties) switch between them (0 = IK, 1 = FK) and can be keyed.

Open `human-ik-fk-snap.py` in Blender's Text Editor and click Run Script. The 3D viewport's N panel gets an "M2M" tab with an FK slider per arm and "IK → FK" / "FK → IK" buttons. These match one chain to the other on the current frame, so switching doesn't pop. With "Key on snap" on, they also key the snapped controls and the switch.


## PSX characters as human rig variations

`CC0-packs/psx-lowpoly/Models/Characters_PSX.blend` has 22 characters rigged with the human skeleton. This saves each one as `rig-variations/human-psx-<name>.blend` (armature + mesh at the origin, textures linked relatively from the pack), so the export to app picks them up. Existing files are skipped unless you pass `--overwrite`; `--dry-run` only lists them.

    blender -b --python python-scripts/export-psx-variations.py -- --overwrite


## Export to app (one file per animation + manifest)

Writes every rig to `export_to_app/`: a `manifest.json` listing each rig's animations (name, tags, loop, root motion, WIP, duration, file, hash), the rig model, the skeleton alone, the rig variations, and one compressed GLB per animation. See `export-to-app/README.md`.

    cd python-scripts/export-to-app
    npm install
    node export.mjs
