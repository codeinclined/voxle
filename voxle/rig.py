"""Armature creation, binding and voxel skin weights."""

import hashlib
from collections import namedtuple

import bpy
import numpy as np
from bpy.app.handlers import persistent
from bpy.props import BoolProperty, EnumProperty, IntProperty, StringProperty
from mathutils import Vector

from . import core, mesh, state

BoneInfo = namedtuple("BoneInfo", "names heads tails slot_map hash depths radii parents hinges")
# radius: the body part's half-thickness in voxels, so a thick torso can claim voxels farther
# from its bone than a thin arm can (None = 1).
BoneSpec = namedtuple("BoneSpec", "name head tail parent connect deform radius", defaults=(None,))

TEMPLATE_ITEMS = [
    ('HUMANOID', "Humanoid", "Hips, spine, chest, head, arms and legs, fitted to the model's shape"),
    ('QUADRUPED', "Quadruped", "Four-legged animal facing -Y, with neck, head and tail"),
    ('CHAIN', "Chain", "A vertical chain of bones, for tails, tentacles, plants or flags"),
    ('SINGLE', "Single Bone", "One bone that moves the whole model"),
]


# ----------------------------------------------------------------------------
# Queries


def get_armature(obj):
    arm = obj.voxel.armature
    if arm is None or arm.type != 'ARMATURE' or not arm.users_collection:
        return None
    return arm


def rest_hash(arm):
    parts = []
    for b in arm.data.bones:
        if b.use_deform:
            parts.append(b.name)
            parts.extend(f"{v:.4f}" for v in (*b.head_local, *b.tail_local))
    return hashlib.md5("|".join(parts).encode()).hexdigest()


def bone_info(obj, arm):
    bones = [b for b in arm.data.bones if b.use_deform]
    names = [b.name for b in bones]
    M = (mesh.world_matrix(obj) @ mesh.grid_to_local(obj)).inverted_safe() @ mesh.world_matrix(arm)
    heads = np.array([tuple(M @ b.head_local) for b in bones], np.float32).reshape(-1, 3)
    tails = np.array([tuple(M @ b.tail_local) for b in bones], np.float32).reshape(-1, 3)
    slot_map = np.full(256, -1, np.int32)
    lookup = {n: i for i, n in enumerate(names)}
    depths, parents = _joint_depths(bones, lookup)
    for i, slot in enumerate(obj.voxel.bone_slots):
        if i >= 255:
            break
        slot_map[i + 1] = lookup.get(slot.name, -1)
    radii = np.array([float(b.get("voxel_radius", 1.0)) for b in bones], np.float32)
    hinges = np.array([bool(b.get("voxel_hinge", False)) for b in bones], bool)
    return BoneInfo(names, heads, tails, slot_map, rest_hash(arm), depths, radii, parents, hinges)


def _joint_depths(bones, lookup):
    """Each deform bone's depth in the limb hierarchy (torso shallow, hands deep) and its
    joint parent (index, -1 for none).

    A bone whose head touches another deform bone's tail counts as that bone's child even
    when it isn't parented to it, so IK hands and feet (parented to the root) still sit
    below their forearm or shin.
    """
    eps = 1e-4
    parent = []
    for b in bones:
        p = next((o for o in bones if o is not b and (o.tail_local - b.head_local).length < eps), None)
        if p is None:
            p = b.parent
            while p is not None and p.name not in lookup:
                p = p.parent
        parent.append(lookup[p.name] if p is not None else -1)
    depths = np.zeros(len(bones), np.int32)
    for i in range(len(bones)):
        d, j, seen = 0, parent[i], {i}
        while j >= 0 and j not in seen:
            seen.add(j)
            d += 1
            j = parent[j]
        depths[i] = d
    return depths, np.array(parent, np.int32)


def bone_slot(obj, name):
    """Bone-map value for a bone name, allocating a slot if needed (0 if out of slots)."""
    slots = obj.voxel.bone_slots
    for i, s in enumerate(slots):
        if s.name == name:
            return i + 1
    if len(slots) >= 255:
        return 0
    slots.add().name = name
    return len(slots)


