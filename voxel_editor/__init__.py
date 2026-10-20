"""Voxel Editor - MagicaVoxel/Goxel-style voxel modeling and rigging inside Blender."""

bl_info = {
    "name": "Voxel Editor",
    "description": "MagicaVoxel-style voxel modeling, painting and easy rigging",
    "blender": (4, 2, 0),
    "version": (1, 0, 0),
    "category": "3D View",
    "location": "3D Viewport > Sidebar > Voxel, Add > Voxel Object",
}

import bpy
from bpy.app.handlers import persistent

from . import edit, mesh, ops, props, rig, state, ui, vox

modules = (props, ops, rig, vox, edit, ui)


@persistent
def _on_load_pre(*_):
    if state.ACTIVE is not None:
        edit.remove_draw_handlers(state.ACTIVE)
        state.ACTIVE = None


@persistent
def _on_load_post(*_):
    # Generated palette images can lose their pixels between sessions; rebuild them.
    for obj in bpy.data.objects:
        if obj.type == 'MESH' and obj.voxel.is_voxel and obj.voxel.palette_image is not None:
            mesh.update_palette_image(obj)


@persistent
def _on_undo(*_):
    if state.ACTIVE is not None:
        state.ACTIVE.needs_reload = True


HANDLERS = (
    (bpy.app.handlers.load_pre, _on_load_pre),
    (bpy.app.handlers.load_post, _on_load_post),
    (bpy.app.handlers.undo_post, _on_undo),
    (bpy.app.handlers.redo_post, _on_undo),
)


def register():
    for m in modules:
        m.register()
    for lst, fn in HANDLERS:
        lst.append(fn)


def unregister():
    for lst, fn in HANDLERS:
        if fn in lst:
            lst.remove(fn)
    for m in reversed(modules):
        m.unregister()
