// Exports every rig for the Mesh2Motion app into <repo>/export_to_app:
//   manifest.json                   every rig and its animations (name, tags, frames, file, hash, ...)
//   <rig>/model.glb                 rig mesh + skeleton, no animations
//   <rig>/skeleton.glb              deform bones only, no mesh or animations (fitted to custom meshes in the app)
//   <rig>/animations/<slug>.glb     one animation each: skeleton + clip, no mesh, meshopt-compressed
//   props_manifest.json             every prop (weapons, tools, ...): name, category, tags, size, file, preview, hash
//   props/<name>.glb                one prop each, at the origin, from props/Props-MASTER.blend
//   props/preview/<name>.png        a small transparent preview of each prop
//
// Usage (from this folder, after `npm install`):
//   node export.mjs                 all rigs and the props
//   node export.mjs human horse     only these rigs (the other rigs already in the manifest are kept)
//   node export.mjs props           only the props
//   node export.mjs --skip-blender  re-run compression + manifest on the last Blender output
//
// Blender must be on PATH (or set BLENDER to its exe). The rig and prop .blend files are never saved.

import { spawn } from 'node:child_process';
import { createHash } from 'node:crypto';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { Logger, NodeIO, PropertyType } from '@gltf-transform/core';
import { ALL_EXTENSIONS, EXTMeshoptCompression } from '@gltf-transform/extensions';
import { dedup, prune, resample } from '@gltf-transform/functions';
import { MeshoptDecoder, MeshoptEncoder } from 'meshoptimizer';

const scriptDir = path.dirname(fileURLToPath(import.meta.url));
const repoDir = path.resolve(scriptDir, '..', '..');
const rigsDir = path.join(repoDir, 'rigs');
const outDir = path.join(repoDir, 'export_to_app');
const stagingDir = path.join(os.tmpdir(), 'm2m-export-to-app-staging');
const propsBlend = path.join(repoDir, 'props', 'Props-MASTER.blend');
const blender = process.env.BLENDER || 'blender';
const MANIFEST_VERSION = 1;
const PROPS_MANIFEST_VERSION = 1;

const args = process.argv.slice(2);
const skipBlender = args.includes('--skip-blender');
const allRigs = fs.readdirSync(rigsDir)
  .filter(f => /^rig-[a-z0-9-]+\.blend$/.test(f))
  .map(f => f.slice('rig-'.length, -'.blend'.length))
  .filter(rig => fs.existsSync(path.join(rigsDir, rig)));
const requested = args.filter(a => !a.startsWith('--'));
const unknown = requested.filter(r => r !== 'props' && !allRigs.includes(r));
if (unknown.length) {
  console.error(`Unknown rig(s): ${unknown.join(', ')}. Rigs: ${allRigs.join(', ')}, or props`);
  process.exit(1);
}
const rigs = requested.length ? requested.filter(r => r !== 'props') : allRigs;
const doProps = !requested.length || requested.includes('props');

// Runs one background Blender export. `rig` names the log; `blendArgs` are the arguments before --python
function runBlender (rig, script, blendArgs = [], scriptArgs = []) {
  const logPath = path.join(stagingDir, `${rig}.log`);
  return new Promise((resolve, reject) => {
    const child = spawn(blender, [
      '-b', ...blendArgs,
      '--python', path.join(scriptDir, script),
      '--', '--out', stagingDir, ...scriptArgs
    ]);
    let output = '';
    child.stdout.on('data', d => { output += d; });
    child.stderr.on('data', d => { output += d; });
    child.on('error', reject);
    child.on('close', code => {
      fs.writeFileSync(logPath, output);
      const result = output.split('\n').find(line => line.startsWith('RESULT_JSON'));
      if (code !== 0 || !result) {
        reject(new Error(`Blender export failed for ${rig} (exit ${code}). Log: ${logPath}`));
        return;
      }
      for (const line of output.split('\n')) {
        // The glTF exporter's own warnings are prefixed with a timestamp, so only this repo's scripts' warnings match
        if (line.startsWith('WARNING: ') && !line.includes('Baking animation')) console.log(`  [${rig}] ${line.trim()}`);
      }
      resolve(JSON.parse(result.slice('RESULT_JSON'.length)));
    });
  });
}