def slot_name(obj, slot):
    if 0 < slot <= len(obj.voxel.bone_slots):
        return obj.voxel.bone_slots[slot - 1].name
    return ""


def bound_objects(arm):
    return [o for o in bpy.data.objects
            if o.type == 'MESH' and o.voxel.is_voxel and o.voxel.armature == arm]


# ----------------------------------------------------------------------------
# Templates (computed in grid space from the model's shape)


def _humanoid(solid, lo, hi):
    x0, y0, z0 = lo
    x1, y1, z1 = hi + 1
    W, H = x1 - x0, z1 - z0
    cx, cy = (x0 + x1) / 2.0, (y0 + y1) / 2.0
    xs_c = np.arange(solid.shape[0]) + 0.5 - cx  # signed x of each column centre

    # Crotch: lowest slice where the centre column(s) are filled.
    centre_cols = sorted({int(np.floor(cx - 0.5)), int(np.floor(cx))})
    crotch = None
    for z in range(z0, z0 + int(H * 0.75) + 1):
        if solid[centre_cols, :, z].any():
            crotch = z
            break
    if crotch is None or crotch - z0 < max(1, 0.12 * H):
        crotch = z0 + 0.45 * H

    # Legs and torso width, measured halfway up the legs.
    zl = int(z0 + (crotch - z0) * 0.5)
    pts = np.argwhere(solid[:, :, zl])
    leg_off, torso_half, leg_y, leg_r = W * 0.2, max(W * 0.25, 1.0), cy, 1.0
    if len(pts):
        sx = xs_c[pts[:, 0]]
        left, right = sx[sx > 0], sx[sx < 0]
        if len(left) and len(right):
            leg_off = (left.mean() - right.mean()) / 2.0
            ly = pts[sx > 0, 1]
            leg_r = max((left.max() - left.min()) / 2.0 + 0.5, (ly.max() - ly.min()) / 2.0 + 0.5, 0.5)
        torso_half = max(np.abs(sx).max() + 0.5, 1.0)
        leg_y = pts[:, 1].mean() + 0.5

    # Head: scanning down from the top, the slice area (arms excluded) shrinks at the neck.
    band = np.abs(xs_c) <= torso_half
    zc = int(np.ceil(crotch))
    head_bottom = None
    best = 0
    for z in range(z1 - 1, zc, -1):
        a = int(solid[band, :, z].sum())
        best = max(best, a)
        if z1 - z >= 2 and a < 0.6 * best:
            head_bottom = z + 1
            break
    if head_bottom is None or head_bottom - crotch < 2:
        head_bottom = crotch + 0.7 * (z1 - crotch)
    shoulder_z = head_bottom - max(1.0, 0.12 * (head_bottom - crotch))

    # Arms: voxels outside the torso width, between the legs' midpoint and the head.
    sub = solid.copy()
    sub[band] = False
    sub[:, :, :int(z0 + 0.3 * (crotch - z0))] = False
    sub[:, :, int(np.ceil(head_bottom)):] = False
    arm_pts = np.argwhere(sub)
    arms = {}
    arm_r = 1.0
    if len(arm_pts):
        ax = np.abs(xs_c[arm_pts[:, 0]])
        az = arm_pts[:, 2] + 0.5
        ay = arm_pts[:, 1].mean() + 0.5
        depth_r = (arm_pts[:, 1].max() - arm_pts[:, 1].min()) / 2.0 + 0.5
        reach = ax.max() + 0.5
        drop = shoulder_z - az.min()
        if reach - torso_half > drop:  # T-pose
            sz = az.mean()
            arm_r = max((az.max() - az.min()) / 2.0 + 0.5, depth_r, 0.5)
            arms['start'] = (torso_half, ay, sz)
            arms['end'] = (reach, ay, sz)
        else:  # arms down
            x = ax.mean()
            arm_r = max((ax.max() - ax.min()) / 2.0 + 0.5, depth_r, 0.5)
            arms['start'] = (x, ay, shoulder_z)
            arms['end'] = (x, ay, az.min() - 0.5)
    else:  # arms hug the body
        x = max(torso_half - 0.5, 0.5)
        arms['start'] = (x, cy, shoulder_z)
        arms['end'] = (x, cy, crotch)

    def lerp(a, b, t):
        return tuple(a[i] + (b[i] - a[i]) * t for i in range(3))

    def snap(p, axis):
        # Put joints on voxel boundaries so limbs bend at a seam, not through a block.
        return tuple(round(v) if i == axis else v for i, v in enumerate(p))

    top = solid[band, :, z1 - 1]
    head_r = max(np.abs(xs_c[band][top.any(axis=1)]).max() + 0.5, 1.0) if top.any() else torso_half
    spine_len = head_bottom - crotch
    hips_t = (cx, cy, crotch + 0.3 * spine_len)
    spine_t = (cx, cy, crotch + 0.65 * spine_len)
    specs = [
        BoneSpec("root", (cx, cy, z0), (cx, cy + max(H * 0.25, 1.0), z0), None, False, False),
        BoneSpec("hips", (cx, cy, crotch), hips_t, "root", False, True, torso_half),
        BoneSpec("spine", hips_t, spine_t, "hips", True, True, torso_half),
        BoneSpec("chest", spine_t, (cx, cy, head_bottom), "spine", True, True, torso_half),
        BoneSpec("head", (cx, cy, head_bottom), (cx, cy, z1), "chest", True, True, head_r),
    ]
    crotch = round(crotch)
    ankle = z0 + max(1, round(0.15 * (crotch - z0)))
    knee = max(ankle + 1, z0 + round(0.5 * (crotch - z0)))
    for side, sgn in (("L", 1), ("R", -1)):
        s = (cx + sgn * arms['start'][0], arms['start'][1], arms['start'][2])
        e = (cx + sgn * arms['end'][0], arms['end'][1], arms['end'][2])
        axis = int(np.argmax(np.abs(np.subtract(e, s))))
        elbow, wrist = snap(lerp(s, e, 0.45), axis), snap(lerp(s, e, 0.8), axis)
        specs += [
            BoneSpec(f"upper_arm.{side}", s, elbow, "chest", False, True, arm_r),
            BoneSpec(f"forearm.{side}", elbow, wrist, f"upper_arm.{side}", True, True, arm_r),
            BoneSpec(f"hand.{side}", wrist, e, f"forearm.{side}", True, True, arm_r),
        ]
        lx = cx + sgn * leg_off
        # Toe: front-most voxel of this foot.
        foot = solid[:, :, z0:int(np.ceil(ankle))].any(axis=2)
        cols = np.argwhere(foot)
        cols = cols[np.sign(xs_c[cols[:, 0]]) == sgn] if len(cols) else cols
        toe_y = cols[:, 1].min() + 0.5 if len(cols) else leg_y - 1.0
        if leg_y - toe_y < 0.5:
            toe_y = leg_y - 1.0
        specs += [
            BoneSpec(f"thigh.{side}", (lx, leg_y, crotch), (lx, leg_y, knee), "hips", False, True, leg_r),
            BoneSpec(f"shin.{side}", (lx, leg_y, knee), (lx, leg_y, ankle), f"thigh.{side}", True, True, leg_r),
            BoneSpec(f"foot.{side}", (lx, leg_y, ankle), (lx, toe_y, z0 + 0.25), f"shin.{side}", True, True, leg_r),
        ]
    return specs


