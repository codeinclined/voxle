"""The interactive voxel editing mode (a modal operator living in the 3D viewport)."""

import traceback
import zlib

import bpy
import numpy as np
from bpy.props import BoolProperty
from bpy_extras import view3d_utils
from mathutils import Vector

from . import core, draw, mesh, rig, state

MODE_KEYS = {'ONE': 'ATTACH', 'TWO': 'ERASE', 'THREE': 'PAINT', 'FOUR': 'BONE'}
SHAPE_KEYS = {'V': 'BRUSH', 'B': 'BOX', 'L': 'LINE', 'F': 'FACE', 'C': 'FILL'}
MIRROR_KEYS = {'X': 'mirror_x', 'Y': 'mirror_y', 'Z': 'mirror_z'}
MOVE_EVENTS = {'MOUSEMOVE', 'INBETWEEN_MOUSEMOVE'}
MAX_UNDO = 100
PREVIEW_LIMIT = 150000


class VOXEL_OT_edit(bpy.types.Operator):
    bl_idname = "voxel.edit"
    bl_label = "Edit Voxels"
    bl_description = "Build, erase and paint voxels directly in the viewport"
    bl_options = {'REGISTER', 'UNDO'}

    reset_tool: BoolProperty(default=True, options={'HIDDEN', 'SKIP_SAVE'})

    @classmethod
    def poll(cls, context):
        return (state.ACTIVE is None and mesh.is_voxel(context.active_object)
                and context.area is not None and context.area.type == 'VIEW_3D')

    # ------------------------------------------------------------------
    # Lifecycle

    def invoke(self, context, event):
        obj = context.active_object
        if obj.mode != 'OBJECT':
            bpy.ops.object.mode_set(mode='OBJECT')
        self.obj = obj
        self.obj_name = obj.name
        self.area = context.area
        self.space = context.space_data
        self.region = next(r for r in context.area.regions if r.type == 'WINDOW')
        self.grid, self.bones = mesh.load_arrays(obj)
        self.version = 0
        self.undo_stack, self.redo_stack = [], []
        self.stroke = None
        self.hover = None
        self.hover_key = None
        self.preview = None
        self.mouse = None
        self.ctrl = False
        self.message = ""
        self.request_exit = False
        self.needs_reload = False
        tool = context.scene.voxel_tool
        if self.reset_tool:
            tool.mode, tool.shape = 'ATTACH', 'BOX'
        self.bone_view = tool.mode == 'BONE'
        self.tool_sig = None

        mesh.ensure_material(obj)
        sh = self.space.shading
        self.saved_view = (sh.type, sh.color_type, self.space.overlay.show_outline_selected)
        if sh.type in {'SOLID', 'WIREFRAME'}:
            sh.type = 'SOLID'
            sh.color_type = 'VERTEX'
        self.space.overlay.show_outline_selected = False
        self.saved_wire = (obj.show_wire, obj.show_all_edges)
        self.apply_wire(tool.show_wire)
        self.muted = []
        for m in obj.modifiers:
            if m.type == 'ARMATURE' and m.show_viewport:
                m.show_viewport = False
                self.muted.append(m.name)

        self.handles = (
            bpy.types.SpaceView3D.draw_handler_add(draw.draw_3d, (self,), 'WINDOW', 'POST_VIEW'),
            bpy.types.SpaceView3D.draw_handler_add(draw.draw_2d, (self,), 'WINDOW', 'POST_PIXEL'),
        )
        wm = context.window_manager
        self.timer = wm.event_timer_add(0.1, window=context.window)
        wm.modal_handler_add(self)
        state.ACTIVE = self
        self.remesh()
        return {'RUNNING_MODAL'}

    def finish(self, context):
        if self.stroke is not None:
            self.end_stroke()
        try:
            context.window_manager.event_timer_remove(self.timer)
        except Exception:
            pass
        remove_draw_handlers(self)
        state.ACTIVE = None
        obj = self.valid_obj()
        if obj is not None:
            self.commit()
            obj.show_wire, obj.show_all_edges = self.saved_wire
            for name in self.muted:
                m = obj.modifiers.get(name)
                if m is not None:
                    m.show_viewport = True
            mesh.rebuild(obj)
        try:
            sh = self.space.shading
            if sh.type == 'SOLID' and sh.color_type == 'VERTEX':
                sh.type, sh.color_type = self.saved_view[0], self.saved_view[1]
            self.space.overlay.show_outline_selected = self.saved_view[2]
            self.area.tag_redraw()
        except (ReferenceError, AttributeError, TypeError):
            pass
        return {'FINISHED'}

    def apply_wire(self, on):
        obj = self.valid_obj()
        if obj is None:
            return
        obj.show_wire = on or self.saved_wire[0]
        obj.show_all_edges = on or self.saved_wire[1]
        self.tag_redraw()

    def valid_obj(self):
        try:
            self.obj.name
        except (ReferenceError, AttributeError):
            self.obj = bpy.data.objects.get(self.obj_name)
            self.needs_reload = True
        if self.obj is None or not self.obj.voxel.is_voxel:
            return None
        return self.obj

    # ------------------------------------------------------------------
    # Data

    def commit(self):
        mesh.save_arrays(self.obj, self.grid, self.bones)

    def remesh(self):
        mesh.rebuild(self.obj, self.grid, self.bones, weights=False, bone_view=self.bone_view)
        self.tag_redraw()

    def reload(self):
        """Pick up the voxel data again after a Blender undo/redo."""
        self.needs_reload = False
        vp = self.obj.voxel
        self.grid = core.decode(vp.data, vp.dims)
        self.bones = core.decode(vp.bone_data, vp.dims)
        self.undo_stack.clear()
        self.redo_stack.clear()
        self.changed()

    def changed(self):
        self.version += 1
        self.hover_key = None
        self.remesh()

    def set_arrays(self, grid, bones):
        self.grid, self.bones = grid, bones
        self.commit()
        self.changed()
        self.set_preview(None)

    def snapshot(self):
        return (self.grid.shape, zlib.compress(self.grid.tobytes(), 1),
                zlib.compress(self.bones.tobytes(), 1), self.obj.matrix_world.copy())

    def restore(self, snap):
        shape, g, b, mw = snap
        self.grid = np.frombuffer(zlib.decompress(g), np.uint8).reshape(shape).copy()
        self.bones = np.frombuffer(zlib.decompress(b), np.uint8).reshape(shape).copy()
        self.obj.matrix_world = mw
        self.commit()
        self.changed()

    def push_undo(self, snap=None):
        self.undo_stack.append(snap or self.snapshot())
        del self.undo_stack[:-MAX_UNDO]
        self.redo_stack.clear()

    def undo(self):
        if not self.undo_stack:
            self.message = "Nothing to undo"
            return
        self.redo_stack.append(self.snapshot())
        self.restore(self.undo_stack.pop())
        self.message = ""

    def redo(self):
        if not self.redo_stack:
            self.message = "Nothing to redo"
            return
        self.undo_stack.append(self.snapshot())
        self.restore(self.redo_stack.pop())
        self.message = ""

    # ------------------------------------------------------------------
    # Event loop

    def modal(self, context, event):
        try:
            return self._modal(context, event)
        except Exception:
            traceback.print_exc()
            self.report({'ERROR'}, "Voxel editing stopped after an error (see the system console)")
            return self.finish(context)

    def _modal(self, context, event):
        if self.valid_obj() is None or self.request_exit:
            return self.finish(context)
        try:
            self.area.type
        except ReferenceError:
            return self.finish(context)
        if self.needs_reload:
            self.reload()

        tool = context.scene.voxel_tool
        if (tool.mode == 'BONE') != self.bone_view:
            self.bone_view = tool.mode == 'BONE'
            self.remesh()

        if event.type == 'TIMER':
            sig = (tool.mode, tool.shape, tool.brush_size, tool.brush_sphere, tool.mirror_x,
                   tool.mirror_y, tool.mirror_z, tool.color_index, tool.active_bone,
                   tuple(self.obj.voxel.dims))
            if sig != self.tool_sig:
                self.tool_sig = sig
                self.hover_key = None
                if self.mouse is not None and self.stroke is None:
                    self.update_hover(context)
            return {'PASS_THROUGH'}

        self.ctrl = event.ctrl or event.oskey
        if self.stroke is not None:
            return self.handle_stroke(context, event)

        if not self.mouse_in_view(context, event):
            if self.hover is not None or self.preview is not None:
                self.hover = None
                self.hover_key = None
                self.set_preview(None)
            self.mouse = None
            return {'PASS_THROUGH'}
        self.mouse = (event.mouse_x - self.region.x, event.mouse_y - self.region.y)

        if event.type in MOVE_EVENTS:
            self.update_hover(context)
            return {'PASS_THROUGH'}

        if event.type == 'LEFTMOUSE' and event.value == 'PRESS':
            idx = draw.palette_hit(self, self.mouse)
            if idx is not None:
                tool.color_index = idx
                self.tag_redraw()
                return {'RUNNING_MODAL'}
            if event.alt:
                self.pick(context)
                return {'RUNNING_MODAL'}
            self.begin_stroke(context, event)
            return {'RUNNING_MODAL'}

        if event.value == 'PRESS':
            result = self.handle_key(context, event)
            if result == 'EXIT':
                return self.finish(context)
            if result:
                self.tag_redraw()
                return {'RUNNING_MODAL'}
        elif event.value == 'RELEASE' and self.is_our_key(event):
            return {'RUNNING_MODAL'}

        if event.type in {'LEFT_CTRL', 'RIGHT_CTRL', 'OSKEY'}:
            self.hover_key = None
            self.update_hover(context)
        return {'PASS_THROUGH'}

    def is_our_key(self, event):
        t = event.type
        return (t in MODE_KEYS or t in SHAPE_KEYS or t in MIRROR_KEYS
                or t in {'LEFT_BRACKET', 'RIGHT_BRACKET', 'H', 'I', 'G', 'W', 'TAB'})

    def handle_key(self, context, event):
        tool = context.scene.voxel_tool
        t = event.type
        ctrl = event.ctrl or event.oskey
        if t == 'ESC' or (t == 'TAB' and not (ctrl or event.alt or event.shift)):
            return 'EXIT'
        if ctrl and t == 'Z':
            self.redo() if event.shift else self.undo()
            return True
        if ctrl and t == 'Y':
            self.redo()
            return True
        if ctrl or event.alt or event.shift:
            return False
        if t in MODE_KEYS:
            tool.mode = MODE_KEYS[t]
        elif t in SHAPE_KEYS:
            tool.shape = SHAPE_KEYS[t]
        elif t in MIRROR_KEYS:
            setattr(tool, MIRROR_KEYS[t], not getattr(tool, MIRROR_KEYS[t]))
        elif t == 'LEFT_BRACKET':
            tool.brush_size = max(1, tool.brush_size - 1)
        elif t == 'RIGHT_BRACKET':
            tool.brush_size = tool.brush_size + 1
        elif t == 'H':
            tool.show_help = not tool.show_help
        elif t == 'G':
            tool.show_grid = not tool.show_grid
        elif t == 'W':
            tool.show_wire = not tool.show_wire
        elif t == 'I':
            self.pick(context)
        else:
            return False
        self.hover_key = None
        self.update_hover(context)
        return True

    # ------------------------------------------------------------------
    # Viewport helpers

    def tag_redraw(self):
        try:
            self.area.tag_redraw()
        except ReferenceError:
            pass

    def view_bounds(self):
        """Left/right edges (region space) not covered by overlapping toolbar/sidebar."""
        r = self.region
        left, right = 0, r.width
        for o in self.area.regions:
            if o.type not in {'TOOLS', 'UI'} or o.width <= 1:
                continue
            if o.x <= r.x + 2:
                left = max(left, o.x + o.width - r.x)
            elif o.x < r.x + r.width:
                right = min(right, o.x - r.x)
        return left, right

    def mouse_in_view(self, context, event):
        x, y = event.mouse_x, event.mouse_y
        r = self.region
        if not (r.x <= x < r.x + r.width and r.y <= y < r.y + r.height):
            return False
        for o in self.area.regions:
            if o.type == 'WINDOW' or o.width <= 1 or o.height <= 1:
                continue
            if o.x <= x < o.x + o.width and o.y <= y < o.y + o.height:
                return False
        if self.space.show_gizmo and self.space.show_gizmo_navigate:
            s = context.preferences.system.ui_scale
            _, right = self.view_bounds()
            if x - r.x > right - 100 * s and y - r.y > r.height - 260 * s:
                return False
        return True

    def ray(self, mouse):
        rv3d = self.space.region_3d
        o = view3d_utils.region_2d_to_origin_3d(self.region, rv3d, mouse)
        d = view3d_utils.region_2d_to_vector_3d(self.region, rv3d, mouse)
        inv = mesh.grid_to_world(self.obj).inverted_safe()
        return inv @ o, inv.to_3x3() @ d

    def hit_at(self, grid, op, mouse):
        o, d = self.ray(mouse)
        return core.pick(grid, o, d, allow_ground=(op == 'ATTACH'))

    @staticmethod
    def target_cell(op, hit):
        if op == 'ATTACH':
            return tuple(hit.cell[i] + hit.normal[i] for i in range(3))
        return hit.cell

    def effective_op(self, tool, shape=None):
        op = tool.mode
        if self.ctrl:
            op = {'ATTACH': 'ERASE', 'ERASE': 'ATTACH'}.get(op, op)
        if (shape or tool.shape) == 'FILL' and op == 'ATTACH':
            op = 'PAINT'
        return op

    def mirror_axes(self, tool):
        return [a for a, on in enumerate((tool.mirror_x, tool.mirror_y, tool.mirror_z)) if on]

    def finalize_cells(self, tool, cells):
        cells = np.asarray(cells, np.int32).reshape(-1, 3)
        cells = core.mirror_cells(cells, self.grid.shape, self.mirror_axes(tool))
        return core.clip_cells(cells, self.grid.shape)

    def layers_from_drag(self, hit, m0, m1):
        """Layers to extrude, from mouse travel along the face normal's screen direction."""
        G = mesh.grid_to_world(self.obj)
        c = Vector(hit.cell) + Vector((0.5, 0.5, 0.5))
        n = Vector(hit.normal)
        rv3d = self.space.region_3d
        s0 = view3d_utils.location_3d_to_region_2d(self.region, rv3d, G @ (c + n * 0.5))
        s1 = view3d_utils.location_3d_to_region_2d(self.region, rv3d, G @ (c + n * 1.5))
        sd = (s1 - s0) if (s0 is not None and s1 is not None) else Vector((0.0, 0.0))
        if sd.length < 6:
            sd = Vector((0.0, 20.0))
        delta = Vector(m1) - Vector(m0)
        return max(1, 1 + int(round(abs(delta.dot(sd)) / sd.length_squared)))

    # ------------------------------------------------------------------
    # Bone painting

    def bone_ready(self, tool):
        arm = rig.get_armature(self.obj)
        if arm is None:
            self.message = "Bones mode needs an armature - add one in the Rigging panel"
            return False
        if tool.active_bone not in arm.data.bones or not arm.data.bones[tool.active_bone].use_deform:
            deform = [b.name for b in arm.data.bones if b.use_deform]
            if not deform:
                self.message = "The armature has no deforming bones"
                return False
            tool.active_bone = deform[0]
        return True

    def bone_color(self, tool):
        arm = rig.get_armature(self.obj)
        if arm is None:
            return (0.6, 0.6, 0.6)
        names = [b.name for b in arm.data.bones if b.use_deform]
        if tool.active_bone in names:
            return tuple(core.bone_colors(len(names))[names.index(tool.active_bone)])
        return (0.6, 0.6, 0.6)

    # ------------------------------------------------------------------
    # Hover preview

    def preview_color(self, tool, op):
        if op == 'ERASE':
            return (1.0, 0.25, 0.2, 0.35), (1.0, 0.35, 0.3, 0.9)
        if op == 'BONE':
            c = self.bone_color(tool) if not self.ctrl else (0.3, 0.3, 0.3)
            return (*c, 0.6), (1.0, 1.0, 1.0, 0.9)
        c = tuple(mesh.palette_srgb(self.obj)[tool.color_index])
        return (*c, 0.55 if op == 'ATTACH' else 0.8), (1.0, 1.0, 1.0, 0.85)

    def set_preview(self, cells, colors=None):
        if cells is None or not len(cells):
            if self.preview is not None:
                self.preview = None
                self.tag_redraw()
            return
        if len(cells) > PREVIEW_LIMIT:
            lo, hi = cells.min(0), cells.max(0)
            quads = (lo + core.FACE_CORNERS * (hi - lo + 1)).astype(np.float32)
        else:
            quads = core.surface_of_cells(cells, inflate=0.015)
        fill, line = colors
        self.preview = {"quads": quads, "fill": fill, "line": line, "count": len(cells),
                        "id": object()}
        self.tag_redraw()

    def hover_cells(self, tool, op, hit):
        shape = tool.shape
        target = self.target_cell(op, hit)
        if shape == 'BRUSH':
            return core.brush_cells(target, tool.brush_size, tool.brush_sphere,
                                    hit.normal if op == 'ATTACH' else None)
        if shape == 'FACE' and not hit.ground:
            region = core.face_region(self.grid, hit.cell, hit.normal)
            return region + np.array(hit.normal, np.int32) if op == 'ATTACH' else region
        if shape == 'FILL' and not hit.ground:
            return core.fill_region(self.grid, hit.cell)
        return np.array([target], np.int32)

    def update_hover(self, context):
        if self.mouse is None:
            return
        tool = context.scene.voxel_tool
        if draw.palette_hit(self, self.mouse) is not None:
            self.hover = None
            self.set_preview(None)
            return
        op = self.effective_op(tool)
        if op == 'BONE' and rig.get_armature(self.obj) is not None:
            self.bone_ready(tool)
        hit = self.hit_at(self.grid, op, self.mouse)
        key = (hit, op, self.version, self.ctrl)
        if key == self.hover_key:
            return
        self.hover_key = key
        self.hover = hit
        if hit is None or (op == 'BONE' and rig.get_armature(self.obj) is None):
            self.set_preview(None)
            return
        cells = self.finalize_cells(tool, self.hover_cells(tool, op, hit))
        self.set_preview(cells, self.preview_color(tool, op))

    # ------------------------------------------------------------------
    # Strokes

    def begin_stroke(self, context, event):
        tool = context.scene.voxel_tool
        op = self.effective_op(tool)
        self.message = ""
        if op == 'BONE' and not self.bone_ready(tool):
            self.tag_redraw()
            return
        snap = self.grid.copy()
        hit = self.hit_at(snap, op, self.mouse)
        if hit is None:
            return
        slot = 0
        if op == 'BONE' and not self.ctrl:
            slot = rig.bone_slot(self.obj, tool.active_bone)
        st = {
            "op": op, "shape": tool.shape, "snap": snap, "hit": hit, "mouse0": self.mouse,
            "before": self.snapshot(), "changed": False, "last": None, "cells": None,
            "slot": slot, "color": tool.color_index, "colors": self.preview_color(tool, op),
            "b": self.target_cell(op, hit), "layers": 1, "shift_mouse": None, "shift_layers": 1,
        }
        self.stroke = st
        if st["shape"] == 'FACE':
            if hit.ground:
                st["region"], st["start"] = np.array([self.target_cell(op, hit)], np.int32), 0
            else:
                st["region"] = core.face_region(snap, hit.cell, hit.normal)
                st["start"] = 1 if op == 'ATTACH' else 0
        if st["shape"] == 'BRUSH':
            self.stamp(tool, hit)
        elif st["shape"] == 'FILL':
            if not hit.ground:
                st["cells"] = self.finalize_cells(tool, core.fill_region(snap, hit.cell))
            self.end_stroke()
        else:
            self.update_drag(tool, event)

    def apply(self, cells):
        st = self.stroke
        if core.apply_cells(self.grid, self.bones, cells, st["op"], st["color"], st["slot"]):
            st["changed"] = True
            self.version += 1
            return True
        return False

    def stamp(self, tool, hit):
        st = self.stroke
        op = st["op"]
        target = self.target_cell(op, hit)
        if st["last"] == target:
            return
        pts = [target]
        if st["last"] is not None and max(abs(target[i] - st["last"][i]) for i in range(3)) <= 6:
            pts = core.line_cells(st["last"], target)
        normal = hit.normal if op == 'ATTACH' else None
        cells = np.concatenate([core.brush_cells(p, tool.brush_size, tool.brush_sphere, normal) for p in pts])
        st["last"] = target
        if self.apply(self.finalize_cells(tool, cells)):
            self.remesh()

    def update_drag(self, tool, event):
        st = self.stroke
        op, hit0, shape = st["op"], st["hit"], st["shape"]
        a = self.target_cell(op, hit0)
        normal = np.array(hit0.normal, np.int32)
        if shape == 'BOX':
            axis = int(np.argmax(np.abs(normal)))
            if event.shift:
                if st["shift_mouse"] is None:
                    st["shift_mouse"], st["shift_layers"] = self.mouse, st["layers"]
                st["layers"] = st["shift_layers"] - 1 + self.layers_from_drag(hit0, st["shift_mouse"], self.mouse)
            else:
                st["shift_mouse"] = None
                o, d = self.ray(self.mouse)
                # The face the user clicked on, so the box tracks the cursor exactly.
                plane = hit0.cell[axis] + (1 if normal[axis] > 0 else 0)
                b = core.ray_plane_cell(o, d, axis, plane, a[axis])
                if b is not None:
                    st["b"] = tuple(int(np.clip(b[i], 0, self.grid.shape[i] - 1)) if i != axis else a[axis]
                                    for i in range(3))
            rect = core.box_cells(np.array(a), np.array(st["b"]))
            direction = normal if op == 'ATTACH' else -normal
            cells = core.extrude(rect, direction, st["layers"])
        elif shape == 'LINE':
            hit = self.hit_at(st["snap"], op, self.mouse)
            if hit is not None:
                st["b"] = self.target_cell(op, hit)
            cells = core.line_cells(a, st["b"])
        elif shape == 'FACE':
            layers = 1
            if op in {'ATTACH', 'ERASE'}:
                layers = self.layers_from_drag(hit0, st["mouse0"], self.mouse)
            direction = normal if op == 'ATTACH' else -normal
            if hit0.ground:
                direction = normal
            cells = core.extrude(st["region"], direction, layers, st["start"])
            st["layers"] = layers
        else:
            return
        st["cells"] = self.finalize_cells(tool, cells)
        self.set_preview(st["cells"], st["colors"])

    def handle_stroke(self, context, event):
        tool = context.scene.voxel_tool
        st = self.stroke
        if event.type in MOVE_EVENTS or event.type in {'LEFT_SHIFT', 'RIGHT_SHIFT'}:
            if event.type in MOVE_EVENTS:
                self.mouse = (event.mouse_x - self.region.x, event.mouse_y - self.region.y)
            if st["shape"] == 'BRUSH':
                if event.type in MOVE_EVENTS:
                    hit = self.hit_at(st["snap"], st["op"], self.mouse)
                    if hit is not None:
                        self.stamp(tool, hit)
            else:
                self.update_drag(tool, event)
            return {'RUNNING_MODAL'}
        if event.type == 'LEFTMOUSE' and event.value == 'RELEASE':
            self.end_stroke()
            self.update_hover(context)
            return {'RUNNING_MODAL'}
        if event.value == 'PRESS' and event.type in {'ESC', 'RIGHTMOUSE'}:
            self.stroke = None
            if st["changed"]:
                self.restore(st["before"])
            self.set_preview(None)
            self.hover_key = None
            return {'RUNNING_MODAL'}
        if event.type == 'TIMER':
            return {'PASS_THROUGH'}
        return {'RUNNING_MODAL'}

    def end_stroke(self):
        st = self.stroke
        if st["shape"] != 'BRUSH' and st["cells"] is not None:
            self.apply(st["cells"])
        self.stroke = None
        self.set_preview(None)
        if st["changed"]:
            self.push_undo(st["before"])
            self.commit()
            self.changed()

    # ------------------------------------------------------------------
    # Eyedropper

    def pick(self, context):
        if self.mouse is None:
            return
        tool = context.scene.voxel_tool
        o, d = self.ray(self.mouse)
        hit = core.raycast(self.grid, o, d)
        if hit is None:
            return
        tool.color_index = int(self.grid[hit.cell])
        if tool.mode == 'BONE':
            name = rig.slot_name(self.obj, int(self.bones[hit.cell]))
            arm = rig.get_armature(self.obj)
            if arm is not None and name not in arm.data.bones:
                info = rig.bone_info(self.obj, arm)
                if info.names:
                    i = core.nearest_bones([hit.cell], info.heads, info.tails, info.radii)[0]
                    name = info.names[int(i)]
            if name:
                tool.active_bone = name
        self.hover_key = None
        self.update_hover(context)


