"""Drives the modal editor with simulated input.
Run: blender --factory-startup --enable-event-simulate --python tests/test_interactive.py -- OUTDIR
"""
import os, sys, traceback
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import bpy
import numpy as np
from bpy_extras import view3d_utils
from mathutils import Vector
import voxel_editor
from voxel_editor import state, mesh

OUT = sys.argv[sys.argv.index("--") + 1] if "--" in sys.argv else "/tmp"
voxel_editor.register()
results = []
def check(name, cond):
    results.append((name, bool(cond)))
    print(("PASS " if cond else "FAIL ") + name, flush=True)

W = {}
def ctx():
    win = bpy.context.window_manager.windows[0]
    area = max((a for a in win.screen.areas if a.type == 'VIEW_3D'), key=lambda a: a.width * a.height)
    region = next(r for r in area.regions if r.type == 'WINDOW')
    return win, area, region

def screen_pos(world):
    win, area, region = ctx()
    p = view3d_utils.location_3d_to_region_2d(region, area.spaces.active.region_3d, world)
    return int(p.x + region.x), int(p.y + region.y)

def grid_world(cell_center):
    s = state.ACTIVE
    return mesh.grid_to_world(s.obj) @ Vector(cell_center)

def ev(t, v, x=None, y=None, **mods):
    win = ctx()[0]
    if x is None:
        x, y = W.get("mouse", (0, 0))
    W["mouse"] = (x, y)
    win.event_simulate(t, v, x=x, y=y, **mods)

def move(x, y): ev('MOUSEMOVE', 'NOTHING', x, y)
def click(x, y, **m):
    move(x, y); ev('LEFTMOUSE', 'PRESS', x, y, **m); ev('LEFTMOUSE', 'RELEASE', x, y, **m)
def key(k, **m):
    ev(k, 'PRESS', **m); ev(k, 'RELEASE', **m)
def shot(name):
    win, area, region = ctx()
    with bpy.context.temp_override(window=win, area=area, region=region):
        bpy.ops.screen.screenshot_area(filepath=os.path.join(OUT, name))

def count(): return int((state.ACTIVE.grid > 0).sum())

steps = []
def step(fn): steps.append(fn); return fn

@step
def setup():
    win, area, region = ctx()
    with bpy.context.temp_override(window=win, area=area, region=region):
        for o in list(bpy.data.objects):
            bpy.data.objects.remove(o)
        bpy.ops.voxel.new('EXEC_DEFAULT', dims=(16, 16, 16), start='BLOCK', edit=True)
    sp = area.spaces.active
    r3 = sp.region_3d
    r3.view_location = (0, 0, 0.4)
    r3.view_distance = 3.5
    from mathutils import Euler
    r3.view_rotation = Euler((1.1, 0, 0.7)).to_quaternion()
    r3.view_perspective = 'PERSP'
    check("session started", state.ACTIVE is not None)
    t = bpy.context.scene.voxel_tool
    check("starts in box attach", (t.mode, t.shape) == ('ATTACH', 'BOX'))
    check("wireframe on by default", state.ACTIVE.obj.show_wire and state.ACTIVE.obj.show_all_edges)
    move(*screen_pos(grid_world((8.0, 8.0, 8.0))))
    key('W')
    W["n0"] = count()

@step
def attach_top():
    check("W toggles wireframe", not state.ACTIVE.obj.show_wire)
    key('W'); key('V')
    # block is 4x4x4 at cells 6..9, z 0..3 -> top face centre of cell (7,7,3)
    x, y = screen_pos(grid_world((7.5, 7.5, 4.0)))
    W["top"] = (x, y)
    click(x, y)

@step
def check_attach():
    check("attach adds one voxel on top", count() == W["n0"] + 1 and state.ACTIVE.grid[7, 7, 4] > 0)
    key('TWO')  # erase

@step
def erase():
    x, y = screen_pos(grid_world((7.5, 7.5, 5.0)))
    click(x, y)

@step
def check_erase():
    check("erase removes it", count() == W["n0"] and state.ACTIVE.grid[7, 7, 4] == 0)
    key('Z', ctrl=True)

@step
def check_undo():
    check("ctrl+z restores", count() == W["n0"] - 0 + 1)
    key('Z', ctrl=True, shift=True)