def _quadruped(solid, lo, hi):
    x0, y0, z0 = lo
    x1, y1, z1 = hi + 1
    W, D, H = x1 - x0, y1 - y0, z1 - z0
    cx = (x0 + x1) / 2.0
    zb = z0 + 0.62 * H
    rear, mid, front = (cx, y1 - 0.15 * D, zb), (cx, y0 + 0.55 * D, zb), (cx, y0 + 0.3 * D, zb)
    neck_t = (cx, y0 + 0.18 * D, z0 + 0.85 * H)
    specs = [
        BoneSpec("root", (cx, (y0 + y1) / 2.0, z0), (cx, (y0 + y1) / 2.0 + max(D * 0.25, 1.0), z0), None, False, False),
        BoneSpec("hips", rear, mid, "root", False, True),
        BoneSpec("chest", mid, front, "hips", True, True),
        BoneSpec("neck", front, neck_t, "chest", True, True),
        BoneSpec("head", neck_t, (cx, y0, neck_t[2]), "neck", True, True),
        BoneSpec("tail", rear, (cx, y1, zb + 0.1 * H), "hips", False, True),
    ]
    knee_z = z0 + 0.3 * H
    for side, sgn in (("L", 1), ("R", -1)):
        x = cx + sgn * 0.3 * W
        for part, y, parent in (("front", y0 + 0.3 * D, "chest"), ("back", y1 - 0.2 * D, "hips")):
            specs += [
                BoneSpec(f"{part}_leg.{side}", (x, y, zb), (x, y, knee_z), parent, False, True),
                BoneSpec(f"{part}_shin.{side}", (x, y, knee_z), (x, y, z0), f"{part}_leg.{side}", True, True),
            ]
    return specs


