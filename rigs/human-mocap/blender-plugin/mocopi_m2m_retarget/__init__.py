"""Mocopi to Mesh2Motion.

Blender extension that collapses the manual Mocopi -> Mesh2Motion retargeting
workflow into a single button:

  1. Pick a Mocopi BVH capture and import it at 0.01 scale.
  2. Append the reference IK rig collections from the bundled .blend.
  3. Re-select the freshly imported BVH skeleton and run retarget-master.py,
     which expands the arms and builds the elbow / knee pole bones.
  4. Retarget the capture onto the Mesh2Motion rig using an explicit bone map
     and bake the result.
  5. Optionally smooth the baked curves, to take the sensor jitter out of
     the capture without changing its timing.
  6. Lift the horizontal travel out of the hips and onto DRV_root, so the
     export skeleton's root bone carries it. Off by default -- it is not
     additive, so it is left to a deliberate choice.
  7. Optionally simplify the four IK pole targets, which are hints rather than
     animation and do not need a key on every frame.
  8. Decimate the result, dropping every keyframe the curves can do without
     inside a stated error bound. One round runs on import by default.

Step 4 uses a retargeting engine ported from the Rokoko Studio Live addon --
see retarget_engine.py for the attribution and for what changed.
"""

import os
import traceback

import bpy
from bpy.props import BoolProperty, EnumProperty, FloatProperty, StringProperty
from bpy.types import Operator, Panel
from bpy_extras.io_utils import ImportHelper

from . import bone_map as bone_map_loader
from . import cleanup
from . import retarget_engine
from . import root_motion


# ----------------------------------------------------------------------
# Bundled asset locations
# ----------------------------------------------------------------------

ADDON_DIR = os.path.dirname(os.path.abspath(__file__))
ASSETS_DIR = os.path.join(ADDON_DIR, "assets")
SCRIPTS_DIR = os.path.join(ADDON_DIR, "scripts")

REFERENCE_RIG_BLEND = os.path.join(ASSETS_DIR, "human-mocopi-rig-setup.blend")
BONE_MAP_JSON = os.path.join(ASSETS_DIR, "mocopi-to-m2m-bone-map.json")
RETARGET_MASTER = os.path.join(SCRIPTS_DIR, "retarget-master.py")

# Collections pulled out of the reference .blend. Appended, never linked:
# library overrides cannot enter edit mode.
REFERENCE_COLLECTIONS = ("Rig", "Custom Bone Shapes")

# The collection holding the Mesh2Motion armature.
TARGET_COLLECTION = "Rig"


# ----------------------------------------------------------------------
# Cleanup wording
#
# The smoothing and decimation settings appear in three places -- the import
# operator and the two panel operators -- and a tooltip that disagrees with
# itself is worse than no tooltip.
# ----------------------------------------------------------------------

SMOOTH_AMOUNT_DESCRIPTION = (
    "Width of the blur, in frames. Roughly half of it is the shortest motion "
    "that survives, so 1 removes sensor noise, 3 softens footplants and 6 "
    "turns a capture into a float"
)

DECIMATE_ROTATION_DESCRIPTION = (
    "How far a bone may end up from where it was, in degrees, for a keyframe "
    "to be worth dropping. Half a degree is invisible and clears most of the "
    "curve; past about 2 fast limbs start to look stepped"
)

DECIMATE_LOCATION_DESCRIPTION = (
    "How far a bone may end up from where it was, in metres, for a keyframe "
    "to be worth dropping. Applies to the IK controls and the root path"
)

POLE_SMOOTH_DESCRIPTION = (
    "Width of the blur applied to the pole targets, in frames. A pole only "
    "has to point the elbow or knee roughly the right way, so it takes far "
    "more blur than a real bone: 6 flattens the per-step wobble out of it"
)

POLE_TOLERANCE_DESCRIPTION = (
    "How far a pole target may end up from where it was, in metres, for a "
    "keyframe to be worth dropping. The poles sit half a metre out from the "
    "limb, so a centimetre there is nothing"
)