const io = new NodeIO()
  .setLogger(new Logger(Logger.Verbosity.WARN))
  .registerExtensions(ALL_EXTENSIONS)
  .registerDependencies({ 'meshopt.encoder': MeshoptEncoder, 'meshopt.decoder': MeshoptDecoder });

// Largest error the compression may add to any bone, on any frame of the original export
const MAX_ROTATION_ERROR_DEG = 0.25;
const MAX_TRANSLATION_ERROR_M = 0.003;

// The exporter bakes translation, rotation and scale for every bone, even the ones that never leave the rest pose.
// A channel that holds the bone's rest value on every key changes nothing, so remove it (most of the file's size
// is the JSON describing channels, not the key data).
function removeRestChannels () {
  return (doc) => {
    const eps = 1e-5;
    for (const animation of doc.getRoot().listAnimations()) {
      for (const channel of animation.listChannels()) {
        const node = channel.getTargetNode();
        const sampler = channel.getSampler();
        const output = sampler?.getOutput();
        const restGetter = { translation: 'getTranslation', rotation: 'getRotation', scale: 'getScale' }[channel.getTargetPath()];
        if (!node || !output || !restGetter) continue;
        const rest = node[restGetter]();
        const size = output.getElementSize();
        const value = [];
        let atRest = true;
        for (let i = 0; i < output.getCount() && atRest; i++) {
          output.getElement(i, value);
          const same = value.every((v, k) => Math.abs(v - rest[k]) < eps);
          // q and -q are the same rotation
          const negated = size === 4 && value.every((v, k) => Math.abs(v + rest[k]) < eps);
          atRest = same || negated;
        }
        if (atRest) {
          channel.dispose();
          if (sampler.listParents().every(p => p === animation || p.propertyType === 'Root')) sampler.dispose();
        }
      }
    }
  };
}

// Animation-only GLB: drop redundant keys (the exporter bakes every frame) and channels stuck at the rest pose,
// share identical accessors, then meshopt-compress. The "filter" method stores rotations as 16-bit quaternions and
// translations as 12-bit exponential floats, so the app needs MeshoptDecoder
// (three.js: gltfLoader.setMeshoptDecoder(MeshoptDecoder)).
async function writeAnimation (src, dest) {
  const doc = await io.read(src);
  await doc.transform(
    resample({ tolerance: 1e-4 }),
    removeRestChannels(),
    prune({ propertyTypes: [PropertyType.ACCESSOR, PropertyType.ANIMATION_SAMPLER] }),
    dedup()
  );
  doc.createExtension(EXTMeshoptCompression)
    .setRequired(true)
    .setEncoderOptions({ method: EXTMeshoptCompression.EncoderMethod.FILTER });
  await io.write(dest, doc);
}

// Each bone's local [translation, rotation, scale] on every frame time of the original export.
// Bones without a channel stay at their rest value.
function sampleBones (doc, times) {
  const animation = doc.getRoot().listAnimations()[0];
  const result = new Map();
  for (const node of doc.getRoot().listNodes()) {
    result.set(node.getName(), times.map(() => ({ translation: node.getTranslation(), rotation: node.getRotation(), scale: node.getScale() })));
  }
  for (const channel of animation?.listChannels() ?? []) {
    const sampler = channel.getSampler();
    const input = sampler.getInput().getArray();
    const output = sampler.getOutput();
    const frames = result.get(channel.getTargetNode().getName());
    times.forEach((t, f) => {
      let i = 0;
      while (i < input.length - 1 && input[i + 1] <= t) i++;
      const a = output.getElement(i, []);
      if (i === input.length - 1 || sampler.getInterpolation() === 'STEP') {
        frames[f][channel.getTargetPath()] = a;
        return;
      }
      const b = output.getElement(i + 1, []);
      const s = (t - input[i]) / (input[i + 1] - input[i]);
      const sign = a.length === 4 && a.reduce((d, v, k) => d + v * b[k], 0) < 0 ? -1 : 1;
      let v = a.map((x, k) => x + (sign * b[k] - x) * s);
      if (a.length === 4) {
        const len = Math.hypot(...v);
        v = v.map(x => x / len);
      }
      frames[f][channel.getTargetPath()] = v;
    });
  }
  return result;
}

