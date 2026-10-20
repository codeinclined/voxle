"""Turning voxel data into a Blender mesh, plus the palette texture/material."""

import bpy
import numpy as np
from mathutils import Matrix, Vector

from . import core, props, state

MATERIAL_TAG = "voxel_editor_material"


# ----------------------------------------------------------------------------
# Data access


def load_arrays(obj):
    """(grid, bone_map) for ``obj``; the live arrays if it is being edited."""
    s = state.session_for(obj)
    if s is not None:
        return s.grid, s.bones
    vp = obj.voxel
    return core.decode(vp.data, vp.dims), core.decode(vp.bone_data, vp.dims)


def save_arrays(obj, grid, bones):
    vp = obj.voxel
    vp.dims = grid.shape
    vp.data = core.encode(grid)
    vp.bone_data = core.encode(bones) if bones.any() else ""


def grid_offset(dims):
    """Grid -> local translation: the grid is centred on X/Y with its floor at Z=0."""
    return Vector((-dims[0] / 2.0, -dims[1] / 2.0, 0.0))


def grid_to_local(obj):
    s = obj.voxel.voxel_size
    return Matrix.Diagonal((s, s, s, 1.0)) @ Matrix.Translation(grid_offset(obj.voxel.dims))


def grid_to_world(obj):
    return obj.matrix_world @ grid_to_local(obj)


def world_matrix(obj):
    """obj's world matrix computed from its parent chain.

    Object.matrix_world is only refreshed on the next depsgraph update, so right after
    parenting (as binding does) it can still hold the old value."""
    if obj.parent is not None and obj.parent_type == 'OBJECT':
        return world_matrix(obj.parent) @ obj.matrix_parent_inverse @ obj.matrix_basis
    if obj.parent is None:
        return obj.matrix_basis.copy()
    return obj.matrix_world.copy()


def is_voxel(obj):
    return obj is not None and obj.type == 'MESH' and obj.voxel.is_voxel


# ----------------------------------------------------------------------------
# Palette, palette texture and material


def palette_srgb(obj):
    pal = obj.voxel.palette
    if len(pal) != 256:
        return core.default_palette()
    arr = np.zeros(256 * 3, np.float32)
    pal.foreach_get("color", arr)
    return arr.reshape(256, 3)


def set_palette(obj, colors):
    """Replace the whole palette with a (256, 3) sRGB array."""
    pal = obj.voxel.palette
    props.suspend_palette_updates = True
    try:
        while len(pal) < 256:
            pal.add()
        while len(pal) > 256:
            pal.remove(len(pal) - 1)
        pal.foreach_set("color", np.clip(np.asarray(colors, np.float32), 0, 1).ravel())
    finally:
        props.suspend_palette_updates = False
    palette_changed(obj)


def ensure_palette(obj):
    if len(obj.voxel.palette) != 256:
        set_palette(obj, core.default_palette())


def _image_users(img):
    return [o for o in bpy.data.objects if o.type == 'MESH' and o.voxel.is_voxel and o.voxel.palette_image == img]


def _make_material(name, img):
    mat = bpy.data.materials.new(name)
    mat[MATERIAL_TAG] = True
    if mat.node_tree is None:
        mat.use_nodes = True
    nt = mat.node_tree
    nt.nodes.clear()
    out = nt.nodes.new('ShaderNodeOutputMaterial')
    out.location = (300, 0)
    bsdf = nt.nodes.new('ShaderNodeBsdfPrincipled')
    bsdf.location = (0, 0)
    bsdf.inputs['Roughness'].default_value = 0.85
    tex = nt.nodes.new('ShaderNodeTexImage')
    tex.location = (-320, 0)
    tex.image = img
    tex.interpolation = 'Closest'
    nt.links.new(tex.outputs['Color'], bsdf.inputs['Base Color'])
    nt.links.new(bsdf.outputs['BSDF'], out.inputs['Surface'])
    nt.nodes.active = tex
    return mat


def _material_image(mat):
    if mat is None or not mat.get(MATERIAL_TAG) or mat.node_tree is None:
        return None
    for n in mat.node_tree.nodes:
        if n.type == 'TEX_IMAGE':
            return n.image
    return None