POLE_SIMPLIFY_DESCRIPTION = (
    "Thin out the four IK pole targets. Their rotation collapses to a single "
    "keyframe -- Blender's IK reads a pole's position and the pole angle, "
    "never its orientation -- and their position is blurred and decimated at "
    "a much looser bound than the rest of the rig. Runs after root motion"
)

CHANNEL_ITEMS = [
    ("ALL", "All", "Position and rotation"),
    ("ROTATION", "Rotation", "Rotation channels only"),
    ("LOCATION", "Location", "Position channels only"),
]

# Shared by all three passes that remove keys. Sparse keys left on LINEAR read
# as a series of straight segments, which looks worse than what was removed.
INTERPOLATION_ITEMS = [
    (
        "BEZIER",
        "Bezier",
        "Auto-clamped Bezier. Sparse keys read as motion rather than "
        "as a series of straight segments",
    ),
    ("LINEAR", "Linear", "Straight lines between keys"),
    ("KEEP", "Keep", "Leave the existing interpolation alone"),
]


# ----------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------

def ensure_object_mode():
    """Drop back to object mode so operators and appends behave."""
    if bpy.context.object is not None and bpy.context.mode != "OBJECT":
        bpy.ops.object.mode_set(mode="OBJECT")


def import_bvh(filepath, global_scale, update_scene_fps, update_scene_duration):
    """Import a BVH and return the armature it created, or None."""
    before = set(bpy.context.scene.objects)

    bpy.ops.import_anim.bvh(
        filepath=filepath,
        target="ARMATURE",
        global_scale=global_scale,
        use_fps_scale=False,
        update_scene_fps=update_scene_fps,
        update_scene_duration=update_scene_duration,
        rotate_mode="NATIVE",
    )

    created = [
        obj
        for obj in bpy.context.scene.objects
        if obj not in before and obj.type == "ARMATURE"
    ]

    if not created:
        return None

    active = bpy.context.view_layer.objects.active
    return active if active in created else created[0]


def append_reference_collections(names):
    """Append the named collections from the bundled rig .blend.

    Returns (appended, skipped, missing). Collections already linked into the
    scene are skipped, so repeat imports do not pile up 'Rig.001'.
    """
    scene_children = {child.name for child in bpy.context.scene.collection.children}

    wanted = [name for name in names if name not in scene_children]
    skipped = [name for name in names if name in scene_children]

    if not wanted:
        return [], skipped, []

    with bpy.data.libraries.load(REFERENCE_RIG_BLEND, link=False) as (src, dst):
        available = [name for name in wanted if name in src.collections]
        missing = [name for name in wanted if name not in src.collections]
        dst.collections = available

    appended = []
    for collection in dst.collections:
        if collection is None:
            continue
        bpy.context.scene.collection.children.link(collection)
        appended.append(collection.name)

    return appended, skipped, missing


def find_target_armature(collection_name=TARGET_COLLECTION):
    """Find the Mesh2Motion armature inside the appended rig collection."""
    collection = bpy.context.scene.collection.children.get(collection_name)

    if collection is None:
        collection = bpy.data.collections.get(collection_name)

    if collection is None:
        return None

    armatures = [obj for obj in collection.all_objects if obj.type == "ARMATURE"]

    if not armatures:
        return None

    for obj in armatures:
        if obj.name.lower().startswith("rig"):
            return obj

    return armatures[0]


def select_only(obj):
    """Make obj the one selected, active object."""
    ensure_object_mode()
    bpy.ops.object.select_all(action="DESELECT")
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj


def run_script(path):
    """Execute a bundled script as if it were run from the text editor."""
    with open(path, "r", encoding="utf-8") as handle:
        source = handle.read()

    namespace = {"__name__": "__main__", "__file__": path}
    exec(compile(source, path, "exec"), namespace)


# ----------------------------------------------------------------------
# Operator
# ----------------------------------------------------------------------

