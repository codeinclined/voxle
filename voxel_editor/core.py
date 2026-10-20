"""Pure numpy voxel algorithms. Nothing in here imports bpy, so it can be tested standalone.

Conventions
-----------
* A voxel grid is a uint8 array indexed ``grid[x, y, z]``; 0 is empty, 1-255 is a palette index.
* A bone map is a parallel uint8 array; 0 means "auto" (nearest bone), otherwise a bone slot.
* Grid space: voxel ``(i, j, k)`` occupies the unit cube ``[i, i+1] x [j, j+1] x [k, k+1]``.
"""

import base64
import colorsys
import math
import zlib
from collections import namedtuple

import numpy as np

MAX_DIM = 256

FACE_NORMALS = np.array(
    [(1, 0, 0), (-1, 0, 0), (0, 1, 0), (0, -1, 0), (0, 0, 1), (0, 0, -1)], dtype=np.int32
)

# Corner offsets of each face, counter-clockwise when viewed from outside.
FACE_CORNERS = np.array(
    [
        [(1, 0, 0), (1, 1, 0), (1, 1, 1), (1, 0, 1)],
        [(0, 0, 0), (0, 0, 1), (0, 1, 1), (0, 1, 0)],
        [(0, 1, 0), (0, 1, 1), (1, 1, 1), (1, 1, 0)],
        [(0, 0, 0), (1, 0, 0), (1, 0, 1), (0, 0, 1)],
        [(0, 0, 1), (1, 0, 1), (1, 1, 1), (0, 1, 1)],
        [(0, 0, 0), (0, 1, 0), (1, 1, 0), (1, 0, 0)],
    ],
    dtype=np.int32,
)

Hit = namedtuple("Hit", "cell normal ground")
Surface = namedtuple("Surface", "verts faces face_voxel face_dir vert_part vert_welded",
                     defaults=(None,))


# ----------------------------------------------------------------------------
# Serialization


def encode(arr):
    raw = np.ascontiguousarray(arr, dtype=np.uint8).tobytes()
    return base64.b64encode(zlib.compress(raw, 6)).decode("ascii")


def decode(text, dims):
    dims = tuple(int(d) for d in dims)
    n = dims[0] * dims[1] * dims[2]
    if text:
        try:
            raw = zlib.decompress(base64.b64decode(text))
            if len(raw) == n:
                return np.frombuffer(raw, dtype=np.uint8).reshape(dims).copy()
        except Exception:
            pass
    return np.zeros(dims, dtype=np.uint8)


# ----------------------------------------------------------------------------
# Meshing