def ensure_material(obj):
    """Give the object its own palette texture and material (a duplicated object would
    otherwise share - and fight over - the original's)."""
    vp = obj.voxel
    ensure_palette(obj)
    img = vp.palette_image
    if img is None or any(o != obj for o in _image_users(img)):
        img = bpy.data.images.new(obj.name + "_palette", 256, 1, alpha=False)
        vp.palette_image = img
        update_palette_image(obj)
    me = obj.data
    if not me.materials or _material_image(me.materials[0]) != img:
        mat = _make_material(obj.name + "_voxels", img)
        if me.materials:
            me.materials[0] = mat
        else:
            me.materials.append(mat)


def update_palette_image(obj):
    img = obj.voxel.palette_image
    if img is None:
        return
    if tuple(img.size) != (256, 1):
        img.scale(256, 1)
    px = np.ones((256, 4), np.float32)
    px[:, :3] = palette_srgb(obj)
    img.pixels.foreach_set(px.ravel())
    img.update()
    try:
        img.pack()
    except RuntimeError:
        pass


def palette_changed(obj):
    """Palette edited: refresh the texture and the colour attribute (no remesh needed)."""
    if obj is None or not obj.voxel.is_voxel:
        return
    update_palette_image(obj)
    s = state.session_for(obj)
    if s is not None and s.bone_view:
        return
    me = obj.data
    attr = me.attributes.get("voxel_index")
    col = me.color_attributes.get("Col")
    if attr is None or col is None or attr.domain != 'FACE':
        return
    ci = np.zeros(len(me.polygons), np.int32)
    attr.data.foreach_get("value", ci)
    _write_colors(me, _palette_linear(obj)[np.clip(ci, 0, 255)])
    me.update()


def _palette_linear(obj):
    rgba = np.ones((256, 4), np.float32)
    rgba[:, :3] = core.srgb_to_linear(palette_srgb(obj))
    return rgba


def _write_colors(me, face_rgba):
    col = me.color_attributes.get("Col")
    if col is None:
        col = me.color_attributes.new("Col", 'FLOAT_COLOR', 'CORNER')
    col.data.foreach_set("color", np.repeat(face_rgba, 4, axis=0).astype(np.float32).ravel())
    me.color_attributes.active_color = col


# ----------------------------------------------------------------------------
# Mesh building


# Each level down the bone hierarchy is this much thinner (in voxels), so where a bent limb
# slides over its parent the child's faces sit just inside instead of z-fighting.
JOINT_INSET = 0.006