class M2M_OT_load_mocopi_bvh(Operator, ImportHelper):
    """Import a Mocopi BVH capture, append the reference IK rig, run the
    retarget prep scripts and retarget onto the Mesh2Motion rig"""

    bl_idname = "m2m.load_mocopi_bvh"
    bl_label = "Load Mocopi BVH File"
    bl_options = {"REGISTER", "UNDO"}

    filename_ext = ".bvh"

    filter_glob: StringProperty(default="*.bvh", options={"HIDDEN"})

    global_scale: FloatProperty(
        name="Scale",
        description="Scale applied on import. Mocopi captures are in centimetres",
        default=0.01,
        soft_min=0.0001,
        soft_max=1.0,
        min=0.0001,
        max=1000.0,
    )

    update_scene_fps: BoolProperty(
        name="Update Scene FPS",
        description="Set the scene frame rate to the capture's frame rate",
        default=True,
    )

    update_scene_duration: BoolProperty(
        name="Update Scene Duration",
        description=(
            "Set the scene frame range to the capture length. "
            "The pole bone bake uses this range, so leave it on"
        ),
        default=True,
    )

    append_reference_rig: BoolProperty(
        name="Append Reference IK Rig",
        description="Append the Rig and Custom Bone Shapes collections from the bundled .blend",
        default=True,
    )

    run_retarget_prep: BoolProperty(
        name="Run Retarget Prep",
        description="Run retarget-master.py on the imported skeleton",
        default=True,
    )

    do_retarget: BoolProperty(
        name="Retarget to Mesh2Motion",
        description="Retarget the capture onto the Mesh2Motion rig and bake it",
        default=True,
    )

    auto_scale: BoolProperty(
        name="Auto Scale",
        description=(
            "Scale the capture to match the height of the Mesh2Motion rig. "
            "Both should be in T-pose for this to be accurate"
        ),
        default=True,
    )

    use_pose: EnumProperty(
        name="Use Pose",
        description="Which pose of the two armatures to retarget from",
        items=[
            ("REST", "Rest", "Use the rest pose"),
            ("CURRENT", "Current", "Use the current pose"),
        ],
        default="REST",
    )

    keep_source: BoolProperty(
        name="Keep BVH Skeleton",
        description="Leave the imported Mocopi skeleton in the scene after retargeting",
        default=True,
    )

    extract_root_motion: BoolProperty(
        name="Extract Root Motion",
        description=(
            "Move the horizontal travel out of the hips and onto DRV_root, so the "
            "export skeleton's root bone carries it. The visible pose is unchanged"
        ),
        default=False,
    )

    root_smoothing: FloatProperty(
        name="Root Smoothing",
        description=(
            "How much of the hips' side-to-side sway to keep out of the root path. "
            "0 follows the capture exactly; 1 is half a second of blur"
        ),
        default=0.5,
        min=0.0,
        max=1.0,
        subtype="FACTOR",
    )

    smooth_keyframes: BoolProperty(
        name="Smooth Keyframes",
        description=(
            "Blur the baked curves along time to take the sensor jitter out of "
            "the capture. Runs before root motion is extracted"
        ),
        default=True,
    )

    smooth_amount: FloatProperty(
        name="Smoothing",
        description=SMOOTH_AMOUNT_DESCRIPTION,
        default=1.0,
        min=0.0,
        soft_max=8.0,
    )

    simplify_poles: BoolProperty(
        name="Simplify Pole Targets",
        description=POLE_SIMPLIFY_DESCRIPTION,
        default=True,
    )

    pole_smoothing: FloatProperty(
        name="Pole Smoothing",
        description=POLE_SMOOTH_DESCRIPTION,
        default=6.0,
        min=0.0,
        soft_max=16.0,
    )

    pole_tolerance: FloatProperty(
        name="Pole Tolerance",
        description=POLE_TOLERANCE_DESCRIPTION,
        default=0.01,
        min=0.0,
        soft_max=0.05,
        step=0.01,
        precision=4,
        unit="LENGTH",
    )

    decimate_keyframes: BoolProperty(
        name="Decimate Keyframes",
        description=(
            "Drop every keyframe the curves can do without. Runs last, after "
            "root motion, since that writes a key on every frame by design"
        ),
        default=True,
    )

    decimate_rotation: FloatProperty(
        name="Rotation Tolerance",
        description=DECIMATE_ROTATION_DESCRIPTION,
        default=0.5,
        min=0.0,
        soft_max=5.0,
        precision=3,
    )

    decimate_location: FloatProperty(
        name="Position Tolerance",
        description=DECIMATE_LOCATION_DESCRIPTION,
        default=0.001,
        min=0.0,
        soft_max=0.05,
        step=0.01,
        precision=4,
        unit="LENGTH",
    )

    def draw(self, context):
        layout = self.layout
        layout.use_property_split = True
        layout.use_property_decorate = False

        box = layout.box()
        box.label(text="Import", icon="IMPORT")
        box.prop(self, "global_scale")
        box.prop(self, "update_scene_fps")
        box.prop(self, "update_scene_duration")

        box = layout.box()
        box.label(text="Setup", icon="ARMATURE_DATA")
        box.prop(self, "append_reference_rig")
        box.prop(self, "run_retarget_prep")

        box = layout.box()
        box.label(text="Retarget", icon="CON_KINEMATIC")
        box.prop(self, "do_retarget")
        column = box.column()
        column.enabled = self.do_retarget
        column.prop(self, "auto_scale")
        column.prop(self, "use_pose", expand=True)
        column.prop(self, "keep_source")
        column.prop(self, "extract_root_motion")

        row = column.row()
        row.enabled = self.extract_root_motion
        row.prop(self, "root_smoothing")

        box = layout.box()
        box.label(text="Cleanup", icon="GRAPH")
        box.enabled = self.do_retarget

        box.prop(self, "smooth_keyframes")
        row = box.row()
        row.enabled = self.smooth_keyframes
        row.prop(self, "smooth_amount")

        box.prop(self, "simplify_poles")
        sub_column = box.column()
        sub_column.enabled = self.simplify_poles
        sub_column.prop(self, "pole_smoothing")
        sub_column.prop(self, "pole_tolerance")

        box.prop(self, "decimate_keyframes")
        sub_column = box.column()
        sub_column.enabled = self.decimate_keyframes
        sub_column.prop(self, "decimate_rotation")
        sub_column.prop(self, "decimate_location")

    def execute(self, context):
        filepath = self.filepath

        if not filepath or not os.path.isfile(filepath):
            self.report({"ERROR"}, "No BVH file selected")
            return {"CANCELLED"}

        # Load the bone map up front, so a typo in the JSON fails before
        # anything has been imported.
        pairs = None
        if self.do_retarget:
            try:
                pairs, map_options = bone_map_loader.load(BONE_MAP_JSON)
            except bone_map_loader.BoneMapError as exc:
                self.report({"ERROR"}, str(exc))
                return {"CANCELLED"}

        ensure_object_mode()

        # --- 1. Import the capture ---------------------------------------
        try:
            armature = import_bvh(
                filepath,
                self.global_scale,
                self.update_scene_fps,
                self.update_scene_duration,
            )
        except Exception as exc:
            traceback.print_exc()
            self.report({"ERROR"}, "BVH import failed: {}".format(exc))
            return {"CANCELLED"}

        if armature is None:
            self.report({"ERROR"}, "BVH import did not create an armature")
            return {"CANCELLED"}

        summary = ["Imported {}".format(os.path.basename(filepath))]

        # --- 2. Append the reference IK rig ------------------------------
        if self.append_reference_rig:
            if not os.path.isfile(REFERENCE_RIG_BLEND):
                self.report(
                    {"WARNING"},
                    "Reference rig not found at {}".format(REFERENCE_RIG_BLEND),
                )
            else:
                try:
                    appended, skipped, missing = append_reference_collections(
                        REFERENCE_COLLECTIONS
                    )
                except Exception as exc:
                    traceback.print_exc()
                    self.report({"ERROR"}, "Appending reference rig failed: {}".format(exc))
                    return {"CANCELLED"}

                if appended:
                    summary.append("appended {}".format(", ".join(appended)))
                if skipped:
                    summary.append("already present: {}".format(", ".join(skipped)))
                if missing:
                    self.report(
                        {"WARNING"},
                        "Missing in reference .blend: {}".format(", ".join(missing)),
                    )

        # --- 3. Re-select the BVH skeleton and run the prep scripts ------
        select_only(armature)

        if self.run_retarget_prep:
            if not os.path.isfile(RETARGET_MASTER):
                self.report(
                    {"ERROR"}, "retarget-master.py not found at {}".format(RETARGET_MASTER)
                )
                return {"CANCELLED"}

            try:
                run_script(RETARGET_MASTER)
            except Exception as exc:
                traceback.print_exc()
                self.report(
                    {"ERROR"},
                    "retarget-master.py failed: {} (see system console)".format(exc),
                )
                return {"CANCELLED"}

            summary.append("ran retarget prep")

        # --- 4. Retarget onto the Mesh2Motion rig ------------------------
        if self.do_retarget:
            ensure_object_mode()

            target = find_target_armature()
            if target is None:
                self.report(
                    {"ERROR"},
                    "No armature found in the '{}' collection to retarget onto".format(
                        TARGET_COLLECTION
                    ),
                )
                return {"CANCELLED"}

            try:
                result = retarget_engine.retarget(
                    armature,
                    target,
                    pairs,
                    auto_scale=self.auto_scale,
                    use_pose=self.use_pose,
                )
            except retarget_engine.RetargetError as exc:
                self.report({"ERROR"}, str(exc))
                return {"CANCELLED"}
            except Exception as exc:
                traceback.print_exc()
                self.report(
                    {"ERROR"},
                    "Retargeting failed: {} (see system console)".format(exc),
                )
                return {"CANCELLED"}

            summary.append(
                "retargeted {} bones onto {}".format(result["pairs"], target.name)
            )

            # Anything in the map that did not land is worth saying out loud,
            # because the result will look subtly wrong rather than broken.
            if result["missing_source"]:
                self.report(
                    {"WARNING"},
                    "Not on the capture: {}".format(", ".join(result["missing_source"])),
                )
            if result["missing_target"]:
                self.report(
                    {"WARNING"},
                    "Not on the rig: {}".format(", ".join(result["missing_target"])),
                )

            if self.smooth_keyframes and self.smooth_amount > 0.0:
                try:
                    smooth_result = cleanup.smooth(
                        target, sigma_frames=self.smooth_amount
                    )
                except cleanup.CleanupError as exc:
                    # The retarget worked; this is a polish pass, not a reason
                    # to throw the import away.
                    self.report({"WARNING"}, "Smoothing skipped: {}".format(exc))
                except Exception as exc:
                    traceback.print_exc()
                    self.report(
                        {"WARNING"},
                        "Smoothing failed: {} (see system console)".format(exc),
                    )
                else:
                    summary.append(
                        "smoothed {} channels".format(smooth_result["channels"])
                    )

            if self.extract_root_motion:
                try:
                    root_result = root_motion.extract(
                        target, smoothing=self.root_smoothing
                    )
                except root_motion.RootMotionError as exc:
                    # The retarget itself worked, so this is a warning and not
                    # a reason to throw the whole import away.
                    self.report({"WARNING"}, "Root motion skipped: {}".format(exc))
                except Exception as exc:
                    traceback.print_exc()
                    self.report(
                        {"WARNING"},
                        "Root motion failed: {} (see system console)".format(exc),
                    )
                else:
                    summary.append(
                        "root motion {:.2f}m onto {}".format(
                            root_result["travel"], root_motion.ROOT_BONE
                        )
                    )

            # After root motion, which writes a key on every frame of every
            # control hanging off DRV_root -- the four poles included.
            if self.simplify_poles:
                try:
                    pole_result = cleanup.simplify_poles(
                        target,
                        sigma_frames=self.pole_smoothing,
                        tolerance=self.pole_tolerance,
                    )
                except cleanup.CleanupError as exc:
                    self.report({"WARNING"}, "Pole simplify skipped: {}".format(exc))
                except Exception as exc:
                    traceback.print_exc()
                    self.report(
                        {"WARNING"},
                        "Pole simplify failed: {} (see system console)".format(exc),
                    )
                else:
                    summary.append(
                        "poles {} -> {} keys".format(
                            pole_result["before"], pole_result["after"]
                        )
                    )

            if self.decimate_keyframes:
                try:
                    decimate_result = cleanup.decimate(
                        target,
                        tolerance_location=self.decimate_location,
                        tolerance_rotation=self.decimate_rotation,
                    )
                except cleanup.CleanupError as exc:
                    self.report({"WARNING"}, "Decimation skipped: {}".format(exc))
                except Exception as exc:
                    traceback.print_exc()
                    self.report(
                        {"WARNING"},
                        "Decimation failed: {} (see system console)".format(exc),
                    )
                else:
                    summary.append(
                        "decimated {} -> {} keys".format(
                            decimate_result["before"], decimate_result["after"]
                        )
                    )

            if not self.keep_source:
                bpy.data.objects.remove(armature, do_unlink=True)
                summary.append("removed the BVH skeleton")
            else:
                armature.hide_set(True)

            select_only(target)

        self.report({"INFO"}, " | ".join(summary))
        return {"FINISHED"}


