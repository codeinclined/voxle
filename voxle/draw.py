"""Viewport overlays for the edit session: grid, brush preview, palette and HUD."""

import blf
import bpy
import gpu
import numpy as np
from gpu_extras.batch import batch_for_shader

from . import mesh

PAL_COLS, PAL_ROWS = 32, 8

_shaders = {}


def _shader(name):
    s = _shaders.get(name)
    if s is None:
        s = _shaders[name] = gpu.shader.from_builtin(name)
    return s


def _lines(coords, color, width=1.0):
    if not len(coords):
        return
    sh = _shader('POLYLINE_UNIFORM_COLOR')
    batch = batch_for_shader(sh, 'LINES', {"pos": coords})
    vp = gpu.state.viewport_get()
    sh.bind()
    sh.uniform_float("viewportSize", (vp[2], vp[3]))
    sh.uniform_float("lineWidth", width)
    sh.uniform_float("color", color)
    batch.draw(sh)


def _tris(coords, color):
    if not len(coords):
        return
    sh = _shader('UNIFORM_COLOR')
    batch = batch_for_shader(sh, 'TRIS', {"pos": coords})
    sh.bind()
    sh.uniform_float("color", color)
    batch.draw(sh)


def _rects(rects, colors):
    """Filled 2D rects (x, y, w, h) with one RGBA colour each."""
    if not len(rects):
        return
    r = np.asarray(rects, np.float32)
    x0, y0, x1, y1 = r[:, 0], r[:, 1], r[:, 0] + r[:, 2], r[:, 1] + r[:, 3]
    z = np.zeros_like(x0)
    corners = np.stack([
        np.stack([x0, y0, z], 1), np.stack([x1, y0, z], 1), np.stack([x1, y1, z], 1),
        np.stack([x0, y0, z], 1), np.stack([x1, y1, z], 1), np.stack([x0, y1, z], 1),
    ], 1).reshape(-1, 3)
    cols = np.repeat(np.asarray(colors, np.float32), 6, axis=0)
    sh = _shader('SMOOTH_COLOR')
    batch = batch_for_shader(sh, 'TRIS', {"pos": corners, "color": cols})
    batch.draw(sh)


def _outline(x, y, w, h, color, width=1.0):
    pts = np.array([(x, y, 0), (x + w, y, 0), (x + w, y, 0), (x + w, y + h, 0),
                    (x + w, y + h, 0), (x, y + h, 0), (x, y + h, 0), (x, y, 0)], np.float32)
    _lines(pts, color, width)


# ----------------------------------------------------------------------------
# 3D


def _grid_lines(dims):
    X, Y, Z = dims
    step = 1 if max(X, Y) <= 128 else 4
    xs = np.arange(0, X + 1, step)
    ys = np.arange(0, Y + 1, step)
    floor = []
    for x in xs:
        floor += [(x, 0, 0), (x, Y, 0)]
    for y in ys:
        floor += [(0, y, 0), (X, y, 0)]
    c = [(0, 0, 0), (X, 0, 0), (X, Y, 0), (0, Y, 0), (0, 0, Z), (X, 0, Z), (X, Y, Z), (0, Y, Z)]
    box = []
    for a, b in ((0, 1), (1, 2), (2, 3), (3, 0), (4, 5), (5, 6), (6, 7), (7, 4), (0, 4), (1, 5), (2, 6), (3, 7)):
        box += [c[a], c[b]]
    return np.array(floor, np.float32), np.array(box, np.float32)


def draw_3d(session):
    try:
        if bpy.context.area != session.area:
            return
        obj = session.obj
        M = mesh.grid_to_world(obj)
        dims = tuple(session.grid.shape)
        tool = bpy.context.scene.voxel_tool
    except (ReferenceError, AttributeError):
        return

    gpu.state.blend_set('ALPHA')
    gpu.state.depth_mask_set(False)
    gpu.matrix.push()
    gpu.matrix.multiply_matrix(M)

    if tool.show_grid:
        cache = getattr(session, "_grid_cache", None)
        if cache is None or cache[0] != dims:
            cache = session._grid_cache = (dims, *_grid_lines(dims))
        gpu.state.depth_test_set('LESS_EQUAL')
        _lines(cache[1], (0.6, 0.6, 0.6, 0.22), 1.0)
        _lines(cache[2], (0.75, 0.75, 0.8, 0.45), 1.0)

    p = session.preview
    if p is not None:
        quads = p["quads"]
        cache = getattr(session, "_preview_cache", None)
        if cache is None or cache[0] is not p["id"]:
            tris = quads[:, [0, 1, 2, 0, 2, 3]].reshape(-1, 3)
            edges = quads[:, [0, 1, 1, 2, 2, 3, 3, 0]].reshape(-1, 3)
            cache = session._preview_cache = (p["id"], tris, edges)
        _, tris, edges = cache
        gpu.state.depth_test_set('NONE')
        _lines(edges, (*p["line"][:3], 0.18), 1.0)
        gpu.state.depth_test_set('LESS_EQUAL')
        _tris(tris, p["fill"])
        _lines(edges, p["line"], 1.5)

    gpu.matrix.pop()
    gpu.state.depth_test_set('NONE')
    gpu.state.blend_set('NONE')


# ----------------------------------------------------------------------------
# 2D


def palette_layout(session):
    """(x, y, cell) of the palette grid's bottom-left corner, in region pixels."""
    s = bpy.context.preferences.system.ui_scale
    cell = max(8, int(round(13 * s)))
    left, _ = session.view_bounds()
    swatch = cell * 4
    return left + int(14 * s) + swatch + int(8 * s), int(14 * s), cell


