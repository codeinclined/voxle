"""Object-level voxel operators: create, resize, crop, flip, rotate, palette..."""

import bpy
import numpy as np
from bpy.props import BoolProperty, EnumProperty, FloatProperty, IntProperty, IntVectorProperty
from mathutils import Matrix

from . import core, mesh, rig, state, vox

AXIS_ITEMS = [('X', "X", ""), ('Y', "Y", ""), ('Z', "Z", "")]
AXES = {'X': 0, 'Y': 1, 'Z': 2}


def modify(obj, fn):
    """Run ``fn(grid, bones) -> (grid, bones, shift)`` on the object's voxels.

    ``shift`` (old cell + shift = new cell) keeps the model in place in the world when
    the grid is resized; None means don't compensate. Goes through the edit session
    (with its undo stack) when one is open.
    """
    s = state.session_for(obj)
    if s is not None:
        s.push_undo()
    grid, bones = mesh.load_arrays(obj)
    old_off = mesh.grid_offset(grid.shape)
    grid, bones, shift = fn(grid.copy(), bones.copy())
    if shift is not None:
        delta = (old_off - mesh.grid_offset(grid.shape) - type(old_off)(shift)) * obj.voxel.voxel_size
        mw = obj.matrix_world
        obj.matrix_world = Matrix.Translation(mw.to_3x3() @ delta) @ mw
    if s is not None:
        s.set_arrays(grid, bones)
    else:
        mesh.save_arrays(obj, grid, bones)
        mesh.rebuild(obj)


def resized(arr, new_dims, shift):
    out = np.zeros(tuple(new_dims), arr.dtype)
    src, dst = [], []
    for a in range(3):
        lo = max(0, -shift[a])
        hi = min(arr.shape[a], new_dims[a] - shift[a])
        if hi <= lo:
            return out
        src.append(slice(lo, hi))
        dst.append(slice(lo + shift[a], hi + shift[a]))
    out[tuple(dst)] = arr[tuple(src)]
    return out


def mirrored_slot_map(obj):
    """Bone-map lookup that swaps .L/.R (and _L/_R, .l/.r) bone slots."""
    table = np.arange(256, dtype=np.uint8)
    pairs = [(".L", ".R"), ("_L", "_R"), (".l", ".r"), ("_l", "_r"), (".Left", ".Right")]
    for i, slot in enumerate(list(obj.voxel.bone_slots)):
        name = slot.name
        for a, b in pairs:
            other = None
            if name.endswith(a):
                other = name[:-len(a)] + b
            elif name.endswith(b):
                other = name[:-len(b)] + a
            if other:
                table[i + 1] = rig.bone_slot(obj, other)
                break
    return table


class VoxelOp:
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return mesh.is_voxel(context.active_object)