class M2M_OT_extract_root_motion(Operator):
    """Move the horizontal travel out of the hips and onto DRV_root on the
    active rig. The visible pose does not change"""

    bl_idname = "m2m.extract_root_motion"
    bl_label = "Extract Root Motion"
    bl_options = {"REGISTER", "UNDO"}

    smoothing: FloatProperty(
        name="Smoothing",
        description=(
            "How much of the hips' side-to-side sway to keep out of the root path. "
            "0 follows the capture exactly; 1 is half a second of blur"
        ),
        default=0.5,
        min=0.0,
        max=1.0,
        subtype="FACTOR",
    )

    zero_start: BoolProperty(
        name="Start At Origin",
        description="Offset the path so the root begins at the world origin",
        default=True,
    )

    @classmethod
    def poll(cls, context):
        obj = context.object
        return (
            obj is not None
            and obj.type == "ARMATURE"
            and root_motion.ROOT_BONE in obj.pose.bones
        )

    def execute(self, context):
        ensure_object_mode()

        try:
            result = root_motion.extract(
                context.object,
                smoothing=self.smoothing,
                zero_start=self.zero_start,
            )
        except root_motion.RootMotionError as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        except Exception as exc:
            traceback.print_exc()
            self.report(
                {"ERROR"}, "Root motion failed: {} (see system console)".format(exc)
            )
            return {"CANCELLED"}

        self.report(
            {"INFO"},
            "Moved {:.2f}m onto {} over {} frames ({} controls compensated)".format(
                result["travel"],
                root_motion.ROOT_BONE,
                result["frames"],
                result["children"],
            ),
        )
        return {"FINISHED"}