// Compares the compressed file with Blender's output on every frame, returns the worst rotation (deg) and translation (m) error
async function measureError (rawPath, compressedPath) {
  const raw = await io.read(rawPath);
  const compressed = await io.read(compressedPath);
  const rawSampler = raw.getRoot().listAnimations()[0]?.listSamplers()[0];
  const times = rawSampler ? Array.from(rawSampler.getInput().getArray()) : [0];
  const a = sampleBones(raw, times);
  const b = sampleBones(compressed, times);
  let rotation = 0;
  let translation = 0;
  for (const [name, framesA] of a) {
    const framesB = b.get(name);
    if (!framesB) throw new Error(`bone ${name} missing after compression`);
    framesA.forEach((fa, f) => {
      const fb = framesB[f];
      // 16-bit quaternions come back very slightly off unit length, so compare directions only
      const dot = Math.min(1, Math.abs(fa.rotation.reduce((d, v, k) => d + v * fb.rotation[k], 0)) /
        (Math.hypot(...fa.rotation) * Math.hypot(...fb.rotation)));
      rotation = Math.max(rotation, 2 * Math.acos(dot) * 180 / Math.PI);
      translation = Math.max(translation, Math.hypot(...fa.translation.map((v, k) => v - fb.translation[k])));
    });
  }
  return { rotation, translation };
}

// The model is left uncompressed so any glTF viewer (and the app as it is today) can load it.
async function writeModel (src, dest) {
  const doc = await io.read(src);
  await doc.transform(dedup());
  await io.write(dest, doc);
}

function clipDuration (doc) {
  const samplers = doc.getRoot().listAnimations()[0]?.listSamplers() ?? [];
  return Math.max(0, ...samplers.map(s => s.getInput().getMax([])[0]));
}

function fileInfo (filePath) {
  return { hash: createHash('sha256').update(fs.readFileSync(filePath)).digest('hex').slice(0, 16) };
}

// Each bone's name and parent, in the order of the skin's joints
async function skinBones (filePath) {
  const doc = await io.read(filePath);
  const joints = doc.getRoot().listSkins()[0]?.listJoints() ?? [];
  return new Map(joints.map(j => [j.getName(), j.getParentNode()?.getName() ?? null]));
}

// The rig variations (rig-variations/<rig>-<name>.blend): mesh + skeleton, no animations, uncompressed like the model.
// Warns when a variation's bones don't match the rig's skeleton, since the rig's animations won't fit it then.
async function exportVariations (rig, stageRigDir, rigOutDir) {
  const variationsPath = path.join(stagingDir, 'variations.json');
  const staged = fs.existsSync(variationsPath) ? JSON.parse(fs.readFileSync(variationsPath, 'utf8'))[rig] ?? [] : [];
  if (!staged.length) return [];
  fs.mkdirSync(path.join(rigOutDir, 'variations'), { recursive: true });
  const rigBones = await skinBones(path.join(rigOutDir, 'skeleton.glb'));
  const variations = [];
  for (const variation of staged) {
    const dest = path.join(rigOutDir, variation.file);
    await writeModel(path.join(stageRigDir, variation.file), dest);
    const bones = await skinBones(dest);
    const missing = [...rigBones.keys()].filter(b => !bones.has(b));
    const extra = [...bones.keys()].filter(b => !rigBones.has(b));
    const reparented = [...bones.keys()].filter(b => rigBones.has(b) && rigBones.get(b) !== bones.get(b));
    if (missing.length || extra.length || reparented.length) {
      console.log(`  [${rig}] WARNING: variation ${variation.name} bones differ from the rig skeleton (${variation.source}):` +
        (missing.length ? ` missing ${missing.join(', ')};` : '') +
        (extra.length ? ` extra ${extra.join(', ')};` : '') +
        (reparented.length ? ` different parent ${reparented.join(', ')};` : ''));
    }
    variations.push({ name: variation.name, file: `${rig}/${variation.file}`, ...fileInfo(dest), source: variation.source });
  }
  return variations;
}

