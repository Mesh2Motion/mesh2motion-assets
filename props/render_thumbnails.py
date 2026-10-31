"""Render a small transparent PNG thumbnail of every mesh object in the file.

Run headless:  blender --background Props-MASTER.blend --python render_thumbnails.py
Or open the .blend in Blender and run this from the Text Editor.
Each object is rendered alone in a temporary scene, so the original scene is not touched.
"""
import math
import os

import bpy
from mathutils import Euler, Vector

PREVIEW_FOLDER_NAME = "preview"
THUMBNAIL_SIZE_PX = 50
FILE_EXTENSION = ".png"
RENDERABLE_TYPES = {"MESH", "CURVE", "SURFACE", "META", "FONT"}

TEMP_SCENE_NAME = "_ThumbnailScene"
RENDER_ENGINE = "CYCLES"  # CPU-friendly, works in headless mode
RENDER_SAMPLES = 32
VIEW_TRANSFORM = "Standard"  # keeps flat material colors true instead of desaturated
FRAME_PADDING = 1.1  # 1.0 = object touches the edges
CAMERA_BACK_OFF = 100.0
CAMERA_CLIP_END = 1000.0

CAMERA_ROTATION_DEG = (60.0, 0.0, 45.0)  # 3/4 view from above
SUN_ROTATION_DEG = (45.0, 0.0, 30.0)
SUN_STRENGTH = 3.0
AMBIENT_COLOR = (1.0, 1.0, 1.0, 1.0)
AMBIENT_STRENGTH = 0.6


def get_preview_dir():
    blend_dir = os.path.dirname(bpy.data.filepath)  # preview folder sits next to the .blend
    preview_dir = os.path.join(blend_dir, PREVIEW_FOLDER_NAME)
    os.makedirs(preview_dir, exist_ok=True)
    return preview_dir


def degrees_to_euler(rotation_deg):
    return Euler([math.radians(angle) for angle in rotation_deg])


def build_world():
    world = bpy.data.worlds.new(TEMP_SCENE_NAME + "_World")
    world.use_nodes = True
    background = world.node_tree.nodes["Background"]
    background.inputs["Color"].default_value = AMBIENT_COLOR
    background.inputs["Strength"].default_value = AMBIENT_STRENGTH
    return world


def build_thumbnail_scene():
    scene = bpy.data.scenes.new(TEMP_SCENE_NAME)
    scene.render.engine = RENDER_ENGINE
    scene.cycles.samples = RENDER_SAMPLES
    scene.cycles.device = "CPU"
    scene.render.resolution_x = THUMBNAIL_SIZE_PX
    scene.render.resolution_y = THUMBNAIL_SIZE_PX
    scene.render.resolution_percentage = 100
    scene.render.film_transparent = True
    scene.render.image_settings.file_format = "PNG"
    scene.render.image_settings.color_mode = "RGBA"
    scene.view_settings.view_transform = VIEW_TRANSFORM
    for stamp in ("date", "time", "render_time", "frame", "frame_range", "memory", "hostname", "camera",
                  "lens", "scene", "marker", "filename", "sequencer_strip", "note"):
        setattr(scene.render, "use_stamp_" + stamp, False)  # no metadata, so an unchanged prop gives the same PNG
    scene.world = build_world()

    camera_data = bpy.data.cameras.new(TEMP_SCENE_NAME + "_Camera")
    camera_data.type = "ORTHO"
    camera = bpy.data.objects.new(camera_data.name, camera_data)
    camera.rotation_euler = degrees_to_euler(CAMERA_ROTATION_DEG)
    scene.collection.objects.link(camera)
    scene.camera = camera

    sun_data = bpy.data.lights.new(TEMP_SCENE_NAME + "_Sun", type="SUN")
    sun_data.energy = SUN_STRENGTH
    sun = bpy.data.objects.new(sun_data.name, sun_data)
    sun.rotation_euler = degrees_to_euler(SUN_ROTATION_DEG)
    scene.collection.objects.link(sun)
    return scene


def frame_camera_on(scene, obj):
    depsgraph = scene.view_layers[0].depsgraph
    obj_eval = obj.evaluated_get(depsgraph)
    corners = [coord for corner in obj_eval.bound_box for coord in obj_eval.matrix_world @ Vector(corner)]

    camera = scene.camera
    location, ortho_scale = camera.camera_fit_coords(depsgraph, corners)
    camera.location = location
    camera.data.ortho_scale = ortho_scale * FRAME_PADDING
    camera.data.clip_end = CAMERA_CLIP_END
    backward = camera.rotation_euler.to_quaternion() @ Vector((0.0, 0.0, 1.0))  # camera looks down its local -Z
    camera.location += backward * CAMERA_BACK_OFF  # pull back so nothing is clipped


def render_object(scene, obj, preview_dir):
    scene.collection.objects.link(obj)
    try:
        scene.view_layers[0].update()
        frame_camera_on(scene, obj)
        scene.render.filepath = os.path.join(preview_dir, obj.name + FILE_EXTENSION)
        bpy.ops.render.render(write_still=True, scene=scene.name)
    finally:
        scene.collection.objects.unlink(obj)  # always hand the object back untouched
    print(f"Rendered {obj.name} -> {scene.render.filepath}")


def cleanup(scene):
    world = scene.world
    camera = scene.camera
    sun = bpy.data.objects[TEMP_SCENE_NAME + "_Sun"]
    camera_data, sun_data = camera.data, sun.data

    bpy.data.objects.remove(camera)  # only remove what this script created
    bpy.data.objects.remove(sun)
    bpy.data.cameras.remove(camera_data)
    bpy.data.lights.remove(sun_data)
    bpy.data.scenes.remove(scene)
    bpy.data.worlds.remove(world)


def main():
    original_scene = bpy.context.scene
    objects = [obj for obj in original_scene.objects if obj.type in RENDERABLE_TYPES]
    preview_dir = get_preview_dir()
    scene = build_thumbnail_scene()
    try:
        for obj in objects:
            render_object(scene, obj, preview_dir)
    finally:
        cleanup(scene)

    print(f"Done: {len(objects)} thumbnails rendered to {preview_dir}")


# python-scripts/export-to-app/blender_export_props.py imports this file to reuse the thumbnail scene
if __name__ == "__main__":
    main()