class CleanupOperatorBase:
    """Shared plumbing for the two keyframe cleanup operators."""

    bl_options = {"REGISTER", "UNDO"}

    channels: EnumProperty(
        name="Channels",
        description="Which channels to work on",
        items=CHANNEL_ITEMS,
        default="ALL",
    )

    selected_only: BoolProperty(
        name="Selected Bones Only",
        description=(
            "Only touch the bones selected in pose mode, instead of every "
            "bone in the action"
        ),
        default=False,
    )

    @classmethod
    def poll(cls, context):
        obj = context.object
        return (
            obj is not None
            and obj.type == "ARMATURE"
            and obj.animation_data is not None
            and obj.animation_data.action is not None
        )


class M2M_OT_smooth_keyframes(CleanupOperatorBase, Operator):
    """Blur the active armature's animation along time, to take the sensor
    jitter out of a capture. Keyframe count is unchanged"""

    bl_idname = "m2m.smooth_keyframes"
    bl_label = "Smooth Keyframes"

    amount: FloatProperty(
        name="Smoothing",
        description=SMOOTH_AMOUNT_DESCRIPTION,
        default=1.0,
        min=0.0,
        soft_max=8.0,
    )

    def execute(self, context):
        ensure_object_mode()

        if self.amount <= 0.0:
            self.report({"WARNING"}, "Smoothing is 0 -- nothing to do")
            return {"CANCELLED"}

        try:
            result = cleanup.smooth(
                context.object,
                sigma_frames=self.amount,
                channels=self.channels,
                selected_only=self.selected_only,
            )
        except cleanup.CleanupError as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        except Exception as exc:
            traceback.print_exc()
            self.report(
                {"ERROR"}, "Smoothing failed: {} (see system console)".format(exc)
            )
            return {"CANCELLED"}

        self.report(
            {"INFO"},
            "Smoothed {} channels at {:.2g} frames".format(
                result["channels"], result["sigma"]
            ),
        )
        return {"FINISHED"}