def _chain(solid, lo, hi, count):
    cx, cy = (lo[0] + hi[0] + 1) / 2.0, (lo[1] + hi[1] + 1) / 2.0
    z0, z1 = lo[2], hi[2] + 1
    specs, parent = [], None
    for i in range(count):
        a = z0 + (z1 - z0) * i / count
        b = z0 + (z1 - z0) * (i + 1) / count
        name = f"bone.{i + 1:02d}"
        specs.append(BoneSpec(name, (cx, cy, a), (cx, cy, b), parent, parent is not None, True))
        parent = name
    return specs


def template_bones(kind, grid, chain_count=4):
    bounds = core.content_bounds(grid)
    if bounds is None:
        return []
    lo, hi = bounds
    solid = grid > 0
    if kind == 'HUMANOID':
        return _humanoid(solid, lo, hi)
    if kind == 'QUADRUPED':
        return _quadruped(solid, lo, hi)
    if kind == 'CHAIN':
        return _chain(solid, lo, hi, chain_count)
    return _chain(solid, lo, hi, 1)


# ----------------------------------------------------------------------------
# Binding


def bind(obj, arm):
    vp = obj.voxel
    vp.armature = arm
    if obj.parent != arm:
        mw = mesh.world_matrix(obj)
        obj.parent = arm
        obj.parent_type = 'OBJECT'
        obj.matrix_parent_inverse = mesh.world_matrix(arm).inverted_safe()
        obj.matrix_basis = mw
    mod = next((m for m in obj.modifiers if m.type == 'ARMATURE'), None)
    if mod is None:
        mod = obj.modifiers.new("Armature", 'ARMATURE')
    mod.object = arm
    mod.use_vertex_groups = True
    mod.use_bone_envelopes = False
    # Dual-quaternion skinning keeps the sliced elbows and knees round when they bend.
    mod.use_deform_preserve_volume = True
    vp.bound = True
    mesh.rebuild(obj)


def unbind(obj):
    vp = obj.voxel
    arm = vp.armature
    for m in [m for m in obj.modifiers if m.type == 'ARMATURE']:
        obj.modifiers.remove(m)
    if arm is not None:
        for b in arm.data.bones:
            vg = obj.vertex_groups.get(b.name)
            if vg is not None:
                obj.vertex_groups.remove(vg)
        if obj.parent == arm:
            mw = mesh.world_matrix(obj)
            obj.parent = None
            obj.matrix_basis = mw
    vp.bound = False
    vp.rest_hash = ""
    mesh.rebuild(obj)


