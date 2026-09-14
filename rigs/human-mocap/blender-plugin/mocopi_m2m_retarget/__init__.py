"""Mocopi to Mesh2Motion.

Blender extension that collapses the manual Mocopi -> Mesh2Motion retargeting
workflow into a single button:

  1. Pick a Mocopi BVH capture and import it at 0.01 scale.
  2. Append the reference IK rig collections from the bundled .blend.
  3. Re-select the freshly imported BVH skeleton and run retarget-master.py,
     which expands the arms and builds the elbow / knee pole bones.
  4. Retarget the capture onto the Mesh2Motion rig using an explicit bone map
     and bake the result.

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
from . import retarget_engine


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

            if not self.keep_source:
                bpy.data.objects.remove(armature, do_unlink=True)
                summary.append("removed the BVH skeleton")
            else:
                armature.hide_set(True)

            select_only(target)

        self.report({"INFO"}, " | ".join(summary))
        return {"FINISHED"}


class M2M_OT_open_bone_map(Operator):
    """Load the bone map into a text block so it can be checked or edited"""

    bl_idname = "m2m.open_bone_map"
    bl_label = "Open Bone Map"
    bl_options = {"REGISTER"}

    def execute(self, context):
        if not os.path.isfile(BONE_MAP_JSON):
            self.report({"ERROR"}, "Bone map not found at {}".format(BONE_MAP_JSON))
            return {"CANCELLED"}

        name = os.path.basename(BONE_MAP_JSON)
        existing = bpy.data.texts.get(name)
        if existing:
            bpy.data.texts.remove(existing)

        text = bpy.data.texts.load(BONE_MAP_JSON)
        text.name = name

        self.report(
            {"INFO"},
            "Loaded {} into the text editor. Save it there to apply changes.".format(name),
        )
        return {"FINISHED"}


# ----------------------------------------------------------------------
# Panel
# ----------------------------------------------------------------------

class M2M_PT_mocopi_panel(Panel):
    bl_label = "Mocopi Retarget"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Mocopi"

    def draw(self, context):
        layout = self.layout

        column = layout.column()
        column.scale_y = 1.5
        column.operator(M2M_OT_load_mocopi_bvh.bl_idname, icon="ARMATURE_DATA")

        layout.separator()

        try:
            pairs, _options = bone_map_loader.load(BONE_MAP_JSON)
        except bone_map_loader.BoneMapError as exc:
            box = layout.box()
            box.alert = True
            box.label(text="Bone map problem:", icon="ERROR")
            box.label(text=str(exc))
        else:
            layout.label(text="Bone map: {} pairs".format(len(pairs)))

        layout.operator(M2M_OT_open_bone_map.bl_idname, icon="TEXT")


# ----------------------------------------------------------------------
# Registration
# ----------------------------------------------------------------------

classes = (
    M2M_OT_load_mocopi_bvh,
    M2M_OT_open_bone_map,
    M2M_PT_mocopi_panel,
)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