class M2M_OT_decimate_keyframes(CleanupOperatorBase, Operator):
    """Drop every keyframe the active armature's curves can do without, within
    a stated error bound. Run this after smoothing and root motion"""

    bl_idname = "m2m.decimate_keyframes"
    bl_label = "Decimate Keyframes"

    tolerance_rotation: FloatProperty(
        name="Rotation Tolerance",
        description=DECIMATE_ROTATION_DESCRIPTION,
        default=0.5,
        min=0.0,
        soft_max=5.0,
        precision=3,
    )

    tolerance_location: FloatProperty(
        name="Position Tolerance",
        description=DECIMATE_LOCATION_DESCRIPTION,
        default=0.001,
        min=0.0,
        soft_max=0.05,
        step=0.01,
        precision=4,
        unit="LENGTH",
    )

    collapse_static: BoolProperty(
        name="Collapse Static Channels",
        description=(
            "Reduce a channel that never moves to a single keyframe instead "
            "of two"
        ),
        default=True,
    )

    interpolation: EnumProperty(
        name="Interpolation",
        description="What to set the surviving keyframes to",
        items=INTERPOLATION_ITEMS,
        default="BEZIER",
    )

    def execute(self, context):
        ensure_object_mode()

        try:
            result = cleanup.decimate(
                context.object,
                tolerance_location=self.tolerance_location,
                tolerance_rotation=self.tolerance_rotation,
                channels=self.channels,
                selected_only=self.selected_only,
                collapse_static=self.collapse_static,
                interpolation=self.interpolation,
            )
        except cleanup.CleanupError as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        except Exception as exc:
            traceback.print_exc()
            self.report(
                {"ERROR"}, "Decimation failed: {} (see system console)".format(exc)
            )
            return {"CANCELLED"}

        self.report(
            {"INFO"},
            "{} -> {} keyframes across {} channels ({:.0f}% removed)".format(
                result["before"],
                result["after"],
                result["channels"],
                result["ratio"] * 100.0,
            ),
        )
        return {"FINISHED"}


