# Export to app

Exports every rig's animations as separate, compressed files plus a manifest, so the app can list animations
and their tags without downloading them, and load each one on demand.

## Running it

Needs Node.js and Blender (on PATH, or set the `BLENDER` environment variable to the exe).

    cd python-scripts/export-to-app
    npm install
    node export.mjs                  # every rig and the props
    node export.mjs human horse      # only these rigs; the others stay in the manifest as they were
    node export.mjs props            # only the props
    node export.mjs --skip-blender   # redo compression + manifest from the last Blender output

All 9 rigs take about a minute, the props a few seconds. The rig, variation and prop `.blend` files are opened in the
background and never saved.

## Output

```
export_to_app/
  manifest.json
  <rig>/model.glb                  rig mesh + skeleton, no animations (uncompressed)
  <rig>/skeleton.glb               deform bones only, no mesh or animations (uncompressed); the app fits it to imported meshes
  <rig>/variations/<name>.glb      the rig fitted to another mesh: mesh + skeleton, no animations (uncompressed)
  <rig>/animations/<slug>.glb      skeleton + one animation, no mesh
  props_manifest.json
  props/<name>.glb                 one prop (weapon, tool, ...) at the origin (uncompressed)
  props/preview/<name>.png         50 px transparent preview of the prop
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
      "model": { "file": "human/model.glb", "hash": "..." },
      "skeleton": { "file": "human/skeleton.glb", "hash": "..." },
      "variations": [
        { "name": "bunny", "file": "human/variations/bunny.glb", "hash": "...", "source": "rig-variations/human-bunny.blend" }
      ],
      "animations": [
        {
          "name": "Weapon_Block",                 // the action name, also the clip name inside the GLB
          "file": "human/animations/weapon-block.glb",
          "hash": "4013fce91cf586a5",             // sha256 of the file (first 16 hex); use it to bust caches
          "tags": ["combat", "melee"],
          "loop": false,                          // action.use_cyclic
          "root_motion": false,                   // _RM / -rm file or action name
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

## Props

`blender_export_props.py` opens `props/Props-MASTER.blend`. Every mesh in a collection directly under the
scene collection is a prop, and the collection name is its category (`sword`, `guns`, ...). Objects or collections
disabled for rendering are skipped. Each prop is exported alone, moved to the origin (the app mounts it by its origin),
with the same glTF settings as the rig models. The file name is the object name, so keep `.`, `:` and slashes out of
it. The previews come from `props/render_thumbnails.py`, the same script you can run by hand in Blender, so
they look the same either way.

Optional custom properties on a prop object (Object properties → Custom Properties):

- `m2m_display_name`: the name the app shows, e.g. `Heavy Sniper Rifle`. Defaults to the object name.
- `m2m_tags`: extra comma-separated tags, added after the category.

`props_manifest.json`:

```jsonc
{
  "version": 1,
  "generated": "2026-10-31T18:00:00.000Z",
  "categories": ["arrow", "axe", "bow", "...", "guns"],   // collection order in the .blend
  "props": [
    {
      "name": "AK",                                       // object name, also the node name inside the GLB
      "display_name": "AK",
      "file": "props/AK.glb",
      "hash": "c579eaef7ab0513a",
      "preview": { "file": "props/preview/AK.png", "hash": "e5830868929ee84e" },
      "category": "guns",
      "tags": ["guns"],
      "dimensions": { "width": 0.6802, "depth": 0.0735, "height": 0.3493 },   // metres, Blender X / Y / Z (Z up)
      "vertices": 1804,
      "triangles": 1128,
      "materials": ["Grey.008", "Grey2.002", "Wood.005", "DarkGrey.009"]
    }
  ]
}
```

Unchanged props export byte-identical files (GLB and PNG), so `hash` only changes when a prop does.

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