async function exportRig (rig) {
  const stageRigDir = path.join(stagingDir, rig);
  const rigInfo = JSON.parse(fs.readFileSync(path.join(stageRigDir, 'rig.json'), 'utf8'));
  const rigOutDir = path.join(outDir, rig);
  fs.rmSync(rigOutDir, { recursive: true, force: true }); // removes animations that no longer exist
  fs.mkdirSync(path.join(rigOutDir, 'animations'), { recursive: true });

  await writeModel(path.join(stageRigDir, rigInfo.model), path.join(rigOutDir, 'model.glb'));
  await writeModel(path.join(stageRigDir, rigInfo.skeleton), path.join(rigOutDir, 'skeleton.glb'));
  const variations = await exportVariations(rig, stageRigDir, rigOutDir);
  let rawBytes = 0;
  let outBytes = 0;
  let worstRotation = 0;
  let worstTranslation = 0;
  const animations = [];
  for (const anim of rigInfo.animations) {
    const src = path.join(stageRigDir, anim.file);
    const dest = path.join(rigOutDir, anim.file);
    await writeAnimation(src, dest);
    const error = await measureError(src, dest);
    if (error.rotation > MAX_ROTATION_ERROR_DEG || error.translation > MAX_TRANSLATION_ERROR_M) {
      console.log(`  [${rig}] WARNING: ${anim.name} compression error ${error.rotation.toFixed(3)} deg, ${(error.translation * 1000).toFixed(2)} mm`);
    }
    worstRotation = Math.max(worstRotation, error.rotation);
    worstTranslation = Math.max(worstTranslation, error.translation);
    const info = fileInfo(dest);
    // The exporter ends a clip at its last key that changes anything, which can be shorter than the action's frame range
    const duration = clipDuration(await io.read(src));
    if (Math.abs(duration - anim.duration) > 0.5 / rigInfo.fps) {
      console.log(`  [${rig}] WARNING: ${anim.name} clip is ${duration.toFixed(3)} s but its frame range is ${anim.duration} s (${anim.source})`);
    }
    rawBytes += fs.statSync(src).size;
    outBytes += fs.statSync(dest).size;
    animations.push({
      name: anim.name,
      file: `${rig}/${anim.file}`,
      hash: info.hash,
      tags: anim.tags,
      loop: anim.loop,
      root_motion: anim.root_motion,
      frame_count: Math.round(duration * rigInfo.fps) + 1,
      duration: Math.round(duration * 1e4) / 1e4,
      source: anim.source
    });
  }

  const names = animations.map(a => a.name);
  const duplicates = [...new Set(names.filter((n, i) => names.indexOf(n) !== i))];
  if (duplicates.length) console.log(`  [${rig}] WARNING: duplicate animation names: ${duplicates.join(', ')}`);
  console.log(`  [${rig}] ${animations.length} animations, ${(rawBytes / 1e6).toFixed(2)} MB -> ${(outBytes / 1e6).toFixed(2)} MB` +
    `, worst compression error ${worstRotation.toFixed(3)} deg / ${(worstTranslation * 1000).toFixed(2)} mm`);

  return {
    fps: rigInfo.fps,
    model: { file: `${rig}/model.glb`, ...fileInfo(path.join(rigOutDir, 'model.glb')) },
    skeleton: { file: `${rig}/skeleton.glb`, ...fileInfo(path.join(rigOutDir, 'skeleton.glb')) },
    variations,
    animations
  };
}