def build_surface(key, split=False, joints=None):
    """Extract the exposed faces of a voxel volume.

    ``key`` is an integer volume where 0 is empty. A face is emitted wherever the
    neighbouring key differs, so with ``split=True`` (key = part id) each part gets its
    own closed shell and vertices are not shared between parts.

    ``joints`` (bool, [part, part, axis]) instead keeps parts joined only where they meet
    across a joint: no face is emitted between two parts the table allows on that axis, and
    the vertices there are shared ("welded"). Everywhere else parts that merely touch get
    their own surfaces, so an arm resting against the body can swing away cleanly.
    """
    if joints is not None:
        split = True
    X, Y, Z = key.shape
    P = np.pad(key, 1)
    c = P[1:-1, 1:-1, 1:-1]
    solid = c != 0
    neighbours = (
        P[2:, 1:-1, 1:-1],
        P[:-2, 1:-1, 1:-1],
        P[1:-1, 2:, 1:-1],
        P[1:-1, :-2, 1:-1],
        P[1:-1, 1:-1, 2:],
        P[1:-1, 1:-1, :-2],
    )
    vox, dirs = [], []
    for d, n in enumerate(neighbours):
        idx = np.argwhere(solid & (n != c))
        vox.append(idx)
        dirs.append(np.full(len(idx), d, dtype=np.int8))
    fv = np.concatenate(vox).astype(np.int32)
    fd = np.concatenate(dirs)
    welded_keys = None
    if joints is not None and len(fv):
        n = FACE_NORMALS[fd]
        nb = fv + n
        inside = np.all((nb >= 0) & (nb < key.shape), axis=1)
        own = key[fv[:, 0], fv[:, 1], fv[:, 2]]
        other = np.zeros(len(fv), key.dtype)
        other[inside] = key[nb[inside, 0], nb[inside, 1], nb[inside, 2]]
        joined = (other > 0) & joints[own, other, fd // 2]
        jc = (fv[joined][:, None, :] + FACE_CORNERS[fd[joined]]).reshape(-1, 3).astype(np.int64)
        jk = (jc[:, 0] * (Y + 1) + jc[:, 1]) * (Z + 1) + jc[:, 2]
        # A corner is welded only for the two parts meeting at that joint, so another part's
        # surface that just happens to pass through the same point stays separate.
        npart = int(key.max()) + 1
        a = np.repeat(own[joined].astype(np.int64), 4)
        b = np.repeat(other[joined].astype(np.int64), 4)
        welded_keys = np.unique(np.concatenate([jk * npart + a, jk * npart + b]))
        fv, fd = fv[~joined], fd[~joined]
    if not len(fv):
        e3 = np.zeros((0, 3), np.int32)
        return Surface(e3, np.zeros((0, 4), np.int32), e3, np.zeros(0, np.int8), None)

    corners = fv[:, None, :] + FACE_CORNERS[fd]
    flat = corners.reshape(-1, 3).astype(np.int64)
    vkey = (flat[:, 0] * (Y + 1) + flat[:, 1]) * (Z + 1) + flat[:, 2]
    part = None
    welded = None
    if split:
        part = np.repeat(key[fv[:, 0], fv[:, 1], fv[:, 2]].astype(np.int64), 4)
        if welded_keys is not None:
            welded = np.isin(vkey * npart + part, welded_keys)
            vkey = np.where(welded, vkey, vkey + part * ((X + 1) * (Y + 1) * (Z + 1)))
        else:
            vkey = vkey + part * ((X + 1) * (Y + 1) * (Z + 1))
    _, first, inv = np.unique(vkey, return_index=True, return_inverse=True)
    verts = flat[first].astype(np.int32)
    vpart = part[first] if split else None
    vwelded = welded[first] if welded is not None else None
    return Surface(verts, inv.reshape(-1, 4).astype(np.int32), fv, fd, vpart, vwelded)


def joint_table(parents, heads, tails):
    """The ``joints`` table for build_surface: a bone joins its joint parent only across
    faces facing along the child bone (its end, not its sides). Parts are bone index + 1."""
    n = len(parents)
    table = np.zeros((n + 1, n + 1, 3), bool)
    dirs = np.asarray(tails, np.float32) - np.asarray(heads, np.float32)
    dirs /= np.maximum(np.linalg.norm(dirs, axis=1, keepdims=True), 1e-9)
    for c, p in enumerate(parents):
        if p < 0:
            continue
        along = np.abs(dirs[c]) >= 0.5
        table[c + 1, p + 1] |= along
        table[p + 1, c + 1] |= along
    return table


def slice_faces(surf, W, axis, plane, face_mask):
    """Cut the masked quads that cross ``plane`` (on ``axis``) in two, like a loop cut.

    New vertices go on the crossing edges (shared between neighbouring quads, so the mesh
    stays watertight) and get weights interpolated from the edge's ends.
    """
    V = surf.verts.astype(np.float32)
    F = surf.faces
    pos = V[F][:, :, axis]
    below = pos < plane - 1e-6
    above = pos > plane + 1e-6
    crossing = below.any(1) & above.any(1)
    cross = face_mask & crossing
    if not cross.any():
        return surf, W
    # Like a loop cut, follow the cut around: any crossing quad sharing a cut edge with a
    # cut quad is cut too, so no edge is split on one side only (that would crack).
    ci = np.flatnonzero(crossing)
    fc = F[ci]
    ce = np.sort(np.stack([fc[:, [0, 1]], fc[:, [1, 2]], fc[:, [2, 3]], fc[:, [3, 0]]], axis=1), axis=2)
    bc = below[ci]
    cb = bc != np.roll(bc, -1, axis=1)  # edges that cross the plane
    ekey = np.where(cb, ce[..., 0].astype(np.int64) * (len(V) + 1) + ce[..., 1], -1)
    sel = cross[ci].copy()
    while True:
        cut_edges = np.unique(ekey[sel])
        cut_edges = cut_edges[cut_edges >= 0]
        grow = ~sel & np.isin(ekey, cut_edges).any(axis=1)
        if not grow.any():
            break
        sel |= grow
    idx = ci[sel]
    f = F[idx]
    b = below[idx]
    # Rotate each quad so corners 0,1 are on one side and 2,3 on the other.
    ok = np.stack([(b[:, r] == b[:, (r + 1) % 4]) & (b[:, (r + 1) % 4] != b[:, (r + 2) % 4])
                   for r in range(4)], axis=1)
    rot = np.argmax(ok, axis=1)
    f = np.stack([f[np.arange(len(f)), (rot + k) % 4] for k in range(4)], axis=1)
    e1 = np.sort(f[:, [1, 2]], axis=1)
    e2 = np.sort(f[:, [3, 0]], axis=1)
    edges, inv = np.unique(np.concatenate([e1, e2]), axis=0, return_inverse=True)
    inv = inv.ravel()
    u, v = edges[:, 0], edges[:, 1]
    t = ((plane - V[u, axis]) / np.where(V[v, axis] != V[u, axis], V[v, axis] - V[u, axis], 1.0))[:, None]
    nv = V[u] + (V[v] - V[u]) * t
    base = len(V)
    a = base + inv[:len(f)]
    c = base + inv[len(f):]
    q1 = np.stack([f[:, 0], f[:, 1], a, c], axis=1)
    q2 = np.stack([c, a, f[:, 2], f[:, 3]], axis=1)
    faces = np.concatenate([np.delete(F, idx, axis=0), q1, q2])
    keep = np.ones(len(F), bool)
    keep[idx] = False
    fvox = np.concatenate([surf.face_voxel[keep], surf.face_voxel[idx], surf.face_voxel[idx]])
    fdir = np.concatenate([surf.face_dir[keep], surf.face_dir[idx], surf.face_dir[idx]])
    part = None if surf.vert_part is None else np.concatenate([surf.vert_part, surf.vert_part[u]])
    weld = None if surf.vert_welded is None else np.concatenate([surf.vert_welded, surf.vert_welded[u] & surf.vert_welded[v]])
    W = np.concatenate([W, W[:, u] * (1 - t.T) + W[:, v] * t.T], axis=1)
    return Surface(np.concatenate([V, nv]), faces, fvox, fdir, part, weld), W


HINGE_SLICES = 2


def smooth_hinges(surf, W, info, slices=HINGE_SLICES):
    """Spread each elbow/knee bend over a stretch of thin slices.

    A box limb that bends inside a single voxel folds over itself on the inside of the
    joint. Here the ``hinges`` bones get their joint region (about a limb-thickness on each
    side) loop-cut into thin slices, with weight moving from the upper to the lower limb
    across it; with Preserve Volume skinning that bends the box into a clean arc.

    The weight ramp is linear: the skin folds over itself once the rotation changes along
    the limb faster than the limb's thickness can absorb, and a linear ramp keeps that rate
    as low as possible (a 2-voxel arm bends past 150 degrees without folding).
    """
    if slices <= 0 or not len(surf.verts) or info.hinges is None or not info.hinges.any():
        return surf, W
    heads = np.asarray(info.heads, np.float32)
    tails = np.asarray(info.tails, np.float32)
    lengths = np.linalg.norm(tails - heads, axis=1)
    for c in np.flatnonzero(info.hinges):
        p = int(info.parents[c])
        if p < 0:
            continue
        d = tails[c] - heads[c]
        ax = int(np.argmax(np.abs(d)))
        sgn = 1.0 if d[ax] > 0 else -1.0
        j = float(heads[c][ax])
        h = float(np.clip(1.5 * info.radii[c], 1.0, 0.6 * min(lengths[p], lengths[c])))
        if h <= 0.25:
            continue

        def zone(V, W):
            s = (V[:, ax] - j) * sgn
            return (W[p] + W[c] > 1e-4) & (np.abs(s) <= h + 1e-4), s

        n = int(slices)
        for k in range(-n, n + 1):
            plane = j + sgn * h * k / n
            if abs(plane - round(plane)) < 0.02:
                continue  # an existing voxel edge
            z, _ = zone(surf.verts.astype(np.float32), W)
            mask = z[surf.faces].any(axis=1)
            surf, W = slice_faces(surf, W, ax, plane, mask)
        z, s = zone(surf.verts.astype(np.float32), W)
        t = np.clip((s[z] + h) / (2 * h), 0.0, 1.0)
        # Spread only the upper+lower limb share along the bend, keeping any weight from
        # other joints (a short upper arm may still carry some of the shoulder's blend).
        share = W[p, z] + W[c, z]
        W[c, z] = share * t
        W[p, z] = share * (1.0 - t)
    return surf, W


def axial_distance(assign, heads, tails):
    """Per-voxel distance (in voxels) along its own bone from that bone's head."""
    out = np.full(assign.shape, np.inf, np.float32)
    idx = np.argwhere(assign >= 0)
    if not len(idx):
        return out
    heads = np.asarray(heads, np.float32)
    dirs = np.asarray(tails, np.float32) - heads
    dirs /= np.maximum(np.linalg.norm(dirs, axis=1, keepdims=True), 1e-9)
    b = assign[idx[:, 0], idx[:, 1], idx[:, 2]]
    out[idx[:, 0], idx[:, 1], idx[:, 2]] = ((idx + 0.5 - heads[b]) * dirs[b]).sum(1)
    return out


def inset_offsets(surf, amount):
    """Per-vertex offsets (V, 3) that move every face inward by ``amount`` (per vertex).

    Each axis moves against the outward normals of the faces touching the vertex, so flat
    faces stay flat and box edges stay square.
    """
    V = len(surf.verts)
    acc = np.zeros((V, 3), np.float32)
    n = FACE_NORMALS[surf.face_dir].astype(np.float32)
    for k in range(4):
        np.add.at(acc, surf.faces[:, k], n)
    return -np.sign(acc) * np.asarray(amount, np.float32)[:, None]


def surface_of_cells(cells, inflate=0.0):
    """Outer quads of a set of cells, as float32 (F, 4, 3) corner positions."""
    if cells is None or not len(cells):
        return np.zeros((0, 4, 3), np.float32)
    lo = cells.min(0)
    hi = cells.max(0)
    vol = np.zeros(tuple(hi - lo + 1), np.uint8)
    loc = cells - lo
    vol[loc[:, 0], loc[:, 1], loc[:, 2]] = 1
    s = build_surface(vol)
    quads = s.verts[s.faces].astype(np.float32) + lo.astype(np.float32)
    if inflate:
        quads += FACE_NORMALS[s.face_dir][:, None, :].astype(np.float32) * inflate
    return quads


# ----------------------------------------------------------------------------
# Picking


def raycast(grid, origin, direction):
    """Walk the grid along a ray (grid space). Returns the first solid voxel hit or None."""
    dims = grid.shape
    o = [float(v) for v in origin]
    d = [float(v) for v in direction]
    tmin, tmax, axis_in = -math.inf, math.inf, -1
    for a in range(3):
        if abs(d[a]) < 1e-12:
            if o[a] < 0 or o[a] > dims[a]:
                return None
            continue
        t1 = -o[a] / d[a]
        t2 = (dims[a] - o[a]) / d[a]
        if t1 > t2:
            t1, t2 = t2, t1
        if t1 > tmin:
            tmin, axis_in = t1, a
        tmax = min(tmax, t2)
    if tmax < max(tmin, 0.0):
        return None
    if tmin < 0:
        t, axis_in = 0.0, -1
    else:
        t = tmin
    cell = [min(max(int(math.floor(o[a] + d[a] * t)), 0), dims[a] - 1) for a in range(3)]
    if axis_in >= 0:
        cell[axis_in] = 0 if d[axis_in] > 0 else dims[axis_in] - 1

    step, tnext, tdelta = [0, 0, 0], [math.inf] * 3, [math.inf] * 3
    for a in range(3):
        if d[a] > 1e-12:
            step[a] = 1
            tnext[a] = (cell[a] + 1 - o[a]) / d[a]
            tdelta[a] = 1.0 / d[a]
        elif d[a] < -1e-12:
            step[a] = -1
            tnext[a] = (cell[a] - o[a]) / d[a]
            tdelta[a] = -1.0 / d[a]

    normal = [0, 0, 0]
    if axis_in < 0:
        axis_in = max(range(3), key=lambda i: abs(d[i]))
    normal[axis_in] = -1 if d[axis_in] > 0 else 1

    for _ in range(dims[0] + dims[1] + dims[2] + 3):
        if grid[cell[0], cell[1], cell[2]]:
            return Hit(tuple(cell), tuple(normal), False)
        a = min(range(3), key=tnext.__getitem__)
        cell[a] += step[a]
        if not 0 <= cell[a] < dims[a]:
            return None
        tnext[a] += tdelta[a]
        normal = [0, 0, 0]
        normal[a] = -step[a]
    return None


def ground_hit(dims, origin, direction):
    """Hit the z=0 floor of the grid, as if there were a solid layer below it."""
    if abs(direction[2]) < 1e-9:
        return None
    t = -origin[2] / direction[2]
    if t <= 0:
        return None
    x = int(math.floor(origin[0] + direction[0] * t))
    y = int(math.floor(origin[1] + direction[1] * t))
    if 0 <= x < dims[0] and 0 <= y < dims[1]:
        return Hit((x, y, -1), (0, 0, 1), True)
    return None


def pick(grid, origin, direction, allow_ground=True):
    hit = raycast(grid, origin, direction)
    if hit is None and allow_ground:
        hit = ground_hit(grid.shape, origin, direction)
    return hit


def ray_plane_cell(origin, direction, axis, plane, layer):
    """Where the ray crosses the plane ``coord[axis] == plane``, as a cell in ``layer``."""
    if abs(direction[axis]) < 1e-9:
        return None
    t = (plane - origin[axis]) / direction[axis]
    if t <= 0:
        return None
    cell = [int(math.floor(origin[a] + direction[a] * t)) for a in range(3)]
    cell[axis] = layer
    return tuple(cell)


# ----------------------------------------------------------------------------
# Shapes. All return (N, 3) int32 arrays of cell coordinates (possibly out of bounds).


def brush_offsets(size, sphere):
    lo, hi = -((size - 1) // 2), size // 2
    r = np.arange(lo, hi + 1)
    g = np.stack(np.meshgrid(r, r, r, indexing="ij"), -1).reshape(-1, 3)
    if sphere and size > 2:
        centre = (lo + hi + 1) / 2.0
        dist2 = ((g + 0.5 - centre) ** 2).sum(1)
        g = g[dist2 <= (size / 2.0) ** 2 + 0.01]
    return g.astype(np.int32)


def brush_cells(center, size, sphere, normal=None):
    """Brush stamp. With ``normal`` the stamp sits on the surface instead of sinking in."""
    off = brush_offsets(size, sphere)
    c = np.array(center, np.int32)
    if normal is not None and size > 1:
        lo, hi = -((size - 1) // 2), size // 2
        for a in range(3):
            if normal[a] > 0:
                c[a] += -lo
            elif normal[a] < 0:
                c[a] -= hi
    return off + c


def box_cells(a, b):
    lo = np.minimum(a, b)
    hi = np.maximum(a, b)
    r = [np.arange(lo[i], hi[i] + 1) for i in range(3)]
    return np.stack(np.meshgrid(*r, indexing="ij"), -1).reshape(-1, 3).astype(np.int32)


def line_cells(a, b):
    a = np.array(a, np.float64)
    b = np.array(b, np.float64)
    n = int(np.abs(b - a).max())
    if n == 0:
        return a.astype(np.int32)[None]
    t = np.linspace(0.0, 1.0, n + 1)[:, None]
    pts = np.floor(a + (b - a) * t + 0.5).astype(np.int32)
    return np.unique(pts, axis=0)


def extrude(cells, normal, layers, start=0):
    """Stack copies of ``cells`` along ``normal``: offsets start .. start+layers-1."""
    n = np.array(normal, np.int32)
    return np.concatenate([cells + n * k for k in range(start, start + layers)])


def flood(mask, seed):
    """Connected component (face-adjacent) of ``mask`` containing ``seed``."""
    shape = mask.shape
    flat = mask.ravel()
    visited = np.zeros(flat.size, bool)
    if not all(0 <= seed[i] < shape[i] for i in range(len(shape))):
        return visited.reshape(shape)
    s = np.ravel_multi_index(seed, shape)
    if not flat[s]:
        return visited.reshape(shape)
    strides = [int(np.prod(shape[i + 1:])) for i in range(len(shape))]
    visited[s] = True
    frontier = np.array([s], np.int64)
    while len(frontier):
        coords = np.unravel_index(frontier, shape)
        found = []
        for a in range(len(shape)):
            for step in (-1, 1):
                ok = (coords[a] + step >= 0) & (coords[a] + step < shape[a])
                nb = frontier[ok] + step * strides[a]
                nb = nb[flat[nb] & ~visited[nb]]
                if len(nb):
                    visited[nb] = True
                    found.append(nb)
        frontier = np.unique(np.concatenate(found)) if found else np.zeros(0, np.int64)
    return visited.reshape(shape)


def face_region(grid, cell, normal):
    """All solid cells in the same layer whose face along ``normal`` is exposed and
    connected to ``cell`` - MagicaVoxel's "face" selection."""
    axis = int(np.argmax(np.abs(normal)))
    sign = normal[axis]
    others = [i for i in range(3) if i != axis]
    sl = [slice(None)] * 3
    sl[axis] = cell[axis]
    cur = grid[tuple(sl)] > 0
    nl = cell[axis] + sign
    if 0 <= nl < grid.shape[axis]:
        sl[axis] = nl
        cur = cur & ~(grid[tuple(sl)] > 0)
    region = flood(cur, (cell[others[0]], cell[others[1]]))
    pts = np.argwhere(region)
    out = np.empty((len(pts), 3), np.int32)
    out[:, others[0]] = pts[:, 0]
    out[:, others[1]] = pts[:, 1]
    out[:, axis] = cell[axis]
    return out


def fill_region(grid, cell):
    """Connected voxels with the same colour as ``cell``."""
    return np.argwhere(flood(grid == grid[tuple(cell)], tuple(cell))).astype(np.int32)


def mirror_cells(cells, dims, axes):
    out = cells
    for a in axes:
        m = out.copy()
        m[:, a] = dims[a] - 1 - m[:, a]
        out = np.concatenate([out, m])
    if axes:
        out = np.unique(out, axis=0)
    return out


def clip_cells(cells, dims):
    ok = np.all((cells >= 0) & (cells < np.array(dims)), axis=1)
    return cells[ok]


def apply_cells(grid, bones, cells, op, color=1, bone_slot=0):
    """Apply an edit. Returns True if anything changed."""
    cells = clip_cells(np.asarray(cells, np.int32).reshape(-1, 3), grid.shape)
    if not len(cells):
        return False
    x, y, z = cells.T
    cur = grid[x, y, z]
    if op == "ATTACH":
        m = cur == 0
        grid[x[m], y[m], z[m]] = color
        bones[x[m], y[m], z[m]] = 0
    elif op == "ERASE":
        m = cur != 0
        grid[x[m], y[m], z[m]] = 0
        bones[x[m], y[m], z[m]] = 0
    elif op == "PAINT":
        m = (cur != 0) & (cur != color)
        grid[x[m], y[m], z[m]] = color
    elif op == "BONE":
        m = (cur != 0) & (bones[x, y, z] != bone_slot)
        bones[x[m], y[m], z[m]] = bone_slot
    else:
        return False
    return bool(m.any())


def content_bounds(grid):
    """(lo, hi) inclusive cell bounds of the solid voxels, or None if empty."""
    nz = np.argwhere(grid > 0)
    if not len(nz):
        return None
    return nz.min(0), nz.max(0)


# ----------------------------------------------------------------------------
# Skinning


def segment_distance(p, heads, tails, behind=False):
    """Distances (N, B) from points to bone segments.

    With behind=True also returns a (N, B) mask of points lying behind each bone's head,
    i.e. on the parent's side of the joint.
    """
    ab = tails - heads
    denom = np.maximum((ab * ab).sum(1), 1e-12)
    ap = p[:, None, :] - heads[None, :, :]
    t_raw = (ap * ab[None]).sum(2) / denom[None]
    t = np.clip(t_raw, 0.0, 1.0)
    closest = heads[None] + t[..., None] * ab[None]
    d = np.sqrt(((p[:, None, :] - closest) ** 2).sum(2))
    return (d, t_raw < 0.0) if behind else d


def nearest_bones(cells, heads, tails, radii=None):
    """The bone each voxel cell (N, 3) belongs to automatically; see assign_bones."""
    heads = np.asarray(heads, np.float32)
    tails = np.asarray(tails, np.float32)
    radii = np.ones(len(heads), np.float32) if radii is None else np.maximum(np.asarray(radii, np.float32), 0.25)
    nearest = np.empty(len(cells), np.int16)
    chunk = max(1, 400000 // len(heads))
    for s in range(0, len(cells), chunk):
        p = np.asarray(cells[s:s + chunk], np.float32) + 0.5
        d, behind = segment_distance(p, heads, tails, behind=True)
        d = d / radii[None]
        # Fall back to plain distance for voxels that are behind every bone.
        masked = np.where(behind & ~behind.all(axis=1, keepdims=True), np.inf, d)
        nearest[s:s + chunk] = np.argmin(masked, axis=1)
    return nearest


def assign_bones(grid, bone_map, slot_to_bone, heads, tails, radii=None):
    """Resolve each solid voxel to a bone index.

    Painted voxels (bone_map > 0, slot mapped to a bone) keep their bone; the rest go to
    the nearest bone segment, ignoring bones whose head joint the voxel lies behind, so a
    thigh can't claim the bottom of the torso or an upper arm the side of the chest.
    Distances are divided by each bone's radius (its body part's half-thickness), so
    voxels at the side of a wide torso stay with the torso rather than a nearby arm.
    Returns (assign volume int16 with -1 = empty/none, painted volume bool).
    """
    assign = np.full(grid.shape, -1, np.int16)
    painted = np.zeros(grid.shape, bool)
    solid = grid > 0
    if not len(heads) or not solid.any():
        return assign, painted
    nearest = nearest_bones(np.argwhere(solid), heads, tails, radii)
    pb = slot_to_bone[bone_map[solid]]
    is_painted = pb >= 0
    assign[solid] = np.where(is_painted, pb, nearest)
    painted[solid] = is_painted
    return assign, painted


def box_blur(a, r):
    for ax in range(a.ndim):
        pad = [(0, 0)] * a.ndim
        pad[ax] = (r + 1, r)
        c = np.cumsum(np.pad(a, pad), axis=ax, dtype=np.float32)
        n = a.shape[ax]
        a = np.take(c, np.arange(2 * r + 1, 2 * r + 1 + n), axis=ax) - np.take(c, np.arange(n), axis=ax)
    return a


_CORNER_NEIGHBOURS = [(dx, dy, dz) for dx in (0, 1) for dy in (0, 1) for dz in (0, 1)]


def vertex_weights(assign, verts, nbones, smooth=0, depths=None, max_influences=4, threshold=0.02,
                   vert_part=None, vert_welded=None, axial=None, joints=None, parents=None):
    """Per-vertex bone weights (B, V) from a voxel bone assignment volume.

    Each vertex averages the (optionally blurred) bone ownership of the solid voxels
    around it. With depths (each bone's depth in the hierarchy), joints bend on the child's
    side: a vertex touching both a parent and a child voxel belongs to the parent, and the
    blur never spreads a child's influence back into its parent. That keeps the torso rigid
    while a leg or arm swings; smooth=0 bends within the child's first voxel.

    For a surface built with joints, vert_part/vert_welded say which part each vertex
    belongs to: unwelded vertices only take weight from their own part's voxels, welded
    ones also from the parts joined to it (``joints``, the build_surface table). With
    axial (see axial_distance) and parents (joint parent per bone), only a bone's joint
    parent blends into it, and only over its first ``smooth`` voxels from its head, so the
    blend stays at the joint instead of leaking between parts that just touch.
    """
    V = len(verts)
    W = np.zeros((nbones, V), np.float32)
    solid = assign >= 0
    if not V or not nbones or not solid.any():
        return W
    nz = np.argwhere(solid)
    lo = np.maximum(nz.min(0) - smooth, 0)
    hi = np.minimum(nz.max(0) + 1 + smooth, assign.shape)
    sub = assign[lo[0]:hi[0], lo[1]:hi[1], lo[2]:hi[2]]
    ssolid = (sub >= 0).astype(np.float32)
    c = verts.astype(np.int64) - lo + 1
    corners = [(c[:, 0] - dx, c[:, 1] - dy, c[:, 2] - dz) for dx, dy, dz in _CORNER_NEIGHBOURS]
    depths = np.zeros(nbones, np.int32) if depths is None else np.asarray(depths, np.int32)
    big = np.iinfo(np.int32).max
    D = np.where(sub >= 0, depths[np.maximum(sub, 0)], big)
    Dp = np.pad(D, 1, constant_values=big)
    cd = np.stack([Dp[k] for k in corners])
    if vert_part is not None and vert_welded is not None:
        # Unwelded vertices sit on a part's own surface: ignore other parts' voxels.
        Sp = np.pad(sub, 1, constant_values=-1)
        cb = np.stack([Sp[k] for k in corners])  # bone of each corner voxel (-1 empty)
        vp = np.asarray(vert_part)
        own = cb == (vp - 1)[None]
        if joints is not None:
            partner = joints.any(axis=2)
            joined = partner[vp[None, :], np.maximum(cb, -1) + 1] & (cb >= 0)
            own |= joined & np.asarray(vert_welded)[None]
        cd = np.where(own, cd, big)
    use = (cd == cd.min(0)).astype(np.float32)  # only the shallowest bones around a vertex count
    near_head = None
    if axial is not None and smooth > 0:
        near_head = axial[lo[0]:hi[0], lo[1]:hi[1], lo[2]:hi[2]] <= smooth + 0.5
        if parents is not None:
            par = np.asarray(parents, np.int32)
            sub_parent = np.where(sub >= 0, par[np.maximum(sub, 0)], -1)
    for b in np.unique(sub[sub >= 0]):
        oh = (sub == b).astype(np.float32)
        if smooth > 0:
            oh = box_blur(oh, smooth) * ssolid * (D >= depths[b])
            if near_head is not None:
                ok = near_head if parents is None else near_head & (sub_parent == b)
                oh *= (sub == b) | ok
        P = np.pad(oh, 1)
        acc = np.zeros(V, np.float32)
        for k, m in zip(corners, use):
            acc += P[k] * m
        W[b] = acc
    total = W.sum(0)
    W /= np.where(total > 0, total, 1.0)
    if nbones > max_influences:
        order = np.argsort(-W, axis=0)
        np.put_along_axis(W, order[max_influences:], 0.0, axis=0)
    W[W < threshold] = 0.0
    total = W.sum(0)
    W /= np.where(total > 0, total, 1.0)
    return W


def bone_colors(n):
    out = np.zeros((max(n, 1), 3), np.float32)
    for i in range(n):
        out[i] = colorsys.hsv_to_rgb((i * 0.618034) % 1.0, 0.7, 0.95)
    return out


# ----------------------------------------------------------------------------
# Palettes (sRGB float, index 0 unused)


def default_palette():
    """32 columns x 8 shades: greys, a 24-step hue wheel, then skin/earth/muted tones."""
    tones = [(0.07, 0.45), (0.06, 0.6), (0.08, 0.75), (0.1, 0.3), (0.58, 0.25), (0.33, 0.2), (0.75, 0.2)]
    pal = np.zeros((256, 3), np.float32)
    for i in range(1, 256):
        col, row = divmod(i, 8)
        if col == 0:
            pal[i] = 1.0 - (row - 1) / 6.0
            continue
        if col <= 24:
            h, s = (col - 1) / 24.0, 0.85
        else:
            h, s = tones[col - 25]
        ss = s * (0.35 + 0.65 * min(1.0, row / 3.0))
        v = 1.0 - max(0, row - 3) * 0.17
        pal[i] = colorsys.hsv_to_rgb(h, ss, v)
    return pal


def magicavoxel_palette():
    """The default palette MagicaVoxel uses for files without an RGBA chunk."""
    vals = [0xFF, 0xCC, 0x99, 0x66, 0x33, 0x00]
    cols = [(0, 0, 0)]
    cols += [(r, g, b) for r in vals for g in vals for b in vals][:-1]
    ramp = [0xEE, 0xDD, 0xBB, 0xAA, 0x88, 0x77, 0x55, 0x44, 0x22, 0x11]
    cols += [(v, 0, 0) for v in ramp]
    cols += [(0, v, 0) for v in ramp]
    cols += [(0, 0, v) for v in ramp]
    cols += [(v, v, v) for v in ramp]
    return np.array(cols, np.float32) / 255.0


def srgb_to_linear(c):
    c = np.asarray(c, np.float32)
    return np.where(c <= 0.04045, c / 12.92, ((c + 0.055) / 1.055) ** 2.4)
