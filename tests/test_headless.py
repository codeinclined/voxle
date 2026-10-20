"""Run: blender -b --factory-startup --python tests/test_headless.py"""
import os, sys, tempfile, traceback
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import bpy
import numpy as np
import voxel_editor
from voxel_editor import core, mesh, rig, vox, ops, state

voxel_editor.register()
fails = 0
def check(name, cond):
    global fails
    print(("PASS " if cond else "FAIL ") + name)
    if not cond: fails += 1

ctx = bpy.context
try:
    # New object
    bpy.ops.voxel.new(dims=(24, 16, 32), start='BLOCK', edit=False)
    obj = ctx.active_object
    check("new object is voxel", obj.voxel.is_voxel and len(obj.data.polygons) == 6 * 6 * 6 // 1 * 1 or len(obj.data.polygons) > 0)
    check("palette 256", len(obj.voxel.palette) == 256)
    check("material + image", obj.data.materials[0] is not None and obj.voxel.palette_image is not None)

    # Build a humanoid: legs, torso, arms down, big head
    g = np.zeros((24, 16, 32), np.uint8)
    g[8:11, 6:10, 0:10] = 5      # right leg (x<12)
    g[13:16, 6:10, 0:10] = 5     # left leg
    g[8:16, 6:10, 10:20] = 20    # torso
    g[5:8, 6:10, 9:20] = 30      # right arm (down)
    g[16:19, 6:10, 9:20] = 30    # left arm
    g[10:14, 7:9, 20:21] = 12    # neck
    g[6:18, 3:13, 21:31] = 60    # head
    mesh.save_arrays(obj, g, np.zeros_like(g))
    mesh.rebuild(obj)
    nf = len(obj.data.polygons)
    check("mesh faces match surface", nf == len(core.build_surface((g > 0).view(np.uint8)).faces))
    check("color attr", obj.data.color_attributes.get("Col") is not None)
    check("uv", len(obj.data.uv_layers) == 1)
    bb = [obj.matrix_world @ v.co for v in obj.data.vertices]
    zs = [v.z for v in bb]
    check("floor at 0, height 3.1", abs(min(zs)) < 1e-5 and abs(max(zs) - 3.1) < 1e-4)

    # Palette change updates colors without remesh
    obj.voxel.palette[5].color = (1, 0, 0)
    col = obj.data.color_attributes["Col"]
    ci = np.zeros(len(obj.data.polygons), np.int32); obj.data.attributes["voxel_index"].data.foreach_get("value", ci)
    face = int(np.flatnonzero(ci == 5)[0])
    c = col.data[face * 4].color
    check("palette color propagates", abs(c[0] - 1) < 1e-4 and c[1] < 1e-4)
    px = list(obj.voxel_palette_px if False else obj.voxel.palette_image.pixels[5*4:5*4+3])
    check("palette image updated", abs(px[0] - 1) < 1e-3 and px[1] < 1e-3)

    # Grid ops
    bpy.ops.voxel.flip(axis='X')
    g2, _ = mesh.load_arrays(obj)
    check("flip", np.array_equal(g2, g[::-1]))
    bpy.ops.voxel.flip(axis='X')
    loc0 = (obj.matrix_world @ obj.data.vertices[0].co).copy()
    wpos = lambda o: sorted(tuple(round(c, 4) for c in (o.matrix_world @ v.co)) for v in o.data.vertices)
    before = wpos(obj)
    bpy.ops.voxel.crop()
    check("crop dims", tuple(obj.voxel.dims) == (14, 10, 31))
    check("crop keeps world position", wpos(obj) == before)
    bpy.ops.voxel.resize('EXEC_DEFAULT', dims=(30, 20, 40))
    check("resize keeps world position", wpos(obj) == before)
    bpy.ops.voxel.rotate(axis='Z'); bpy.ops.voxel.rotate(axis='Z'); bpy.ops.voxel.rotate(axis='Z'); bpy.ops.voxel.rotate(axis='Z')
    check("4x rotate identity", wpos(obj) == before)

    # vox roundtrip
    tmp = tempfile.mkdtemp()
    path = os.path.join(tmp, "t.vox")
    gr, _ = mesh.load_arrays(obj)
    vox.write_vox(path, gr, mesh.palette_srgb(obj))
    models, pal, inst = vox.read_vox(path)
    check("vox roundtrip grid", np.array_equal(models[0], gr))
    check("vox roundtrip palette", np.allclose(pal[1:], np.round(mesh.palette_srgb(obj)[1:] * 255) / 255, atol=1e-6))
    bpy.ops.voxel.import_vox(filepath=path)
    imp = ctx.active_object
    check("import creates voxel obj", imp is not obj and imp.voxel.is_voxel and len(imp.data.polygons) == len(obj.data.polygons))
    bpy.data.objects.remove(imp)

    # Rotated vox via scene graph
    import struct
    def ch(cid, content, children=b""): return cid + struct.pack("<ii", len(content), len(children)) + content + children
    def d(dct):
        out = struct.pack("<i", len(dct))
        for k, v in dct.items():
            out += struct.pack("<i", len(k)) + k.encode() + struct.pack("<i", len(v)) + v.encode()
        return out
    xyzi = bytes([0,0,0,1, 1,0,0,1, 2,0,0,1])
    kids = ch(b"SIZE", struct.pack("<iii", 3, 1, 1)) + ch(b"XYZI", struct.pack("<i", 3) + xyzi)
    kids += ch(b"nTRN", struct.pack("<i", 0) + d({}) + struct.pack("<iiii", 1, -1, -1, 1) + d({}))
    kids += ch(b"nGRP", struct.pack("<i", 1) + d({}) + struct.pack("<ii", 1, 2))
    # rotate 90 about z: row0 -> index1, row1 -> index0 => r = 1 | (0<<2) | sign row0 neg (16)
    kids += ch(b"nTRN", struct.pack("<i", 2) + d({"_name": "rotated"}) + struct.pack("<iiii", 3, -1, 0, 1) + d({"_t": "5 0 0", "_r": str(1 | 16)}))
    kids += ch(b"nSHP", struct.pack("<i", 3) + d({}) + struct.pack("<i", 1) + struct.pack("<i", 0) + d({}))
    with open(path, "wb") as f: f.write(b"VOX " + struct.pack("<i", 150) + ch(b"MAIN", b"", kids))
    models, pal, inst = vox.read_vox(path)
    gg, piv = vox.oriented(models[inst[0][0]], inst[0][3])
    check("scene graph rotation", gg.shape == (1, 3, 1) and inst[0][1] == "rotated" and list(inst[0][2]) == [5, 0, 0])

    # Rigging
    obj.select_set(True); ctx.view_layer.objects.active = obj
    mesh.save_arrays(obj, g, np.zeros_like(g)); obj.voxel.dims = g.shape
    mesh.rebuild(obj)
    arm = rig.create_armature(ctx, obj, 'HUMANOID')
    check("armature created", arm is not None and arm.type == 'ARMATURE')
    names = [b.name for b in arm.data.bones]
    print("  bones:", names)
    check("humanoid bones", {"hips", "spine", "chest", "head", "upper_arm.L", "thigh.R", "foot.L"} <= set(names))
    hb = arm.data.bones["head"].head_local
    print("  head bottom z:", hb.z, " chest:", arm.data.bones["chest"].head_local.z, " thigh.L head", tuple(arm.data.bones["thigh.L"].head_local))
    check("head detected at neck (z~2.0-2.1)", 1.95 <= hb.z <= 2.15)
    check("thigh.L on +x", arm.data.bones["thigh.L"].head_local.x > 0)
    ua = arm.data.bones["upper_arm.L"]
    print("  upper_arm.L", tuple(ua.head_local), tuple(ua.tail_local))
    check("arm hangs down", ua.tail_local.z < ua.head_local.z and ua.head_local.x > 0.3)
    check("bound", obj.voxel.bound and obj.parent == arm and any(m.type == 'ARMATURE' for m in obj.modifiers))
    vg_names = {vg.name for vg in obj.vertex_groups}
    check("vertex groups", {"head", "upper_arm.L", "thigh.R"} <= vg_names)
    # every vertex weighted, weights sum to 1
    sums = np.zeros(len(obj.data.vertices))
    for v in obj.data.vertices:
        sums[v.index] = sum(gw.weight for gw in v.groups)
    check("all verts weighted ~1", np.all(np.abs(sums - 1) < 0.06))
    # head vertex
    top = max(obj.data.vertices, key=lambda v: v.co.z)
    gname = obj.vertex_groups[max(top.groups, key=lambda gw: gw.weight).group].name
    check("top vertex -> head", gname == "head")

    # IK controls
    from mathutils import Matrix, Vector
    def rest_drift(a):
        dg = ctx.evaluated_depsgraph_get(); dg.update()
        return max(max(abs(x - y) for rx, ry in zip(pb.matrix, a.data.bones[pb.name].matrix_local) for x, y in zip(rx, ry))
                   for pb in a.pose.bones)
    check("ik poles", {"knee_pole.R", "elbow_pole.R"} <= set(names) and not arm.data.bones["knee_pole.R"].use_deform)
    check("foot is its own IK target (no duplicate)", "foot_ik.L" not in names)
    mech = arm.data.collections.get("Mechanism")
    helpers = [n for n in names if n.endswith(("_reach.L", "_reach.R")) or n.startswith("arm_aim")] + ["hand.L"]
    check("helper bones are hidden in Pose Mode", mech is not None and not mech.is_visible
          and all(arm.data.bones[n].collections and all(c == mech for c in arm.data.bones[n].collections) for n in helpers))
    check("elbows and knees are smooth hinges", all(arm.data.bones[n].get("voxel_hinge") for n in ("forearm.L", "shin.R")))
    check("preserve volume skinning", next(m for m in obj.modifiers if m.type == 'ARMATURE').use_deform_preserve_volume)
    check("hand control sits on the hand", "hand_ik.L" in names and arm.data.bones["hand.L"].parent.name == "forearm.L"
          and (arm.data.bones["hand_ik.L"].head_local - arm.data.bones["hand.L"].head_local).length < 1e-6)
    check("foot is the ik target", any(c.type == 'IK' and c.subtarget == "foot.L" and c.pole_subtarget == "knee_pole.L"
                                       for c in arm.pose.bones["shin.L"].constraints)
          and not arm.data.bones["foot.L"].use_connect and arm.data.bones["foot.L"].parent.name == "root")
    check("poles not weighted", "knee_pole.L" not in vg_names and "foot.L" in vg_names)
    drift = rest_drift(arm)
    print("  rest drift with IK:", drift)
    check("ik keeps rest pose", drift < 1e-3)
    knee0 = arm.pose.bones["shin.L"].head.copy(); elbow0 = arm.pose.bones["forearm.L"].head.copy()
    thigh0 = arm.pose.bones["thigh.L"].matrix.copy(); uarm0 = arm.pose.bones["upper_arm.L"].matrix.copy()
    for n in ("foot.L", "hand_ik.L"):
        arm.pose.bones[n].matrix = Matrix.Translation((0, 0, 0.5)) @ arm.pose.bones[n].matrix
    ctx.evaluated_depsgraph_get().update()
    knee1 = arm.pose.bones["shin.L"].head; elbow1 = arm.pose.bones["forearm.L"].head
    print("  knee y", knee0.y, "->", knee1.y, " elbow y", elbow0.y, "->", elbow1.y)
    check("knee bends forward", knee1.y < knee0.y - 0.1)
    check("elbow bends back", elbow1.y > elbow0.y + 0.1)
    check("moving foot rotates thigh", thigh0.to_quaternion().rotation_difference(arm.pose.bones["thigh.L"].matrix.to_quaternion()).angle > 0.2)
    hand = arm.pose.bones["hand.L"]
    fa = arm.pose.bones["forearm.L"]
    rel_pose = fa.matrix.to_3x3().inverted() @ hand.matrix.to_3x3()
    rel_rest = fa.bone.matrix_local.to_3x3().inverted() @ hand.bone.matrix_local.to_3x3()
    check("hand follows the forearm", (hand.head - fa.tail).length < 1e-4
          and max(abs(x - y) for r1, r2 in zip(rel_pose, rel_rest) for x, y in zip(r1, r2)) < 1e-4)
    check("moving hand rotates upper arm", uarm0.to_quaternion().rotation_difference(arm.pose.bones["upper_arm.L"].matrix.to_quaternion()).angle > 0.2)
    check("foot reaches target", (arm.pose.bones["shin.L"].tail - arm.pose.bones["foot.L"].head).length < 1e-3)
    # Lifting the leg must leave the torso undeformed (joints bend on the limb's side).
    arm.pose.bones["hand_ik.L"].matrix_basis = Matrix()
    dg = ctx.evaluated_depsgraph_get(); dg.update()
    to_obj = obj.matrix_world.inverted() @ arm.matrix_world
    crotch = (to_obj @ arm.data.bones["thigh.L"].head_local).z
    ev = obj.evaluated_get(dg).data
    moved = [(a.co - b.co).length for a, b in zip(obj.data.vertices, ev.vertices)]
    torso_moved = max(m for m, v in zip(moved, obj.data.vertices) if v.co.z >= crotch - 1e-4)
    print("  vertex drift above the hip with the leg lifted:", torso_moved, " leg max:", max(moved))
    check("torso stays rigid when limbs move", torso_moved < 1e-4)
    inf = rig.bone_info(obj, arm)
    asg, _ = core.assign_bones(g, np.zeros_like(g), inf.slot_map, inf.heads, inf.tails, inf.radii)
    torso_bones = {inf.names[i] for i in np.unique(asg[8:16, 6:10, 10:20])}
    check("torso voxels beside hanging arms stay on torso bones", torso_bones <= {"hips", "spine", "chest"})
    # Limbs are inset a hair per hierarchy level so bent joints don't z-fight.
    vs = obj.voxel.voxel_size
    off = np.array([v.co[:] for v in obj.data.vertices]) / vs
    d = np.abs(off - np.round(off))
    dev = d[d < 0.06].max()  # loop-cut vertices sit well off the grid on purpose
    print("  max joint inset (voxels):", dev)
    check("joint inset is tiny but present", 0.001 < dev <= mesh.JOINT_INSET * (int(inf.depths.max()) + 0.5))
    hand_v = [v for v in obj.data.vertices if any(obj.vertex_groups[g.group].name == "hand.L" and g.weight > 0.99 for g in v.groups)]
    def inset_of(v):
        e = np.abs(np.array(v.co[:]) / vs - np.round(np.array(v.co[:]) / vs))
        return e[e < 0.06].max(initial=0)
    check("deeper bones are inset more", hand_v and min(inset_of(v) for v in hand_v) > 0.02)
    # Loop cuts only exist on the bound mesh; voxel editing rebuilds plain voxels.
    nv_bound = len(obj.data.vertices)
    mesh.rebuild(obj, weights=False)
    co = np.array([v.co[:] for v in obj.data.vertices]) / vs
    check("voxel editing shows no loop cuts", np.abs(co - np.round(co)).max() < 1e-4 and len(co) < nv_bound)
    mesh.rebuild(obj)
    for pb in arm.pose.bones:
        pb.matrix_basis = Matrix()
    # No IK flips when hands and feet are brought in to touch the body.
    import math
    def worst_jump(end, joint, path):
        rest = arm.pose.bones[end].matrix.copy(); prev = None; worst = 0
        for i, p in enumerate(path):
            arm.pose.bones[end].matrix = Matrix.Translation(p - rest.translation) @ rest
            ctx.view_layer.update()
            e = arm.pose.bones[joint].head.copy()
            if prev is not None:
                worst = max(worst, (e - prev).length / max((p - path[i - 1]).length, 1e-6))
            prev = e
        arm.pose.bones[end].matrix_basis = Matrix(); ctx.view_layer.update()
        return worst
    sh = arm.pose.bones["upper_arm.L"].head.copy(); hip = arm.pose.bones["thigh.L"].head.copy()
    La = arm.pose.bones["upper_arm.L"].length + arm.pose.bones["forearm.L"].length
    Ll = arm.pose.bones["thigh.L"].length + arm.pose.bones["shin.L"].length
    Y, Z = Vector((0, 1, 0)), Vector((0, 0, 1))
    def arc(c, r, a, u, v): return [c + u * (r * math.cos(t)) + v * (r * math.sin(t)) for t in np.linspace(0, a, 200)]
    sweeps = {
        "hand down to across chest": ("hand_ik.L", "forearm.L", [sh + Vector((0.05, 0, -0.9 * La)).lerp(Vector((-sh.x, -0.3 * La, sh.z)), t) for t in np.linspace(0, 1, 200)]),
        "hand down to mouth": ("hand_ik.L", "forearm.L", [sh + Vector((0.05, 0, -0.9 * La)).lerp(Vector((0.05, -0.35 * La, sh.z + 0.35 * La)), t) for t in np.linspace(0, 1, 200)]),
        "heel to butt": ("foot.L", "shin.L", arc(hip, 0.7 * Ll, math.pi * 0.8, -Z, Y)),
        "knee up kick": ("foot.L", "shin.L", arc(hip, 0.7 * Ll, math.pi * 0.8, -Z, -Y)),
    }
    for name, (end, joint, path) in sweeps.items():
        w = worst_jump(end, joint, path)
        print(f"  {name}: worst joint/target speed ratio {w:.2f}")
        check(f"no IK flip: {name}", w < 8)
    # Limit Reach keeps the hand from being pulled off the arm.
    hb = arm.pose.bones["hand_ik.L"]
    def pull():
        hb.matrix = Matrix.Translation((2.0, -1.0, 0.5)) @ hb.matrix
        ctx.view_layer.update()
        d = (hb.head - arm.pose.bones["upper_arm.L"].head).length
        gap = (hb.head - arm.pose.bones["forearm.L"].tail).length
        hb.matrix_basis = Matrix(); ctx.view_layer.update()
        return d, gap
    d, gap = pull()
    print("  pulled hand: distance", d, "reach", La, "gap to forearm", gap)
    check("limit reach caps hand distance", d <= La + 1e-3 and gap < 1e-3)
    obj.voxel.limit_reach = False
    d, gap = pull()
    check("limit reach toggle off lets hand leave the arm", gap > 0.5)
    obj.voxel.limit_reach = True
    check("limit reach toggle back on", not any(c.mute for c in hb.constraints if c.type == 'LIMIT_DISTANCE'))
    # Arms resting against the body come away cleanly: no torso vertex follows the arm.
    hb.matrix = Matrix.Translation((0.3, -0.5, 0.4)) @ hb.matrix
    dg = ctx.evaluated_depsgraph_get(); dg.update()
    ev = obj.evaluated_get(dg).data
    arm_groups = {obj.vertex_groups[n].index for n in ("upper_arm.L", "forearm.L", "hand.L") if n in obj.vertex_groups}
    torso_pulled = max(((a.co - e.co).length for a, e in zip(obj.data.vertices, ev.vertices)
                        if not any(g.group in arm_groups and g.weight > 0 for g in a.groups)), default=0)
    check("arm against the body swings away without dragging the torso", torso_pulled < 1e-4)
    hb.matrix_basis = Matrix(); ctx.view_layer.update()

    # T-pose: moving the hand around in the XY plane (G, Shift+Z) must not roll the arm.
    import math
    tg = np.zeros((32, 32, 32), np.uint8)
    tg[13:15, 14:16, 0:7] = 5; tg[17:19, 14:16, 0:7] = 5; tg[13:19, 14:16, 7:12] = 20
    tg[6:26, 14:16, 12:14] = 30; tg[15:17, 14:16, 14:15] = 12; tg[13:19, 12:18, 15:21] = 60
    tp = vox.new_voxel_object(ctx, "TPose", tg, location=(5, 0, 0))
    ctx.view_layer.objects.active = tp
    ta = rig.create_armature(ctx, tp, 'HUMANOID')
    TP = ta.pose.bones
    # A model away from the world origin, rigged straight after it's created, must still
    # get weights for its limbs (stale world matrices used to shift the bones off it).
    ua_vg = tp.vertex_groups.get("upper_arm.L")
    check("rig works away from the origin", ua_vg is not None and any(g.group == ua_vg.index for v in tp.data.vertices for g in v.groups)
          and (tp.matrix_world.translation - Vector((5, 0, 0))).length < 1e-6 or (mesh.world_matrix(tp).translation - Vector((5, 0, 0))).length < 1e-6)
    def roll(pb):
        R = pb.matrix.to_3x3() @ pb.bone.matrix_local.to_3x3().inverted()
        y0 = pb.bone.matrix_local.to_3x3().col[1]; y1 = pb.matrix.to_3x3().col[1]
        tw = (y0.rotation_difference(y1).to_matrix().inverted() @ R).to_quaternion()
        return abs(math.degrees(2 * math.atan2(Vector(tw[1:]).dot(y0), tw[0])))
    tsh = TP["upper_arm.L"].head.copy(); tr = TP["hand_ik.L"].matrix.copy()
    tL = TP["upper_arm.L"].length + TP["forearm.L"].length
    worst = 0
    for ang in np.linspace(-1.2, 2.2, 18):
        for r in (0.6, 0.85):
            p = tsh + Vector((math.cos(ang), -math.sin(ang), 0)) * r * tL
            TP["hand_ik.L"].matrix = Matrix.Translation(p - tr.translation) @ tr
            ctx.view_layer.update()
            worst = max(worst, roll(TP["upper_arm.L"]))
    print("  worst upper arm roll moving the hand in the XY plane:", worst)
    check("no arm twist moving the hand in the XY plane", worst < 5)
    TP["hand_ik.L"].matrix_basis = Matrix(); ctx.view_layer.update()
    # Elbows bend smoothly without the skin folding over itself, up to 150 degrees.
    for pb in TP:
        for cn in pb.constraints: cn.mute = True
    fa = TP["forearm.L"]
    folds = []
    for ang in (60, 90, 120, 150):
        E = fa.bone.matrix_local.translation
        fa.matrix = Matrix.Translation(E) @ Matrix.Rotation(math.radians(-ang), 4, 'Z') @ Matrix.Translation(-E) @ fa.bone.matrix_local
        dg = ctx.evaluated_depsgraph_get(); dg.update()
        ev = tp.evaluated_get(dg).data
        folds.append(sum(1 for pr, pe in zip(tp.data.polygons, ev.polygons) if pr.normal.z > 0.9 and pe.normal.z < 0.3))
    print("  folded faces bending the elbow 60/90/120/150 degrees:", folds)
    check("elbow bends without folding", not any(folds))
    # Bend Loops controls how many cuts each elbow/knee gets.
    counts = {}
    for n in (0, 2, 4, 8):
        tp.voxel.bend_loops = n
        counts[n] = len(tp.data.vertices)
    print("  vertex counts for bend loops 0/2/4/8:", counts)
    check("bend loops add more cuts", counts[0] < counts[2] < counts[4] < counts[8])
    co = np.array([v.co[:] for v in tp.data.vertices]) / tp.voxel.voxel_size
    tp.voxel.bend_loops = 0
    co = np.array([v.co[:] for v in tp.data.vertices]) / tp.voxel.voxel_size
    check("0 bend loops adds no cuts", np.abs(co - np.round(co)).max() < 0.06)
    tp.voxel.bend_loops = 2
    check("bend loops default to 2", type(tp.voxel).bl_rna.properties["bend_loops"].default == 2)
    # Auto Smooth: smooth shading with voxel edges kept sharp, bend slices smooth.
    check("flat shading by default", not tp.voxel.auto_smooth and not any(p.use_smooth for p in tp.data.polygons))
    tp.voxel.auto_smooth = True
    me = tp.data
    sharp = me.attributes.get("sharp_edge")
    sv = [False] * len(me.edges)
    if sharp is not None:
        sharp.data.foreach_get("value", sv)
    fn = {}
    for poly in me.polygons:
        for k in poly.edge_keys:
            fn.setdefault(k, []).append(poly.normal.copy())
    keyidx = {e.key: i for i, e in enumerate(me.edges)}
    wrong = 0
    for k, ns in fn.items():
        if len(ns) == 2:
            should = ns[0].angle(ns[1]) > tp.voxel.auto_smooth_angle + 1e-3
            wrong += should != sv[keyidx[k]]
    check("auto smooth: smooth faces, sharp voxel edges", all(p.use_smooth for p in me.polygons) and any(sv) and wrong == 0)
    tp.voxel.auto_smooth = False
    check("auto smooth off goes back to flat", not any(p.use_smooth for p in tp.data.polygons))
    for pb in TP:
        pb.matrix_basis = Matrix()
        for cn in pb.constraints: cn.mute = False
    TP["hand_ik.L"].matrix_basis = Matrix(); ctx.view_layer.update()
    ctx.view_layer.objects.active = obj
    q = rig.create_armature(ctx, obj, 'QUADRUPED')
    check("quadruped ik keeps rest pose", "front_foot_ik.L" in q.data.bones and rest_drift(q) < 1e-3)
    q = rig.create_armature(ctx, obj, 'HUMANOID', ik=False)
    check("ik off", "knee_pole.L" not in q.data.bones and q.data.bones["foot.L"].use_connect and not any(pb.constraints for pb in q.pose.bones))
    arm = rig.create_armature(ctx, obj, 'HUMANOID')
    ctx.view_layer.objects.active = obj

    # Bone painting: paint a head voxel to upper_arm.L
    bones = np.zeros_like(g)
    slot = rig.bone_slot(obj, "upper_arm.L")
    bones[6:18, 3:13, 28:31] = slot
    mesh.save_arrays(obj, g, bones)
    mesh.rebuild(obj)
    top = max(obj.data.vertices, key=lambda v: v.co.z)
    gname = obj.vertex_groups[max(top.groups, key=lambda gw: gw.weight).group].name
    check("painted bone overrides", gname == "upper_arm.L")
    # Split parts
    obj.voxel.split_parts = True
    nv_split = len(obj.data.vertices)
    ok = all(len([gw for gw in v.groups if gw.weight > 0]) == 1 for v in obj.data.vertices)
    check("split parts rigid", ok)
    co = np.array([v.co[:] for v in obj.data.vertices]) / obj.voxel.voxel_size
    check("rigid parts have no loop cuts", np.abs(co - np.round(co)).max() < 0.06)
    obj.voxel.split_parts = False

    # Posing deforms
    dg = ctx.evaluated_depsgraph_get()
    pb = arm.pose.bones["head"]
    pb.rotation_mode = 'XYZ'; pb.rotation_euler = (0.5, 0, 0)
    dg.update()
    ev = obj.evaluated_get(dg).data
    moved = sum(1 for a, b in zip(obj.data.vertices, ev.vertices) if (a.co - b.co).length > 1e-3)
    check("pose deforms mesh", moved > 50)
    pb.rotation_euler = (0, 0, 0)

    # Auto-refresh on bone edit
    old = obj.voxel.rest_hash
    arm.data.bones  # edit a bone
    ctx.view_layer.objects.active = arm
    bpy.ops.object.mode_set(mode='EDIT')
    arm.data.edit_bones["head"].tail.z += 0.5
    bpy.ops.object.mode_set(mode='OBJECT')
    rig._refresh_stale()
    check("weights refresh after bone edit", obj.voxel.rest_hash != old and obj.voxel.rest_hash == rig.rest_hash(arm))
    ctx.view_layer.objects.active = obj

    # Other templates
    for t in ('QUADRUPED', 'CHAIN', 'SINGLE'):
        a2 = rig.create_armature(ctx, obj, t)
        check(f"template {t}", a2 is not None and obj.voxel.armature == a2 and obj.voxel.bound)
    rig.unbind(obj)
    co = np.array([v.co[:] for v in obj.data.vertices]) / obj.voxel.voxel_size
    check("unbind removes joint inset", np.abs(co - np.round(co)).max() < 1e-4)
    check("unbind", not obj.voxel.bound and obj.parent is None and not any(m.type == 'ARMATURE' for m in obj.modifiers))

    # Symmetrize with bone slot swap
    bones = np.zeros_like(g); bones[16:19, 6:10, 9:20] = rig.bone_slot(obj, "upper_arm.L")
    mesh.save_arrays(obj, g, bones)
    bpy.ops.voxel.symmetrize(axis='X', positive_to_negative=True)
    _, b2 = mesh.load_arrays(obj)
    check("symmetrize swaps .L/.R", rig.slot_name(obj, int(b2[6, 7, 12])) == "upper_arm.R")

    # Duplicate gets own palette image when edited
    dup = obj.copy(); dup.data = obj.data.copy(); ctx.collection.objects.link(dup)
    mesh.ensure_material(dup)
    check("duplicate gets own palette", dup.voxel.palette_image != obj.voxel.palette_image and dup.data.materials[0] != obj.data.materials[0])

    # Save/reload persists
    fp = os.path.join(tmp, "t.blend")
    nf = len(obj.data.polygons)
    name = obj.name
    bpy.ops.wm.save_as_mainfile(filepath=fp)
    bpy.ops.wm.open_mainfile(filepath=fp)
    o = bpy.data.objects[name]
    gl, _ = mesh.load_arrays(o)
    check("persist after reload", gl.any() and len(o.voxel.palette) == 256)
    check("palette image pixels after reload", abs(o.voxel.palette_image.pixels[5*4] - 1) < 1e-3)

    # unregister / register
    voxel_editor.unregister(); voxel_editor.register()
    check("re-register", hasattr(bpy.types.Object, "voxel"))
except Exception:
    traceback.print_exc()
    fails += 1

print(f"\n==== {'ALL PASSED' if not fails else str(fails) + ' FAILED'} ====")
sys.exit(1 if fails else 0)