def rebuild(obj, grid=None, bones=None, weights=True, bone_view=False, target=None, optimize=False):
    """Regenerate the mesh (and, if bound to an armature, its skin weights).

    target: write into this object's mesh and vertex groups instead of obj's own (used by
    export, which builds a separate copy). optimize: merge flat runs of same-colored faces
    into bigger quads (see core.merge_coplanar).
    """
    from . import rig

    vp = obj.voxel
    if grid is None:
        grid, bones = load_arrays(obj)
    ensure_material(obj)
    out = obj if target is None else target
    if target is not None:
        target.data.materials.clear()
        for m in obj.data.materials:
            target.data.materials.append(m)

    info = None
    arm = rig.get_armature(obj)
    if arm is not None and (vp.bound or bone_view):
        info = rig.bone_info(obj, arm)
    assign = painted = None
    if info is not None:
        assign, painted = core.assign_bones(grid, bones, info.slot_map, info.heads, info.tails, info.radii)

    split = info is not None and vp.bound and vp.split_parts and len(info.names) > 0
    # Skinned: parts stay joined only at their joints, so limbs touching the body separate.
    jointed = (info is not None and vp.bound and weights and not split and not bone_view
               and len(info.names) > 0)
    joints = core.joint_table(info.parents, info.heads, info.tails) if jointed else None
    if split or jointed:
        surf = core.build_surface(assign.astype(np.int32) + 1, split, joints)
    else:
        surf = core.build_surface((grid > 0).view(np.uint8))

    W = None
    if weights and vp.bound and info is not None:
        if split:
            W = np.zeros((len(info.names), len(surf.verts)), np.float32)
            W[surf.vert_part - 1, np.arange(len(surf.verts))] = 1.0
        else:
            W = core.vertex_weights(assign, surf.verts, len(info.names), vp.weight_smooth, info.depths,
                                    vert_part=surf.vert_part, vert_welded=surf.vert_welded,
                                    axial=core.axial_distance(assign, info.heads, info.tails),
                                    joints=joints, parents=info.parents)
            # Elbows and knees bend over a few thin slices instead of inside one voxel.
            # The slices only exist on the bound mesh; voxel editing shows plain voxels.
            surf, W = core.smooth_hinges(surf, W, info, vp.bend_loops)

    offset = np.array(grid_offset(grid.shape), np.float32)
    co = surf.verts.astype(np.float32)
    if W is not None and len(surf.verts):
        # Limbs are a hair thinner per hierarchy level so bent joints don't z-fight.
        co = co + core.inset_offsets(surf, JOINT_INSET * (info.depths.astype(np.float32) @ W))
    if optimize and len(surf.verts):
        surf, W, keep = core.merge_coplanar(surf, W, grid)
        co = co[keep]
    co = (co + offset) * vp.voxel_size

    me = out.data
    _write_geometry(me, surf, co)

    F = len(surf.faces)
    fx, fy, fz = surf.face_voxel.T
    ci = grid[fx, fy, fz].astype(np.int32)

    idx_attr = me.attributes.get("voxel_index")
    if idx_attr is not None and (idx_attr.domain != 'FACE' or idx_attr.data_type != 'INT'):
        me.attributes.remove(idx_attr)
        idx_attr = None
    if idx_attr is None:
        idx_attr = me.attributes.new("voxel_index", 'INT', 'FACE')
    idx_attr.data.foreach_set("value", ci)

    uv_layer = me.uv_layers.get("UVMap") or me.uv_layers.new(name="UVMap")
    uv = np.empty((F * 4, 2), np.float32)
    uv[:, 0] = np.repeat((ci + 0.5) / 256.0, 4)
    uv[:, 1] = 0.5
    uv_layer.uv.foreach_set("vector", uv.ravel())

    if bone_view and assign is not None:
        n = len(info.names)
        bcol = np.ones((n + 1, 4), np.float32)
        bcol[:n, :3] = core.srgb_to_linear(core.bone_colors(n))
        bcol[n, :3] = 0.08
        b = assign[fx, fy, fz].astype(np.int32)
        b[b < 0] = n
        face_rgba = bcol[b]
        auto = ~painted[fx, fy, fz]
        face_rgba[auto, :3] = face_rgba[auto, :3] * 0.35 + 0.05
        _write_colors(me, face_rgba)
    else:
        _write_colors(me, _palette_linear(obj)[ci])

    if W is not None:
        _write_vertex_groups(out, info.names, W)
        if target is None:
            vp.rest_hash = info.hash
    _apply_shading(me, vp)
    me.update()


def _apply_shading(me, vp):
    """Flat voxel shading, or smooth with sharp edges past an angle (Shade Auto Smooth)."""
    if not len(me.polygons):
        return
    if vp.auto_smooth:
        me.shade_smooth()
        me.set_sharp_from_angle(angle=vp.auto_smooth_angle)
    else:
        me.shade_flat()


def _write_geometry(me, surf, co):
    me.clear_geometry()
    V, F = len(surf.verts), len(surf.faces)
    if V == 0:
        me.update()
        return
    me.vertices.add(V)
    me.vertices.foreach_set("co", co.astype(np.float32).ravel())
    me.loops.add(F * 4)
    me.loops.foreach_set("vertex_index", surf.faces.ravel())
    me.polygons.add(F)
    me.polygons.foreach_set("loop_start", np.arange(0, F * 4, 4, dtype=np.int32))
    me.update(calc_edges=True)
    if hasattr(me, "shade_flat"):
        me.shade_flat()


def _write_vertex_groups(obj, names, W):
    for b, name in enumerate(names):
        vg = obj.vertex_groups.get(name) or obj.vertex_groups.new(name=name)
        w = W[b]
        nz = np.flatnonzero(w > 0)
        if not len(nz):
            continue
        q = np.round(w[nz] * 100).astype(np.int32)
        for val in np.unique(q):
            if val > 0:
                vg.add(nz[q == val].tolist(), val / 100.0, 'REPLACE')


def refresh(obj):
    """Rebuild after a settings change, going through the edit session if one is open."""
    if obj is None or not obj.voxel.is_voxel:
        return
    s = state.session_for(obj)
    if s is not None:
        s.remesh()
    else:
        rebuild(obj)
