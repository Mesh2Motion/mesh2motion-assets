# Export to app

Exports every rig's animations as separate, compressed files plus a manifest, so the app can list animations
and their tags without downloading them, and load each one on demand.

## Running it

Needs Node.js and Blender (on PATH, or set the `BLENDER` environment variable to the exe).

    cd python-scripts/export-to-app
    npm install
    node export.mjs                  # every rig
    node export.mjs human horse      # only these rigs; the others stay in the manifest as they were
    node export.mjs --skip-blender   # redo compression + manifest from the last Blender output

All 9 rigs take about a minute. The rig and variation `.blend` files are opened in the background and never saved.

## Output

```
export_to_app/
  manifest.json
  <rig>/model.glb                  rig mesh + skeleton, no animations (uncompressed)
  <rig>/skeleton.glb               deform bones only, no mesh or animations (uncompressed); the app fits it to imported meshes
  <rig>/variations/<name>.glb      the rig fitted to another mesh: mesh + skeleton, no animations (uncompressed)
  <rig>/animations/<slug>.glb      skeleton + one animation, no mesh
```

Variations come from `rig-variations/<rig>-<name>.blend` (`blender_export_variations.py`). The file name prefix is the
rig, except `fish-*`, which uses the shark rig. The export warns when a variation's bones don't match the rig's
skeleton, or a texture it uses is missing.

`manifest.json`:

```jsonc
{
  "version": 1,
  "generated": "2026-10-31T15:10:00.000Z",
  "rigs": {
    "human": {
      "fps": 24,
      "model": { "file": "human/model.glb", "bytes": 1234, "hash": "..." },
      "skeleton": { "file": "human/skeleton.glb", "bytes": 1234, "hash": "..." },
      "variations": [
        { "name": "bunny", "file": "human/variations/bunny.glb", "bytes": 1234, "hash": "...", "source": "rig-variations/human-bunny.blend" }
      ],
      "animations": [
        {
          "name": "Weapon_Block",                 // the action name, also the clip name inside the GLB
          "file": "human/animations/weapon-block.glb",
          "hash": "4013fce91cf586a5",             // sha256 of the file (first 16 hex); use it to bust caches
          "bytes": 35916,
          "tags": ["combat", "melee"],
          "loop": false,                          // action.use_cyclic
          "root_motion": false,                   // _RM / -rm file or action name
          "wip": false,                           // WIP- file prefix
          "frame_count": 37,
          "duration": 1.5,                        // seconds, same as the clip's duration in three.js
          "source": "rigs/human/combat/melee/animation-human-weapon-block.blend"
        }
      ]
    }
  }
}
```

File paths are relative to `manifest.json`. File names are stable between exports, so `hash` changes when an
animation changes but its URL doesn't.

### Tags

- The folder an animation file sits in, relative to its rig folder, gives its tags: `rigs/human/combat/melee/...`
  is tagged `combat`, `melee`.
- Extra tags: add a custom property `m2m_tags` to the action (Action properties → Custom Properties), as a
  comma-separated string such as `sword, two handed`. Tags are lower-case kebab-case.

## What happens to each animation

1. `blender_export_rig.py` (one Blender process per rig) appends every `animation*.blend` in the rig folder and its
   sub-folders, the same set as `combine-and-export.py`, and exports each action alone with the same glTF settings
   (deform bones only, Y-up, baked constraints). It also keeps that script's fixes: unkeyed custom properties are
   pinned to their rig defaults, a loop's repeated last frame is dropped, and clips are slid to start at 0 s.
2. `export.mjs` then, for every GLB:
   - removes keys that linear interpolation already reproduces (the exporter bakes every frame),
   - removes channels that hold a bone at its rest pose for the whole clip,
   - compresses with `EXT_meshopt_compression` (16-bit quaternions, 12-bit translations),
   - re-reads the result and compares it with Blender's output on every frame. It warns if any bone is off by more
     than 0.25° or 3 mm. Today the worst cases are 0.19° (human Spin_Death) and 2.6 mm (human Land_Three_Point).
   - warns when a clip ends before the action's frame range does (the exporter stops at the last key that changes
     anything).

Raw Blender output and logs are kept in `%TEMP%\m2m-export-to-app-staging`.

## Loading in three.js

The animation files need the meshopt decoder:

```js
import { GLTFLoader } from 'three/addons/loaders/GLTFLoader.js';
import { MeshoptDecoder } from 'three/addons/libs/meshopt_decoder.module.js';

const loader = new GLTFLoader().setMeshoptDecoder(MeshoptDecoder);
const clip = (await loader.loadAsync(`${base}/${anim.file}`)).animations[0];
```

Bones that a clip leaves at rest have no track. three.js leaves untracked bones as they are, so reset the skeleton
to its rest pose (`skeleton.pose()`) when switching clips.