# IK limbs per template: (upper bone, lower bone, IK target, pole, bend direction, style).
# Models face -Y, so knees bend forward and elbows back; a quadruped's front elbows point
# back and its hind knees forward.
#
# 'SIDE' (legs): the foot itself is the IK target and keeps its orientation, so feet stay
# flat. Its pole sits out to the side of the foot and moves with it (turning the foot aims
# the knee); an IK chain only flips when its target lines up with the pole, and a foot is
# rarely straight out to the side.
#
# 'AIM' (arms): a separate hand_ik control is dragged, and the (hidden) hand stays on the
# forearm so it follows the arm (rotating the control turns the hand). The elbow pole is
# carried by an aim bone at the shoulder that tracks the hand, so box-shaped arms bend flat
# instead of rolling and twisting.
IK_LIMBS = {
    'HUMANOID': [(f"{u}.{s}", f"{l}.{s}", f"{e}.{s}", f"{p}.{s}", d, style)
                 for s in "LR"
                 for u, l, e, p, d, style in (("upper_arm", "forearm", "hand", "hand_ik", 1.0, 'AIM'),
                                              ("thigh", "shin", "foot", "knee_pole", -1.0, 'SIDE'))],
    'QUADRUPED': [(f"{part}_leg.{s}", f"{part}_shin.{s}", f"{part}_foot_ik.{s}",
                   f"{part}_pole.{s}", d, 'SIDE')
                  for s in "LR" for part, d in (("front", 1.0), ("back", -1.0))],
}

LIMIT_REACH = "Limit Reach"


def _signed_angle(u, v, normal):
    a = u.angle(v)
    c = u.cross(v)
    if c.length < 1e-9:  # parallel or opposite: 0 or 180 degrees either way
        return a
    return -a if c.angle(normal) < 1.0 else a


def _pole_angle(base, ik_bone, pole):
    """The IK pole angle that keeps a chain exactly in its rest pose."""
    normal = (ik_bone.tail - base.head).cross(pole - base.head)
    projected = normal.cross(base.tail - base.head)
    return _signed_angle(base.x_axis, projected, base.tail - base.head)


def _reach_helper(eb, upper, u, root, voxel_size, mech):
    """A hidden bone at the top of the limb, used to keep the IK target within reach."""
    base, _, side = upper.rpartition(".")
    helper = eb.new(f"{base}_reach.{side}" if base else upper + "_reach")
    helper.head = u.head.copy()
    helper.tail = u.head + (u.tail - u.head).normalized() * max(voxel_size, u.length * 0.25)
    helper.parent = u.parent or root
    helper.use_deform = False
    mech.assign(helper)
    return helper.name


def _add_ik_bones(data, kind, voxel_size):
    """Set up IK targets, poles and reach helpers (edit mode). Returns the constraints to add."""
    eb = data.edit_bones
    root = eb.get("root")
    coll = data.collections.new("IK Controls")
    # Helper bones the rig needs but you never touch live in a hidden collection.
    mech = data.collections.new("Mechanism")
    mech.is_visible = False
    setups = []
    for upper, lower, end, ctrl_name, d, style in IK_LIMBS.get(kind, ()):
        u, lo = eb.get(upper), eb.get(lower)
        if u is None or lo is None:
            continue
        bend = Vector((0.0, d, 0.0))
        # A slight bend tells the solver which way the joint folds.
        u.tail = u.tail + bend * (voxel_size * 0.1)
        reach = u.length + lo.length
        setup = dict(style=style, upper=upper, lower=lower, reach=reach,
                     helper=_reach_helper(eb, upper, u, root, voxel_size, mech))

        if style == 'AIM':
            hand = eb.get(end)
            ctrl = eb.new(ctrl_name)
            if hand is not None:
                ctrl.head, ctrl.tail, ctrl.roll = hand.head.copy(), hand.tail.copy(), hand.roll
                mech.assign(hand)  # the control sits on top of it; only the control is clickable
            else:
                ctrl.head = lo.tail.copy()
                ctrl.tail = ctrl.head + (lo.tail - lo.head).normalized() * max(voxel_size, lo.length * 0.3)
            ctrl.parent = root
            ctrl.use_deform = False
            # The elbow pole rides on a hidden aim bone from the shoulder to the hand that
            # tracks the hand, so the bend plane swings with the arm (the shortest way from
            # the rest pose) instead of rolling it. The one direction it can't swing to
            # smoothly is straight opposite the rest arm: into the chest for a T-pose, or
            # straight overhead for arms at the sides, where the arm is straight anyway.
            aim = eb.new(upper.replace("upper_arm", "arm_aim"))
            aim.head = u.head.copy()
            aim.tail = ctrl.head.copy()
            aim.parent = u.parent or root
            aim.use_deform = False
            mech.assign(aim)
            pole = eb.new(upper.replace("upper_arm", "elbow_pole"))
            pole.head = u.tail + bend * max(reach * 0.6, voxel_size * 2)
            pole.tail = pole.head + bend * max(reach * 0.2, voxel_size)
            want_head = pole.head.copy()
            pole.parent = aim
            pole.use_deform = False
            setup.update(target=ctrl.name, end=end if hand is not None else None, aim=aim.name,
                         pole=pole.name, angle=_pole_angle(u, lo, want_head))
            colored = (ctrl, pole)
        else:
            target = eb.get(end)
            if target is None:
                target = eb.new(end)
                target.head = lo.tail.copy()
                target.tail = target.head + Vector((0.0, -max(lo.length * 0.5, voxel_size), 0.0))
                target.use_deform = False
            # Free the foot from the leg so moving it pulls the whole chain along.
            target.use_connect = False
            target.parent = root
            out = Vector((1.0 if (lo.tail.x or (1.0 if upper.endswith(".L") else -1.0)) > 0 else -1.0, 0.0, 0.0))
            pole = eb.new(ctrl_name)
            pole.head = target.head + out * max(reach * 0.6, voxel_size * 2)
            pole.tail = pole.head + out * max(reach * 0.2, voxel_size)
            pole.parent = target
            pole.use_deform = False
            setup.update(target=target.name, pole=pole.name, angle=_pole_angle(u, lo, pole.head))
            colored = (target, pole)
        for b in colored:
            b.color.palette = 'THEME01'
            coll.assign(b)
        setups.append(setup)
    return setups