def palette_hit(session, mouse):
    try:
        x0, y0, cs = palette_layout(session)
    except (ReferenceError, AttributeError):
        return None
    col = int((mouse[0] - x0) // cs)
    row = PAL_ROWS - 1 - int((mouse[1] - y0) // cs)
    if mouse[0] < x0 or mouse[1] < y0 or not (0 <= col < PAL_COLS and 0 <= row < PAL_ROWS):
        return None
    idx = col * PAL_ROWS + row
    return idx if idx > 0 else None


MODE_NAMES = {'ATTACH': "Attach", 'ERASE': "Erase", 'PAINT': "Paint", 'BONE': "Bones"}
SHAPE_NAMES = {'BRUSH': "Brush", 'BOX': "Box", 'LINE': "Line", 'FACE': "Face", 'FILL': "Fill"}

HELP = [
    "LMB draw   Ctrl invert   Alt+LMB / I pick color",
    "1 Attach   2 Erase   3 Paint   4 Bones",
    "V Brush   B Box   L Line   F Face   C Fill",
    "[ ] brush size   X Y Z mirror   G grid   W wire",
    "Box: hold Shift while dragging to raise it",
    "Face: drag to extrude / push in layers",
    "Ctrl+Z / Ctrl+Shift+Z undo / redo",
    "Tab or Esc: done   H: hide help",
]


def _text(x, y, txt, size, color):
    blf.size(0, size)
    blf.color(0, *color)
    blf.position(0, x, y, 0)
    blf.draw(0, txt)


def draw_2d(session):
    try:
        if bpy.context.area != session.area:
            return
        obj = session.obj
        tool = bpy.context.scene.voxel_tool
        x0, y0, cs = palette_layout(session)
        left, _ = session.view_bounds()
        pal = mesh.palette_srgb(obj)
    except (ReferenceError, AttributeError):
        return
    s = bpy.context.preferences.system.ui_scale
    gpu.state.blend_set('ALPHA')

    # Palette background, cells, active and hover highlights.
    pad = int(5 * s)
    gw, gh = PAL_COLS * cs, PAL_ROWS * cs
    sx = left + int(14 * s)
    swatch = cs * 4
    _rects([(sx - pad, y0 - pad, gw + (x0 - sx) + 2 * pad, gh + 2 * pad)], [(0.08, 0.08, 0.09, 0.82)])
    rects, cols = [], []
    for i in range(1, 256):
        c, r = divmod(i, PAL_ROWS)
        rects.append((x0 + c * cs, y0 + (PAL_ROWS - 1 - r) * cs, cs - 1, cs - 1))
        cols.append((*pal[i], 1.0))
    active = tool.color_index
    rects.append((sx, y0 + gh - swatch, swatch, swatch))
    cols.append((*pal[active], 1.0))
    _rects(rects, cols)

    c, r = divmod(active, PAL_ROWS)
    ax, ay = x0 + c * cs, y0 + (PAL_ROWS - 1 - r) * cs
    _outline(ax - 1, ay - 1, cs + 1, cs + 1, (0, 0, 0, 1), 3.0)
    _outline(ax - 1, ay - 1, cs + 1, cs + 1, (1, 1, 1, 1), 1.5)
    _outline(sx, y0 + gh - swatch, swatch, swatch, (1, 1, 1, 0.6), 1.0)
    if session.mouse is not None:
        h = palette_hit(session, session.mouse)
        if h is not None:
            c, r = divmod(h, PAL_ROWS)
            _outline(x0 + c * cs - 1, y0 + (PAL_ROWS - 1 - r) * cs - 1, cs + 1, cs + 1, (1, 1, 1, 0.7), 1.0)

    blf.enable(0, blf.SHADOW)
    blf.shadow(0, 3, 0.0, 0.0, 0.0, 0.9)
    blf.shadow_offset(0, 1, -1)
    _text(sx + int(2 * s), y0 + int(4 * s), f"#{active}", int(11 * s), (0.85, 0.85, 0.85, 1.0))

    # Status line.
    y = y0 + gh + pad + int(12 * s)
    op_name = MODE_NAMES[tool.mode]
    parts = [op_name, SHAPE_NAMES[tool.shape]]
    if tool.shape == 'BRUSH':
        parts.append(f"Size {tool.brush_size}{' round' if tool.brush_sphere else ''}")
    mirror = "".join(a for a, on in zip("XYZ", (tool.mirror_x, tool.mirror_y, tool.mirror_z)) if on)
    if mirror:
        parts.append(f"Mirror {mirror}")
    if tool.mode == 'BONE':
        parts.append(f"Bone: {tool.active_bone or '-'}")
    st = session.stroke
    if st is not None and st["shape"] in {'BOX', 'FACE'} and st.get("layers", 1) > 1:
        parts.append(f"{st['layers']} layers")
    X, Y, Z = session.grid.shape
    parts.append(f"{X}x{Y}x{Z}")
    _text(sx, y, "VOXLE  ·  " + "  ·  ".join(parts), int(13 * s), (1.0, 1.0, 1.0, 1.0))
    y += int(20 * s)
    if session.message:
        _text(sx, y, session.message, int(12 * s), (1.0, 0.8, 0.3, 1.0))
        y += int(18 * s)
    if tool.show_help:
        for line in reversed(HELP):
            _text(sx, y, line, int(11 * s), (0.82, 0.82, 0.82, 0.95))
            y += int(16 * s)
    else:
        _text(sx, y, "H: show shortcuts   Tab: done", int(11 * s), (0.8, 0.8, 0.8, 0.85))
    blf.disable(0, blf.SHADOW)
    gpu.state.blend_set('NONE')