class M2M_OT_simplify_poles(Operator):
    """Thin out the four IK pole targets on the active rig. Their rotation
    collapses to one keyframe and their position is blurred and decimated at a
    much looser bound than the rest of the rig. Run it after root motion"""

    bl_idname = "m2m.simplify_poles"
    bl_label = "Simplify Pole Targets"
    bl_options = {"REGISTER", "UNDO"}

    amount: FloatProperty(
        name="Pole Smoothing",
        description=POLE_SMOOTH_DESCRIPTION,
        default=6.0,
        min=0.0,
        soft_max=16.0,
    )

    tolerance: FloatProperty(
        name="Pole Tolerance",
        description=POLE_TOLERANCE_DESCRIPTION,
        default=0.01,
        min=0.0,
        soft_max=0.05,
        step=0.01,
        precision=4,
        unit="LENGTH",
    )

    interpolation: EnumProperty(
        name="Interpolation",
        description="What to set the surviving keyframes to",
        items=INTERPOLATION_ITEMS,
        default="BEZIER",
    )

    @classmethod
    def poll(cls, context):
        obj = context.object
        if (
            obj is None
            or obj.type != "ARMATURE"
            or obj.animation_data is None
            or obj.animation_data.action is None
        ):
            return False

        names = {name.lower() for name in obj.pose.bones.keys()}
        return any(pole.lower() in names for pole in cleanup.POLE_BONES)

    def execute(self, context):
        ensure_object_mode()

        try:
            result = cleanup.simplify_poles(
                context.object,
                sigma_frames=self.amount,
                tolerance=self.tolerance,
                interpolation=self.interpolation,
            )
        except cleanup.CleanupError as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        except Exception as exc:
            traceback.print_exc()
            self.report(
                {"ERROR"}, "Pole simplify failed: {} (see system console)".format(exc)
            )
            return {"CANCELLED"}

        self.report(
            {"INFO"},
            "{} -> {} keyframes across {} pole channels ({:.0f}% removed)".format(
                result["before"],
                result["after"],
                result["channels"],
                result["ratio"] * 100.0,
            ),
        )
        return {"FINISHED"}