def _add_ik_constraints(arm, setups, limit_reach=True):
    P = arm.pose.bones
    for st in setups:
        c = P[st["lower"]].constraints.new('IK')
        c.target, c.subtarget = arm, st["target"]
        c.chain_count = 2
        c.pole_target, c.pole_subtarget = arm, st["pole"]
        c.pole_angle = st["angle"]
        if st.get("aim"):
            dt = P[st["aim"]].constraints.new('DAMPED_TRACK')
            dt.target, dt.subtarget = arm, st["target"]
        if st["style"] == 'AIM' and st["end"]:
            cr = P[st["end"]].constraints.new('COPY_ROTATION')
            cr.name = "Follow Hand Control"
            cr.target, cr.subtarget = arm, st["target"]
            cr.owner_space = cr.target_space = 'LOCAL'
        c = P[st["target"]].constraints.new('LIMIT_DISTANCE')
        c.name = LIMIT_REACH
        c.target, c.subtarget = arm, st["helper"]
        c.distance = st["reach"]
        c.limit_mode = 'LIMITDIST_INSIDE'
        c.mute = not limit_reach


def set_limit_reach(arm, on):
    """Turn the 'no stretching' reach limit on the IK hands and feet on or off."""
    if arm is None:
        return
    for pb in arm.pose.bones:
        for c in pb.constraints:
            if c.type == 'LIMIT_DISTANCE' and c.name.startswith(LIMIT_REACH):
                c.mute = not on