// The props from Props-MASTER.blend: one GLB each, uncompressed like the rig models, plus its preview PNG
async function exportProps () {
  const stagePropsDir = path.join(stagingDir, 'props');
  const staged = JSON.parse(fs.readFileSync(path.join(stagePropsDir, 'props.json'), 'utf8'));
  const propsOutDir = path.join(outDir, 'props');
  fs.rmSync(propsOutDir, { recursive: true, force: true }); // removes props that no longer exist
  fs.mkdirSync(path.join(propsOutDir, 'preview'), { recursive: true });
  const props = [];
  for (const prop of staged) {
    const dest = path.join(propsOutDir, prop.file);
    const previewDest = path.join(propsOutDir, prop.preview);
    await writeModel(path.join(stagePropsDir, prop.file), dest);
    fs.copyFileSync(path.join(stagePropsDir, prop.preview), previewDest);
    const { name, display_name: displayName, file, preview, ...details } = prop;
    props.push({
      name,
      display_name: displayName,
      file: `props/${file}`,
      ...fileInfo(dest),
      preview: { file: `props/${preview}`, ...fileInfo(previewDest) },
      ...details
    });
  }
  const categories = [...new Set(props.map(p => p.category))];
  console.log(`  [props] ${props.length} props in ${categories.length} categories`);
  return { version: PROPS_MANIFEST_VERSION, generated: new Date().toISOString(), categories, props };
}

await Promise.all([MeshoptEncoder.ready, MeshoptDecoder.ready]);
fs.mkdirSync(outDir, { recursive: true });

if (!skipBlender) {
  for (const rig of rigs) fs.rmSync(path.join(stagingDir, rig), { recursive: true, force: true });
  if (doProps) fs.rmSync(path.join(stagingDir, 'props'), { recursive: true, force: true });
  fs.mkdirSync(stagingDir, { recursive: true });
  console.log(`Blender export: ${[...rigs, ...(doProps ? ['props'] : [])].join(', ')}`);
  // A few Blender processes at a time; each one loads a rig and appends every animation file of it
  const queue = [...rigs];
  const workers = Array.from({ length: Math.min(3, queue.length) }, async () => {
    while (queue.length) {
      const rig = queue.shift();
      const result = await runBlender(rig, 'blender_export_rig.py', [path.join(rigsDir, `rig-${rig}.blend`)]);
      console.log(`  [${rig}] ${result.animations} animations exported from Blender`);
    }
  });
  // The variations are small, so one Blender process opens each of them in turn
  const variations = rigs.length && runBlender('variations', 'blender_export_variations.py', [], ['--rigs', ...rigs]).then(result => {
    for (const [rig, count] of Object.entries(result)) console.log(`  [${rig}] ${count} variations exported from Blender`);
  });
  // The props render their previews too, which takes a few seconds
  const props = doProps && runBlender('props', 'blender_export_props.py', [propsBlend]).then(result => {
    console.log(`  [props] ${result.props} props exported from Blender`);
  });
  await Promise.all([...workers, variations, props]);
}

console.log('Compressing and writing export_to_app');
if (rigs.length) {
  const manifestPath = path.join(outDir, 'manifest.json');
  const previous = fs.existsSync(manifestPath) ? JSON.parse(fs.readFileSync(manifestPath, 'utf8')) : { rigs: {} };
  const manifest = { version: MANIFEST_VERSION, generated: new Date().toISOString(), rigs: { ...previous.rigs } };
  for (const rig of rigs) manifest.rigs[rig] = await exportRig(rig);
  manifest.rigs = Object.fromEntries(Object.entries(manifest.rigs).sort(([a], [b]) => a.localeCompare(b)));
  fs.writeFileSync(manifestPath, JSON.stringify(manifest, null, 2) + '\n');
  console.log(`Wrote ${path.relative(repoDir, manifestPath)}`);
}
if (doProps) {
  const propsManifestPath = path.join(outDir, 'props_manifest.json');
  fs.writeFileSync(propsManifestPath, JSON.stringify(await exportProps(), null, 2) + '\n');
  console.log(`Wrote ${path.relative(repoDir, propsManifestPath)}`);
}
