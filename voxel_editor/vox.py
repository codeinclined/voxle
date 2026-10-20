"""MagicaVoxel .vox import/export."""

import os
import struct

import bpy
import numpy as np
from bpy.props import FloatProperty, StringProperty
from bpy_extras.io_utils import ExportHelper, ImportHelper
from mathutils import Vector

from . import core, mesh, state


# ----------------------------------------------------------------------------
# Reading


def _read_string(buf, pos):
    n, = struct.unpack_from("<i", buf, pos)
    pos += 4
    return buf[pos:pos + n].decode("utf-8", "replace"), pos + n


def _read_dict(buf, pos):
    n, = struct.unpack_from("<i", buf, pos)
    pos += 4
    d = {}
    for _ in range(n):
        k, pos = _read_string(buf, pos)
        v, pos = _read_string(buf, pos)
        d[k] = v
    return d, pos


def decode_rotation(r):
    i0, i1 = r & 3, (r >> 2) & 3
    i2 = 3 - i0 - i1
    m = np.zeros((3, 3), np.int32)
    m[0, i0] = -1 if r & 16 else 1
    m[1, i1] = -1 if r & 32 else 1
    m[2, i2] = -1 if r & 64 else 1
    return m


def read_vox(path):
    """Returns (models, palette, instances).

    models: list of uint8 grids; palette: (256, 3) sRGB floats indexed by colour index;
    instances: list of (model index, name, translation, rotation) from the scene graph.
    """
    with open(path, "rb") as f:
        data = f.read()
    if data[:4] != b"VOX ":
        raise ValueError("Not a MagicaVoxel .vox file")
    if data[8:12] != b"MAIN":
        raise ValueError("Missing MAIN chunk")
    n, m = struct.unpack_from("<ii", data, 12)
    pos = 20 + n
    end = min(pos + m, len(data))

    models, size, rgba, nodes = [], (0, 0, 0), None, {}
    while pos + 12 <= end:
        cid = data[pos:pos + 4]
        n, m = struct.unpack_from("<ii", data, pos + 4)
        c = data[pos + 12:pos + 12 + n]
        pos += 12 + n + m
        if cid == b"SIZE":
            size = struct.unpack_from("<iii", c, 0)
        elif cid == b"XYZI":
            count, = struct.unpack_from("<i", c, 0)
            xyzi = np.frombuffer(c, np.uint8, count * 4, 4).reshape(-1, 4)
            g = np.zeros(tuple(max(1, min(s, core.MAX_DIM)) for s in size), np.uint8)
            ok = (xyzi[:, 0] < g.shape[0]) & (xyzi[:, 1] < g.shape[1]) & (xyzi[:, 2] < g.shape[2])
            v = xyzi[ok]
            g[v[:, 0], v[:, 1], v[:, 2]] = v[:, 3]
            models.append(g)
        elif cid == b"RGBA":
            rgba = np.frombuffer(c, np.uint8, 1024).reshape(256, 4)
        elif cid == b"nTRN":
            nid, = struct.unpack_from("<i", c, 0)
            attrs, p = _read_dict(c, 4)
            child, _reserved, _layer, nframes = struct.unpack_from("<iiii", c, p)
            p += 16
            frames = []
            for _ in range(nframes):
                fd, p = _read_dict(c, p)
                frames.append(fd)
            nodes[nid] = ("T", attrs, child, frames)
        elif cid == b"nGRP":
            nid, = struct.unpack_from("<i", c, 0)
            attrs, p = _read_dict(c, 4)
            nch, = struct.unpack_from("<i", c, p)
            nodes[nid] = ("G", attrs, list(struct.unpack_from(f"<{nch}i", c, p + 4)))
        elif cid == b"nSHP":
            nid, = struct.unpack_from("<i", c, 0)
            attrs, p = _read_dict(c, 4)
            nm, = struct.unpack_from("<i", c, p)
            p += 4
            mids = []
            for _ in range(nm):
                mid, = struct.unpack_from("<i", c, p)
                _, p = _read_dict(c, p + 4)
                mids.append(mid)
            nodes[nid] = ("S", attrs, mids)

    if rgba is not None:
        palette = np.zeros((256, 3), np.float32)
        palette[1:] = rgba[:255, :3] / 255.0
    else:
        palette = core.magicavoxel_palette()

    instances = []

    def walk(nid, T, R, name, depth):
        node = nodes.get(nid)
        if node is None or depth > 64:
            return
        if node[0] == "T":
            _, attrs, child, frames = node
            if attrs.get("_hidden") == "1":
                return
            f = frames[0] if frames else {}
            t = np.array([int(v) for v in f.get("_t", "0 0 0").split()][:3] or [0, 0, 0])
            r = decode_rotation(int(f.get("_r", "4")))
            walk(child, T + R @ t, R @ r, attrs.get("_name") or name, depth + 1)
        elif node[0] == "G":
            for c in node[2]:
                walk(c, T, R, name, depth + 1)
        else:
            for mid in node[2]:
                if 0 <= mid < len(models):
                    instances.append((mid, name, T, R))

    if 0 in nodes:
        walk(0, np.zeros(3, np.int64), np.eye(3, dtype=np.int64), "", 0)
    if not instances:
        x = 0
        for i, g in enumerate(models):
            instances.append((i, "", np.array([x, 0, g.shape[2] // 2]), np.eye(3, dtype=np.int64)))
            x += g.shape[0] + 2
    return models, palette, instances


def oriented(grid, R):
    """Bake a MagicaVoxel rotation into the grid. Returns (grid, pivot cell)."""
    size = np.array(grid.shape)
    piv = size // 2
    if np.array_equal(R, np.eye(3)):
        return grid, piv
    corners = np.array([[x, y, z] for x in (0, size[0] - 1) for y in (0, size[1] - 1) for z in (0, size[2] - 1)])
    rc = (R @ (corners - piv).T).T
    lo = rc.min(0)
    new_size = rc.max(0) - lo + 1
    idx = np.argwhere(grid > 0)
    vals = grid[grid > 0]
    nidx = (R @ (idx - piv).T).T - lo
    out = np.zeros(tuple(new_size), np.uint8)
    out[nidx[:, 0], nidx[:, 1], nidx[:, 2]] = vals
    return out, -lo


# ----------------------------------------------------------------------------
# Writing


def _chunk(cid, content, children=b""):
    return cid + struct.pack("<ii", len(content), len(children)) + content + children


def write_vox(path, grid, palette):
    X, Y, Z = grid.shape
    idx = np.argwhere(grid > 0)
    xyzi = np.empty((len(idx), 4), np.uint8)
    xyzi[:, :3] = idx
    xyzi[:, 3] = grid[grid > 0]
    rgba = np.zeros((256, 4), np.uint8)
    rgba[:255, :3] = np.clip(np.round(np.asarray(palette)[1:256] * 255), 0, 255)
    rgba[:255, 3] = 255
    children = (
        _chunk(b"SIZE", struct.pack("<iii", X, Y, Z))
        + _chunk(b"XYZI", struct.pack("<i", len(idx)) + xyzi.tobytes())
        + _chunk(b"RGBA", rgba.tobytes())
    )
    with open(path, "wb") as f:
        f.write(b"VOX " + struct.pack("<i", 150) + _chunk(b"MAIN", b"", children))


# ----------------------------------------------------------------------------
# Operators


def new_voxel_object(context, name, grid, palette=None, voxel_size=0.1, location=None):
    me = bpy.data.meshes.new(name)
    obj = bpy.data.objects.new(name, me)
    context.collection.objects.link(obj)
    if location is not None:
        obj.location = location
    vp = obj.voxel
    vp.is_voxel = True
    vp.voxel_size = voxel_size
    mesh.save_arrays(obj, grid, np.zeros_like(grid))
    mesh.set_palette(obj, core.default_palette() if palette is None else palette)
    mesh.rebuild(obj)
    return obj


class VOXEL_OT_import_vox(bpy.types.Operator, ImportHelper):
    bl_idname = "voxel.import_vox"
    bl_label = "Import MagicaVoxel (.vox)"
    bl_description = "Import a MagicaVoxel .vox file as editable voxel objects"
    bl_options = {'REGISTER', 'UNDO'}

    filename_ext = ".vox"
    filter_glob: StringProperty(default="*.vox", options={'HIDDEN'})
    voxel_size: FloatProperty(name="Voxel Size", default=0.1, min=0.0001, unit='LENGTH')

    def execute(self, context):
        if state.ACTIVE is not None:
            self.report({'ERROR'}, "Leave voxel editing first")
            return {'CANCELLED'}
        try:
            models, palette, instances = read_vox(self.filepath)
        except (OSError, ValueError, struct.error) as e:
            self.report({'ERROR'}, f"Could not read file: {e}")
            return {'CANCELLED'}
        base = os.path.splitext(os.path.basename(self.filepath))[0]
        cursor = context.scene.cursor.location.copy()
        s = self.voxel_size
        if context.object is not None and context.object.mode != 'OBJECT':
            bpy.ops.object.mode_set(mode='OBJECT')
        for o in context.selected_objects:
            o.select_set(False)
        created = []
        for mid, name, T, R in instances:
            grid, piv = oriented(models[mid], R)
            X, Y, _ = grid.shape
            origin = Vector(T.astype(float)) + Vector((X / 2.0, Y / 2.0, 0.0)) - Vector(piv.astype(float))
            obj = new_voxel_object(context, name or base, grid, palette, s, cursor + origin * s)
            obj.select_set(True)
            created.append(obj)
        if created:
            context.view_layer.objects.active = created[0]
        self.report({'INFO'}, f"Imported {len(created)} model(s)")
        return {'FINISHED'}


class VOXEL_OT_export_vox(bpy.types.Operator, ExportHelper):
    bl_idname = "voxel.export_vox"
    bl_label = "Export MagicaVoxel (.vox)"
    bl_description = "Export the active voxel object to a MagicaVoxel .vox file"

    filename_ext = ".vox"
    filter_glob: StringProperty(default="*.vox", options={'HIDDEN'})

    @classmethod
    def poll(cls, context):
        return mesh.is_voxel(context.active_object)

    def invoke(self, context, event):
        self.filepath = bpy.path.clean_name(context.active_object.name) + ".vox"
        return super().invoke(context, event)

    def execute(self, context):
        obj = context.active_object
        grid, _ = mesh.load_arrays(obj)
        try:
            write_vox(self.filepath, grid, mesh.palette_srgb(obj))
        except OSError as e:
            self.report({'ERROR'}, str(e))
            return {'CANCELLED'}
        self.report({'INFO'}, f"Saved {self.filepath}")
        return {'FINISHED'}


def menu_import(self, context):
    self.layout.operator(VOXEL_OT_import_vox.bl_idname, text="MagicaVoxel (.vox)")


def menu_export(self, context):
    self.layout.operator(VOXEL_OT_export_vox.bl_idname, text="MagicaVoxel (.vox)")


classes = (VOXEL_OT_import_vox, VOXEL_OT_export_vox)


def register():
    for c in classes:
        bpy.utils.register_class(c)
    bpy.types.TOPBAR_MT_file_import.append(menu_import)
    bpy.types.TOPBAR_MT_file_export.append(menu_export)


def unregister():
    bpy.types.TOPBAR_MT_file_export.remove(menu_export)
    bpy.types.TOPBAR_MT_file_import.remove(menu_import)
    for c in reversed(classes):
        bpy.utils.unregister_class(c)