def create_armature(context, obj, kind, chain_count=4, ik=True):
    grid, _ = mesh.load_arrays(obj)
    specs = template_bones(kind, grid, chain_count)
    if not specs:
        return None
    if obj.voxel.bound:
        unbind(obj)

    name = obj.name + "_rig"
    data = bpy.data.armatures.new(name)
    arm = bpy.data.objects.new(name, data)
    coll = obj.users_collection[0] if obj.users_collection else context.scene.collection
    coll.objects.link(arm)
    arm.matrix_basis = mesh.world_matrix(obj)
    arm.show_in_front = True

    if context.object is not None and context.object.mode != 'OBJECT':
        bpy.ops.object.mode_set(mode='OBJECT')
    for o in context.selected_objects:
        o.select_set(False)
    context.view_layer.objects.active = arm
    arm.select_set(True)
    bpy.ops.object.mode_set(mode='EDIT')
    to_local = mesh.grid_to_local(obj)
    min_len = obj.voxel.voxel_size * 0.5
    hinges = {limb[1] for limb in IK_LIMBS.get(kind, ())}  # elbows and knees
    made = {}
    for spec in specs:
        b = data.edit_bones.new(spec.name)
        b.head = to_local @ Vector(spec.head)
        b.tail = to_local @ Vector(spec.tail)
        if (b.tail - b.head).length < min_len:
            b.tail = b.head + Vector((0.0, 0.0, obj.voxel.voxel_size))
        b.use_deform = spec.deform
        if spec.radius is not None:
            b["voxel_radius"] = float(spec.radius)
        if spec.name in hinges:
            b["voxel_hinge"] = True  # elbow/knee: bends smoothly over several slices
        if spec.parent:
            b.parent = made[spec.parent]
            b.use_connect = spec.connect
        made[spec.name] = b
    setups = _add_ik_bones(data, kind, obj.voxel.voxel_size) if ik else []
    bpy.ops.object.mode_set(mode='OBJECT')
    _add_ik_constraints(arm, setups, obj.voxel.limit_reach)

    arm.select_set(False)
    obj.select_set(True)
    context.view_layer.objects.active = obj
    bind(obj, arm)
    return arm


# ----------------------------------------------------------------------------
# Keep weights in sync when bones are edited


def _refresh_stale():
    if state.ACTIVE is not None:
        return None
    for obj in bpy.data.objects:
        if obj.type != 'MESH' or not obj.voxel.is_voxel or not obj.voxel.bound:
            continue
        arm = get_armature(obj)
        if arm is None or arm.mode == 'EDIT':
            continue
        if rest_hash(arm) != obj.voxel.rest_hash:
            mesh.rebuild(obj)
    return None


@persistent
def on_depsgraph_update(scene, depsgraph):
    if state.ACTIVE is not None:
        return
    if any(isinstance(u.id, bpy.types.Armature) for u in depsgraph.updates):
        if not bpy.app.timers.is_registered(_refresh_stale):
            bpy.app.timers.register(_refresh_stale, first_interval=0.1)


# ----------------------------------------------------------------------------
# Operators


class VoxelObjectOperator:
    @classmethod
    def poll(cls, context):
        return mesh.is_voxel(context.active_object) and state.ACTIVE is None


class VOXEL_OT_add_armature(VoxelObjectOperator, bpy.types.Operator):
    bl_idname = "voxel.add_armature"
    bl_label = "Add Armature"
    bl_description = "Create an armature fitted to this voxel model and bind it with voxel weights"
    bl_options = {'REGISTER', 'UNDO'}

    template: EnumProperty(name="Template", items=TEMPLATE_ITEMS, default='HUMANOID')
    chain_count: IntProperty(name="Bones", default=4, min=1, max=64)
    ik: BoolProperty(name="IK Controls", default=True,
                     description="Add IK controls for arms and legs (Humanoid and Quadruped)")

    def execute(self, context):
        obj = context.active_object
        arm = create_armature(context, obj, self.template, self.chain_count, self.ik)
        if arm is None:
            self.report({'ERROR'}, "The model is empty - add some voxels first")
            return {'CANCELLED'}
        self.report({'INFO'}, f"Added '{arm.name}'. Use Pose Mode to animate, Bones mode (4) to fix weights")
        return {'FINISHED'}


class VOXEL_OT_bind(VoxelObjectOperator, bpy.types.Operator):
    bl_idname = "voxel.bind"
    bl_label = "Bind"
    bl_description = "Parent this model to the armature and generate weights from the voxels"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        obj = context.active_object
        arm = get_armature(obj)
        if arm is None:
            self.report({'ERROR'}, "Choose an armature first")
            return {'CANCELLED'}
        bind(obj, arm)
        return {'FINISHED'}


class VOXEL_OT_unbind(VoxelObjectOperator, bpy.types.Operator):
    bl_idname = "voxel.unbind"
    bl_label = "Unbind"
    bl_description = "Remove the armature modifier, vertex groups and parenting (keeps painted bones)"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        unbind(context.active_object)
        return {'FINISHED'}