class VOXEL_OT_new(bpy.types.Operator):
    bl_idname = "voxel.new"
    bl_label = "Voxel Object"
    bl_description = "Create a new voxel object and start editing it"
    bl_options = {'REGISTER', 'UNDO'}

    dims: IntVectorProperty(name="Size", size=3, default=(32, 32, 32), min=1, max=256)
    voxel_size: FloatProperty(name="Voxel Size", default=0.1, min=0.0001, unit='LENGTH')
    start: EnumProperty(name="Start With", default='EMPTY', items=[
        ('EMPTY', "Empty", "An empty grid"),
        ('BLOCK', "Block", "A small block to build from"),
        ('FLOOR', "Floor", "A one-voxel-thick floor covering the grid"),
    ])
    edit: BoolProperty(name="Start Editing", default=True)

    @classmethod
    def poll(cls, context):
        return state.ACTIVE is None

    def execute(self, context):
        if context.object is not None and context.object.mode != 'OBJECT':
            bpy.ops.object.mode_set(mode='OBJECT')
        grid = np.zeros(tuple(self.dims), np.uint8)
        color = context.scene.voxel_tool.color_index
        X, Y, Z = grid.shape
        if self.start == 'BLOCK':
            n = max(1, min(X, Y, Z) // 4)
            x0, y0 = (X - n) // 2, (Y - n) // 2
            grid[x0:x0 + n, y0:y0 + n, 0:n] = color
        elif self.start == 'FLOOR':
            grid[:, :, 0] = color
        obj = vox.new_voxel_object(context, "Voxels", grid, None, self.voxel_size,
                                   context.scene.cursor.location.copy())
        for o in context.selected_objects:
            o.select_set(False)
        obj.select_set(True)
        context.view_layer.objects.active = obj
        if self.edit and context.area is not None and context.area.type == 'VIEW_3D':
            bpy.ops.voxel.edit('INVOKE_DEFAULT')
        return {'FINISHED'}


class VOXEL_OT_resize(VoxelOp, bpy.types.Operator):
    bl_idname = "voxel.resize"
    bl_label = "Resize Grid"
    bl_description = "Change the grid dimensions, keeping the model centred"

    dims: IntVectorProperty(name="Size", size=3, default=(32, 32, 32), min=1, max=256)

    def invoke(self, context, event):
        self.dims = tuple(context.active_object.voxel.dims)
        return context.window_manager.invoke_props_dialog(self)

    def execute(self, context):
        new = tuple(self.dims)

        def fn(grid, bones):
            old = grid.shape
            shift = ((new[0] - old[0]) // 2, (new[1] - old[1]) // 2, 0)
            return resized(grid, new, shift), resized(bones, new, shift), shift

        modify(context.active_object, fn)
        return {'FINISHED'}


class VOXEL_OT_crop(VoxelOp, bpy.types.Operator):
    bl_idname = "voxel.crop"
    bl_label = "Fit Grid to Model"
    bl_description = "Shrink the grid to the model's bounds (plus an optional margin)"

    margin: IntProperty(name="Margin", default=0, min=0, max=64)

    def execute(self, context):
        obj = context.active_object
        grid, _ = mesh.load_arrays(obj)
        b = core.content_bounds(grid)
        if b is None:
            self.report({'WARNING'}, "Model is empty")
            return {'CANCELLED'}
        lo, hi = b
        m = self.margin
        lo = lo - np.array([m, m, 0])
        new = tuple(int(v) for v in np.minimum(hi - lo + 1 + np.array([m, m, m]), core.MAX_DIM))
        shift = tuple(int(-v) for v in lo)

        def fn(grid, bones):
            return resized(grid, new, shift), resized(bones, new, shift), shift

        modify(obj, fn)
        return {'FINISHED'}


class VOXEL_OT_flip(VoxelOp, bpy.types.Operator):
    bl_idname = "voxel.flip"
    bl_label = "Flip"
    bl_description = "Mirror the whole model along an axis"

    axis: EnumProperty(items=AXIS_ITEMS)

    def execute(self, context):
        a = AXES[self.axis]
        obj = context.active_object
        table = mirrored_slot_map(obj) if a == 0 else None

        def fn(grid, bones):
            bones = np.flip(bones, a).copy()
            if table is not None:
                bones = table[bones]
            return np.flip(grid, a).copy(), bones, None

        modify(obj, fn)
        return {'FINISHED'}


class VOXEL_OT_rotate(VoxelOp, bpy.types.Operator):
    bl_idname = "voxel.rotate"
    bl_label = "Rotate 90°"
    bl_description = "Rotate the whole model 90 degrees around an axis"

    axis: EnumProperty(items=AXIS_ITEMS, default='Z')

    def execute(self, context):
        axes = {'X': (1, 2), 'Y': (2, 0), 'Z': (0, 1)}[self.axis]

        def fn(grid, bones):
            return (np.ascontiguousarray(np.rot90(grid, 1, axes)),
                    np.ascontiguousarray(np.rot90(bones, 1, axes)), None)

        modify(context.active_object, fn)
        return {'FINISHED'}


class VOXEL_OT_shift(VoxelOp, bpy.types.Operator):
    bl_idname = "voxel.shift"
    bl_label = "Shift"
    bl_description = "Move the model inside its grid (wraps around)"

    axis: EnumProperty(items=AXIS_ITEMS)
    amount: IntProperty(default=1)

    def execute(self, context):
        a = AXES[self.axis]

        def fn(grid, bones):
            return np.roll(grid, self.amount, a), np.roll(bones, self.amount, a), None

        modify(context.active_object, fn)
        return {'FINISHED'}


class VOXEL_OT_symmetrize(VoxelOp, bpy.types.Operator):
    bl_idname = "voxel.symmetrize"
    bl_label = "Symmetrize"
    bl_description = "Copy one half of the model onto the other half"

    axis: EnumProperty(items=AXIS_ITEMS)
    positive_to_negative: BoolProperty(name="+ to -", default=True)

    def execute(self, context):
        a = AXES[self.axis]
        obj = context.active_object
        table = mirrored_slot_map(obj) if a == 0 else np.arange(256, dtype=np.uint8)

        def fn(grid, bones):
            n = grid.shape[a]
            half = np.arange(n) < n // 2 if self.positive_to_negative else np.arange(n) >= (n + 1) // 2
            sel = [slice(None)] * 3
            sel[a] = half
            sel = tuple(sel)
            grid[sel] = np.flip(grid, a)[sel]
            bones[sel] = table[np.flip(bones, a)[sel]]
            return grid, bones, None

        modify(obj, fn)
        return {'FINISHED'}


class VOXEL_OT_clear(VoxelOp, bpy.types.Operator):
    bl_idname = "voxel.clear"
    bl_label = "Clear All"
    bl_description = "Remove every voxel"

    def invoke(self, context, event):
        return context.window_manager.invoke_confirm(self, event)

    def execute(self, context):
        modify(context.active_object, lambda g, b: (np.zeros_like(g), np.zeros_like(b), None))
        return {'FINISHED'}


class VOXEL_OT_palette_preset(VoxelOp, bpy.types.Operator):
    bl_idname = "voxel.palette_preset"
    bl_label = "Load Palette Preset"
    bl_description = "Replace the palette (voxels keep their palette indices)"

    preset: EnumProperty(items=[
        ('DEFAULT', "Shades", "Hue columns, each with 8 shades, plus greys and skin tones"),
        ('MAGICAVOXEL', "MagicaVoxel Classic", "MagicaVoxel's original default palette"),
    ])

    def execute(self, context):
        pal = core.default_palette() if self.preset == 'DEFAULT' else core.magicavoxel_palette()
        mesh.set_palette(context.active_object, pal)
        return {'FINISHED'}


class VOXEL_OT_replace_color(VoxelOp, bpy.types.Operator):
    bl_idname = "voxel.replace_color"
    bl_label = "Replace Color"
    bl_description = "Recolor every voxel of one palette index with another"

    source: IntProperty(name="From", default=1, min=1, max=255)
    target: IntProperty(name="To", default=1, min=1, max=255)

    def invoke(self, context, event):
        self.target = context.scene.voxel_tool.color_index
        return context.window_manager.invoke_props_dialog(self)

    def execute(self, context):
        src, dst = self.source, self.target

        def fn(grid, bones):
            grid[grid == src] = dst
            return grid, bones, None

        modify(context.active_object, fn)
        return {'FINISHED'}


class VOXEL_OT_set_color(bpy.types.Operator):
    bl_idname = "voxel.set_color"
    bl_label = "Select Color"
    bl_description = "Make this the active palette color"
    bl_options = {'INTERNAL'}

    index: IntProperty(min=1, max=255)

    def execute(self, context):
        context.scene.voxel_tool.color_index = self.index
        return {'FINISHED'}


def menu_add(self, context):
    self.layout.operator(VOXEL_OT_new.bl_idname, text="Voxel Object", icon='MESH_CUBE')


classes = (
    VOXEL_OT_new,
    VOXEL_OT_resize,
    VOXEL_OT_crop,
    VOXEL_OT_flip,
    VOXEL_OT_rotate,
    VOXEL_OT_shift,
    VOXEL_OT_symmetrize,
    VOXEL_OT_clear,
    VOXEL_OT_palette_preset,
    VOXEL_OT_replace_color,
    VOXEL_OT_set_color,
)


def register():
    for c in classes:
        bpy.utils.register_class(c)
    bpy.types.VIEW3D_MT_add.append(menu_add)


def unregister():
    bpy.types.VIEW3D_MT_add.remove(menu_add)
    for c in reversed(classes):
        bpy.utils.unregister_class(c)