@step
def check_redo():
    check("ctrl+shift+z redoes", count() == W["n0"])
    key('ONE'); key('B')

@step
def box_drag():
    s = state.ACTIVE
    a = screen_pos(grid_world((2.5, 2.5, 0.0)))
    b = screen_pos(grid_world((4.5, 5.5, 0.0)))
    move(*a); ev('LEFTMOUSE', 'PRESS', *a)
    move((a[0] + b[0]) // 2, (a[1] + b[1]) // 2); move(*b)

@step
def box_shot():
    shot("box_drag.png")
    ev('LEFTMOUSE', 'RELEASE')

@step
def check_box():
    g = state.ACTIVE.grid
    print("box cells:", np.argwhere(g[:, :, 0] > 0).tolist()[:40], count() - W["n0"]); check("box on floor 3x4", (g[2:5, 2:6, 0] > 0).all() and count() == W["n0"] + 12)
    W["n1"] = count()
    key('F')

@step
def face_extrude():
    x, y = W["top"]
    move(x, y); ev('LEFTMOUSE', 'PRESS', x, y)
    # drag upward on screen ~ along +Z
    p0 = screen_pos(grid_world((7.5, 7.5, 4.5)))
    p1 = screen_pos(grid_world((7.5, 7.5, 5.5)))
    dy = p1[1] - p0[1]; dx = p1[0] - p0[0]
    move(x + dx, y + dy); move(x + 2 * dx, y + 2 * dy)

@step
def face_shot():
    shot("face_extrude.png")
    ev('LEFTMOUSE', 'RELEASE')

@step
def check_face():
    g = state.ACTIVE.grid
    check("face extrude 3 layers of 4x4", count() == W["n1"] + 48 and (g[6:10, 6:10, 4:7] > 0).all())
    key('THREE'); key('V')
    # pick palette colour from the overlay: column 3 row 3 -> index 27
    from voxel_editor import draw
    x0, y0, cs = draw.palette_layout(state.ACTIVE)
    win, area, region = ctx()
    idx = 3 * 8 + 3
    px = region.x + x0 + 3 * cs + cs // 2
    py = region.y + y0 + (7 - 3) * cs + cs // 2
    click(px, py)
    W["idx"] = idx

@step
def check_palette_click():
    check("overlay palette click selects color", bpy.context.scene.voxel_tool.color_index == W["idx"])
    x, y = screen_pos(grid_world((7.5, 7.5, 7.0)))
    click(x, y)

@step
def check_paint():
    check("paint recolors", state.ACTIVE.grid[7, 7, 6] == W["idx"])
    key('X')  # mirror x
    key('ONE')

@step
def mirror_attach():
    x, y = screen_pos(grid_world((3.5, 2.5, 1.0)))  # top of box at (3,2,0)
    click(x, y)

@step
def check_mirror():
    g = state.ACTIVE.grid
    check("mirror X attach", g[3, 2, 1] > 0 and g[12, 2, 1] > 0)
    x, y = screen_pos(grid_world((7.5, 7.5, 7.0)))
    move(x, y)

@step
def hover_shot():
    shot("hover.png")
    key('TAB')

@step
def check_exit():
    check("tab exits", state.ACTIVE is None)
    obj = bpy.context.active_object
    check("wireframe restored on exit", not obj.show_wire)
    g, _ = mesh.load_arrays(obj)
    check("edits committed", g[7, 7, 6] == W["idx"])
    # rig + bone paint session
    win, area, region = ctx()
    with bpy.context.temp_override(window=win, area=area, region=region):
        bpy.ops.voxel.add_armature(template='CHAIN', chain_count=3)
        bpy.ops.voxel.paint_bones()
    check("bone paint session", state.ACTIVE is not None and state.ACTIVE.bone_view)

@step
def bone_shot():
    x, y = screen_pos(grid_world((7.5, 7.5, 7.0)))
    move(x, y)

@step
def bone_shot2():
    shot("bones.png")
    bpy.context.scene.voxel_tool.active_bone = "bone.01"
    x, y = screen_pos(grid_world((7.5, 7.5, 7.0)))
    click(x, y)

@step
def check_bone_paint():
    s = state.ACTIVE
    from voxel_editor import rig
    check("bone painted", rig.slot_name(s.obj, int(s.bones[7, 7, 6])) == "bone.01")
    key('ESC')

@step
def check_final():
    obj = bpy.context.active_object
    check("esc exits, still bound", state.ACTIVE is None and obj.voxel.bound and len(obj.vertex_groups) >= 3)
    # Material preview look
    win, area, region = ctx()
    move(5, 5)
    area.spaces.active.shading.type = 'MATERIAL'

@step
def final_shot():
    shot("material.png")
    ctx()[1].spaces.active.show_region_ui = True
    for o in bpy.data.objects: o.select_set(False)
    v = [o for o in bpy.data.objects if o.voxel.is_voxel][0]
    bpy.context.view_layer.objects.active = v

@step
def move_panels_to_item_tab():
    from voxel_editor import ui
    for c in reversed(ui.classes): bpy.utils.unregister_class(c)
    for n in ("VIEW3D_PT_transform", "OBJECT_PT_transform"):
        c = getattr(bpy.types, n, None)
        if c: bpy.utils.unregister_class(c)
    hide = {"VIEW3D_PT_voxel_tools", "VIEW3D_PT_voxel_rig"}
    for c in ui.classes:
        c.bl_category = "Item"
        if c.__name__ in hide: c.bl_options = {'DEFAULT_CLOSED'}
        elif c.__name__ != "VIEW3D_PT_voxel": c.bl_options = set()
        bpy.utils.register_class(c)

@step
def sidebar_shot():
    shot("sidebar.png")

@step
def ik_grab_hand():
    # A real G move on a T-posed humanoid's IK hand must follow the drag (Limit Reach once
    # made the hand jump up 45 degrees during interactive transforms).
    from voxel_editor import vox, rig
    from mathutils import Vector
    g = np.zeros((32, 32, 32), np.uint8)
    g[13:15, 14:16, 0:7] = 5; g[17:19, 14:16, 0:7] = 5; g[13:19, 14:16, 7:12] = 20
    g[6:26, 14:16, 12:14] = 30; g[15:17, 14:16, 14:15] = 12; g[13:19, 12:18, 15:21] = 60
    win, area, region = ctx()
    with bpy.context.temp_override(window=win, area=area, region=region):
        if bpy.context.object and bpy.context.object.mode != 'OBJECT':
            bpy.ops.object.mode_set(mode='OBJECT')
        o = vox.new_voxel_object(bpy.context, "TPose", g, location=(5, 0, 0))
        for x in bpy.context.selected_objects: x.select_set(False)
        bpy.context.view_layer.objects.active = o; o.select_set(True)
        arm = rig.create_armature(bpy.context, o, 'HUMANOID')
        bpy.context.view_layer.objects.active = arm
        bpy.ops.object.mode_set(mode='POSE')
        bpy.ops.pose.select_all(action='DESELECT')
        P = arm.pose.bones
        P["hand_ik.L"].select = True
        arm.data.bones.active = arm.data.bones["hand_ik.L"]
        before = P["hand_ik.L"].head.copy()
        bpy.ops.transform.translate(value=(0, 0, -0.1))
        bpy.context.view_layer.update()
        moved = P["hand_ik.L"].head - before
        print("ik grab moved hand by", tuple(round(c, 3) for c in moved))
        check("IK hand follows a G move", moved.z < -0.05 and abs(moved.x) < 0.05)
        bpy.ops.transform.translate(value=(1.0, 0, 0.1))
        bpy.context.view_layer.update()
        d = (P["hand_ik.L"].head - P["upper_arm.L"].head).length
        reach = P["upper_arm.L"].length + P["forearm.L"].length
        check("dragged-out hand stops at reach", d <= reach + 1e-3)
        bpy.ops.object.mode_set(mode='OBJECT')

def runner():
    try:
        if steps:
            fn = steps.pop(0); print("STEP", fn.__name__, flush=True); fn()
            return 0.4
    except Exception:
        traceback.print_exc()
        results.append(("exception", False))
    fails = [n for n, ok in results if not ok]
    print(f"==== {'ALL PASSED' if not fails else 'FAILED: ' + ', '.join(fails)} ====", flush=True)
    bpy.ops.wm.quit_blender()
    return None

bpy.app.timers.register(runner, first_interval=2.0)