class VOXEL_OT_exit_edit(bpy.types.Operator):
    bl_idname = "voxel.exit_edit"
    bl_label = "Done Editing"
    bl_description = "Leave voxel editing (Tab)"

    @classmethod
    def poll(cls, context):
        return state.ACTIVE is not None

    def execute(self, context):
        state.ACTIVE.request_exit = True
        state.ACTIVE.tag_redraw()
        return {'FINISHED'}


class VOXEL_OT_paint_bones(bpy.types.Operator):
    bl_idname = "voxel.paint_bones"
    bl_label = "Paint Bones"
    bl_description = "Switch to Bones mode and paint which voxels each bone moves"

    @classmethod
    def poll(cls, context):
        obj = context.active_object
        return mesh.is_voxel(obj) and rig.get_armature(obj) is not None

    def execute(self, context):
        tool = context.scene.voxel_tool
        tool.mode = 'BONE'
        arm = rig.get_armature(context.active_object)
        if tool.active_bone not in arm.data.bones:
            tool.active_bone = next((b.name for b in arm.data.bones if b.use_deform), "")
        if state.ACTIVE is None and context.area is not None and context.area.type == 'VIEW_3D':
            bpy.ops.voxel.edit('INVOKE_DEFAULT', reset_tool=False)
        return {'FINISHED'}


def remove_draw_handlers(session):
    for h in getattr(session, "handles", ()):
        try:
            bpy.types.SpaceView3D.draw_handler_remove(h, 'WINDOW')
        except (ValueError, RuntimeError):
            pass
    session.handles = ()


classes = (VOXEL_OT_edit, VOXEL_OT_exit_edit, VOXEL_OT_paint_bones)


def register():
    for c in classes:
        bpy.utils.register_class(c)


def unregister():
    if state.ACTIVE is not None:
        remove_draw_handlers(state.ACTIVE)
        state.ACTIVE = None
    for c in reversed(classes):
        bpy.utils.unregister_class(c)