class M2M_OT_delete_non_extreme(CleanupOperatorBase, Operator):
    """Delete every keyframe that is not on a frame marked Extreme. Mark the
    poses worth keeping first -- in the Dope Sheet, select them and use
    Key > Keyframe Type > Extreme (R). A channel with no key on an extreme
    frame is sampled there, so the whole rig lands on the same frames"""

    bl_idname = "m2m.delete_non_extreme"
    bl_label = "Delete Non-Extreme Keyframes"

    interpolation: EnumProperty(
        name="Interpolation",
        description="What to set the surviving keyframes to",
        items=INTERPOLATION_ITEMS,
        default="BEZIER",
    )

    # poll() is inherited: an armature holding an action, and no more than
    # that. Checking that something is actually marked Extreme would mean
    # walking every key in the action on every panel redraw, which is what
    # got the old bone-map readout removed. The operator reports it instead.

    def execute(self, context):
        ensure_object_mode()

        try:
            result = cleanup.delete_non_extreme(
                context.object,
                channels=self.channels,
                selected_only=self.selected_only,
                interpolation=self.interpolation,
            )
        except cleanup.CleanupError as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        except Exception as exc:
            traceback.print_exc()
            self.report(
                {"ERROR"},
                "Extreme cleanup failed: {} (see system console)".format(exc),
            )
            return {"CANCELLED"}

        message = (
            "{} -> {} keyframes across {} channels, on {} extreme frames "
            "({}-{})".format(
                result["before"],
                result["after"],
                result["channels"],
                result["frames"],
                result["first"],
                result["last"],
            )
        )
        if result["inserted"]:
            message += " | {} sampled in".format(result["inserted"])

        self.report({"INFO"}, message)
        return {"FINISHED"}


# ----------------------------------------------------------------------
# Panel
# ----------------------------------------------------------------------

class M2M_PT_mocopi_panel(Panel):
    bl_label = "Mocopi Retarget"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Mesh2Motion"

    def draw(self, context):
        layout = self.layout

        column = layout.column()
        column.scale_y = 1.5
        column.operator(M2M_OT_load_mocopi_bvh.bl_idname, icon="ARMATURE_DATA")

        layout.separator()
        layout.operator(M2M_OT_extract_root_motion.bl_idname, icon="ORIENTATION_PARENT")

        layout.separator()
        layout.label(text="Cleanup")

        column = layout.column(align=True)
        column.operator(M2M_OT_smooth_keyframes.bl_idname, icon="SMOOTHCURVE")
        column.operator(M2M_OT_simplify_poles.bl_idname, icon="CON_KINEMATIC")
        column.operator(M2M_OT_decimate_keyframes.bl_idname, icon="DECORATE_KEYFRAME")

        layout.separator()
        layout.operator(
            M2M_OT_delete_non_extreme.bl_idname, icon="KEYTYPE_EXTREME_VEC"
        )


# ----------------------------------------------------------------------
# Registration
# ----------------------------------------------------------------------

classes = (
    M2M_OT_load_mocopi_bvh,
    M2M_OT_extract_root_motion,
    M2M_OT_smooth_keyframes,
    M2M_OT_simplify_poles,
    M2M_OT_decimate_keyframes,
    M2M_OT_delete_non_extreme,
    M2M_PT_mocopi_panel,
)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
