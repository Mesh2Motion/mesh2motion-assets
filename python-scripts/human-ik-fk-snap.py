import math
import bpy
from mathutils import Matrix, Vector

'''
Adds an "M2M IK/FK" panel to the 3D viewport sidebar (N panel) for the human rig arms.

The human rig arms can be posed with IK (CTRL_Arm_*, POLEARM_*) or FK (FK_upper_arm_*,
FK_forearm_*, FK_hand_*). The "FK_L" / "FK_R" properties on the armature object switch
between them (0 = IK, 1 = FK) and can be keyed. Switching on its own makes the arm pop, so use the
snap buttons to match one chain to the other on the current frame first.

Instructions:
1. Open an animation file that links rig-human.blend
2. Open a Text Editor, open this file, and click "Run Script"
3. In the 3D viewport, press N and open the "M2M" tab
'''

SIDES = ('L', 'R')
POLE_DISTANCE = 0.4  # distance of the elbow pole from the elbow, matches the rest pose


def get_armature(context):
    ob = context.active_object
    if ob and ob.type == 'ARMATURE' and 'FK_L' in ob and 'FK_forearm_L' in ob.pose.bones:
        return ob
    return None


def update_pose(context, ob):
    ob.update_tag()
    context.view_layer.update()


def key_bones(ob, names, with_location, frame):
    for name in names:
        pb = ob.pose.bones[name]
        pb.keyframe_insert('rotation_quaternion', frame=frame)
        if with_location:
            pb.keyframe_insert('location', frame=frame)


def snap_ik_to_fk(context, ob, side, key):
    # copy the current (IK) arm pose onto the FK controls, then switch to FK
    pb = ob.pose.bones
    for drv, fk in (('DRV_upper_arm', 'FK_upper_arm'), ('DRV_forearm', 'FK_forearm'), ('DRV_hand', 'FK_hand')):
        pb[f'{fk}_{side}'].matrix = pb[f'{drv}_{side}'].matrix.copy()
        update_pose(context, ob)
    ob[f'FK_{side}'] = 1.0
    update_pose(context, ob)
    if key:
        frame = context.scene.frame_current
        key_bones(ob, [f'FK_upper_arm_{side}', f'FK_forearm_{side}', f'FK_hand_{side}'], False, frame)
        ob.keyframe_insert(f'["FK_{side}"]', frame=frame)


def snap_fk_to_ik(context, ob, side, key):
    # move the IK control and pole to match the current (FK) arm pose, then switch to IK
    pb = ob.pose.bones
    ctrl = pb[f'CTRL_Arm_{side}']
    pole = pb[f'POLEARM_{side}']
    hand = pb[f'DRV_hand_{side}']
    upper = pb[f'DRV_upper_arm_{side}']
    fore = pb[f'DRV_forearm_{side}']

    # DRV_hand is a child of CTRL_Arm, so solve the control from the hand's offset to it
    hand_offset = ctrl.bone.matrix_local.inverted() @ hand.bone.matrix_local @ hand.matrix_basis
    target = pb[f'FK_hand_{side}'].matrix.copy()
    shoulder, elbow = upper.head.copy(), fore.head.copy()
    wrist = fore.tail.copy()

    ctrl.matrix = target @ hand_offset.inverted()
    update_pose(context, ob)

    # the IK has a pole angle, so search for the pole position around the shoulder->wrist
    # line that puts the IK arm closest to the FK arm
    fk_upper = pb[f'FK_upper_arm_{side}'].matrix.copy()
    fk_fore = pb[f'FK_forearm_{side}'].matrix.copy()
    line = (wrist - shoulder).normalized()
    out = (elbow - shoulder) - line * (elbow - shoulder).dot(line)
    if out.length < 1e-4:  # arm is straight, start from the elbow's current bend direction
        out = fore.matrix.to_3x3() @ Vector((0, 0, 1))
    out.normalize()
    pole_matrix = pole.matrix.copy()
    ob[f'FK_{side}'] = 0.0

    def try_angle(angle):
        pole_matrix.translation = elbow + Matrix.Rotation(angle, 3, line) @ out * POLE_DISTANCE
        pole.matrix = pole_matrix
        update_pose(context, ob)
        return sum(abs(a - b) for m1, m2 in ((upper.matrix, fk_upper), (fore.matrix, fk_fore))
                   for r1, r2 in zip(m1, m2) for a, b in zip(r1, r2))

    step = math.radians(10)
    best = min((i * step for i in range(36)), key=try_angle)
    while step > math.radians(0.01):
        step /= 2
        best = min((best - step, best, best + step), key=try_angle)
    try_angle(best)
    if key:
        frame = context.scene.frame_current
        key_bones(ob, [f'CTRL_Arm_{side}', f'POLEARM_{side}'], True, frame)
        ob.keyframe_insert(f'["FK_{side}"]', frame=frame)


class M2M_OT_arm_snap(bpy.types.Operator):
    bl_idname = 'm2m.arm_snap'
    bl_label = 'Snap Arm'
    bl_description = 'Match the other chain to the current arm pose and switch to it'
    bl_options = {'REGISTER', 'UNDO'}

    side: bpy.props.EnumProperty(items=[('L', 'Left', ''), ('R', 'Right', '')])
    to_fk: bpy.props.BoolProperty()

    def execute(self, context):
        ob = get_armature(context)
        if ob is None:
            self.report({'ERROR'}, 'Select the human rig armature')
            return {'CANCELLED'}
        key = context.scene.m2m_snap_key
        if self.to_fk:
            snap_ik_to_fk(context, ob, self.side, key)
        else:
            snap_fk_to_ik(context, ob, self.side, key)
        return {'FINISHED'}


class M2M_PT_ik_fk(bpy.types.Panel):
    bl_label = 'Arm IK/FK'
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = 'M2M'

    @classmethod
    def poll(cls, context):
        return get_armature(context) is not None

    def draw(self, context):
        ob = get_armature(context)
        layout = self.layout
        layout.prop(context.scene, 'm2m_snap_key', text='Key on snap')
        for side in SIDES:
            box = layout.box()
            box.label(text=f'Arm {side}')
            box.prop(ob, f'["FK_{side}"]', text='FK', slider=True)
            row = box.row(align=True)
            op = row.operator('m2m.arm_snap', text='IK → FK')
            op.side, op.to_fk = side, True
            op = row.operator('m2m.arm_snap', text='FK → IK')
            op.side, op.to_fk = side, False


classes = (M2M_OT_arm_snap, M2M_PT_ik_fk)


def register():
    bpy.types.Scene.m2m_snap_key = bpy.props.BoolProperty(
        name='Key on snap', default=True,
        description='Insert keys on the snapped controls and the FK switch at the current frame')
    for cls in classes:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
    del bpy.types.Scene.m2m_snap_key


if __name__ == '__main__':
    try:
        unregister()
    except (RuntimeError, AttributeError):
        pass
    register()