class VOXEL_OT_refresh_weights(VoxelObjectOperator, bpy.types.Operator):
    bl_idname = "voxel.refresh_weights"
    bl_label = "Refresh Weights"
    bl_description = "Recompute skin weights from the voxels and current bone positions"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        mesh.rebuild(context.active_object)
        return {'FINISHED'}


class VOXEL_OT_clear_bone_paint(VoxelObjectOperator, bpy.types.Operator):
    bl_idname = "voxel.clear_bone_paint"
    bl_label = "Clear Painted Bones"
    bl_description = "Forget all hand-painted bone assignments (voxels go back to nearest bone)"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        obj = context.active_object
        grid, bones = mesh.load_arrays(obj)
        bones[:] = 0
        mesh.save_arrays(obj, grid, bones)
        obj.voxel.bone_slots.clear()
        mesh.rebuild(obj)
        return {'FINISHED'}


class VOXEL_OT_rig_mode(bpy.types.Operator):
    bl_idname = "voxel.rig_mode"
    bl_label = "Switch Rig Mode"
    bl_description = "Jump between the voxel model and its armature"
    bl_options = {'REGISTER', 'UNDO'}

    mode: EnumProperty(items=[
        ('POSE', "Pose", "Pose the armature", 'POSE_HLT', 0),
        ('EDIT', "Edit Bones", "Adjust bone positions (weights update when you leave)", 'EDITMODE_HLT', 1),
        ('VOXELS', "Back to Voxels", "Return to the voxel model in Object Mode", 'OBJECT_DATAMODE', 2),
    ])
    target: StringProperty(options={'HIDDEN'})

    @classmethod
    def poll(cls, context):
        return state.ACTIVE is None and context.active_object is not None

    def execute(self, context):
        active = context.active_object
        if self.mode == 'VOXELS':
            obj = bpy.data.objects.get(self.target)
            if obj is None and active.type == 'ARMATURE':
                objs = bound_objects(active)
                obj = objs[0] if objs else None
            if obj is None:
                return {'CANCELLED'}
            if active.mode != 'OBJECT':
                bpy.ops.object.mode_set(mode='OBJECT')
            for o in context.selected_objects:
                o.select_set(False)
            context.view_layer.objects.active = obj
            obj.select_set(True)
            return {'FINISHED'}

        arm = active if active.type == 'ARMATURE' else (get_armature(active) if mesh.is_voxel(active) else None)
        if arm is None:
            return {'CANCELLED'}
        if active.mode != 'OBJECT':
            bpy.ops.object.mode_set(mode='OBJECT')
        for o in context.selected_objects:
            o.select_set(False)
        arm.hide_set(False)
        context.view_layer.objects.active = arm
        arm.select_set(True)
        bpy.ops.object.mode_set(mode=self.mode)
        return {'FINISHED'}


class VOXEL_OT_reset_pose(bpy.types.Operator):
    bl_idname = "voxel.reset_pose"
    bl_label = "Reset Pose"
    bl_description = "Clear all bone transforms back to the rest pose"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        o = context.active_object
        return o is not None and o.type == 'ARMATURE'

    def execute(self, context):
        for pb in context.active_object.pose.bones:
            pb.location = (0, 0, 0)
            pb.rotation_quaternion = (1, 0, 0, 0)
            pb.rotation_euler = (0, 0, 0)
            pb.scale = (1, 1, 1)
        return {'FINISHED'}


classes = (
    VOXEL_OT_add_armature,
    VOXEL_OT_bind,
    VOXEL_OT_unbind,
    VOXEL_OT_refresh_weights,
    VOXEL_OT_clear_bone_paint,
    VOXEL_OT_rig_mode,
    VOXEL_OT_reset_pose,
)


def register():
    for c in classes:
        bpy.utils.register_class(c)
    bpy.app.handlers.depsgraph_update_post.append(on_depsgraph_update)


def unregister():
    if on_depsgraph_update in bpy.app.handlers.depsgraph_update_post:
        bpy.app.handlers.depsgraph_update_post.remove(on_depsgraph_update)
    if bpy.app.timers.is_registered(_refresh_stale):
        bpy.app.timers.unregister(_refresh_stale)
    for c in reversed(classes):
        bpy.utils.unregister_class(c)
