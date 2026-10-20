import bpy
from bpy.types import Panel

from . import mesh, rig, state


def _voxel_object(context):
    s = state.ACTIVE
    if s is not None:
        try:
            return s.obj
        except ReferenceError:
            return None
    obj = context.active_object
    return obj if mesh.is_voxel(obj) else None


class VoxelPanel:
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "Voxle"


class VoxelSubPanel(VoxelPanel):
    bl_parent_id = "VIEW3D_PT_voxel"

    @classmethod
    def poll(cls, context):
        return _voxel_object(context) is not None


class VIEW3D_PT_voxel(VoxelPanel, Panel):
    bl_label = "Voxle"

    def draw(self, context):
        layout = self.layout
        obj = _voxel_object(context)
        active = context.active_object

        if state.ACTIVE is not None:
            box = layout.box()
            box.label(text=f"Editing {obj.name if obj else ''}", icon='EDITMODE_HLT')
            row = box.row()
            row.scale_y = 1.5
            row.operator("voxel.exit_edit", text="Done  (Tab)", icon='CHECKMARK')
            return

        if obj is not None:
            row = layout.row()
            row.scale_y = 1.6
            row.operator("voxel.edit", text="Edit Voxels", icon='EDITMODE_HLT')
            return

        if active is not None and active.type == 'ARMATURE':
            targets = rig.bound_objects(active)
            if targets:
                col = layout.column(align=True)
                col.label(text="Voxle rig", icon='OUTLINER_OB_ARMATURE')
                row = col.row(align=True)
                row.operator("voxel.rig_mode", text="Pose", icon='POSE_HLT').mode = 'POSE'
                row.operator("voxel.rig_mode", text="Edit Bones", icon='EDITMODE_HLT').mode = 'EDIT'
                if active.mode == 'POSE':
                    col.operator("voxel.reset_pose", icon='LOOP_BACK')
                col.separator()
                for o in targets:
                    op = col.operator("voxel.rig_mode", text=f"Back to {o.name}", icon='OBJECT_DATAMODE')
                    op.mode = 'VOXELS'
                    op.target = o.name
                if active.mode == 'EDIT':
                    col.label(text="Weights update when you leave Edit Mode", icon='INFO')
                return

        col = layout.column(align=True)
        row = col.row()
        row.scale_y = 1.5
        row.operator("voxel.new", text="New Voxle Object", icon='ADD')
        col.operator("voxel.import_vox", text="Import .vox", icon='IMPORT')


class VIEW3D_PT_voxel_tools(VoxelSubPanel, Panel):
    bl_label = "Tools"

    def draw(self, context):
        layout = self.layout
        tool = context.scene.voxel_tool
        obj = _voxel_object(context)

        col = layout.column(align=True)
        col.row(align=True).prop(tool, "mode", expand=True)
        row = col.row(align=True)
        row.prop(tool, "shape", expand=True, icon_only=True)
        col.label(text=bpy.types.UILayout.enum_item_name(tool, "shape", tool.shape) + " - " +
                  bpy.types.UILayout.enum_item_description(tool, "shape", tool.shape).split(" (")[0])

        col = layout.column(align=True)
        row = col.row(align=True)
        row.prop(tool, "brush_size")
        row.prop(tool, "brush_sphere", toggle=True, icon='MESH_UVSPHERE', icon_only=True)
        row = col.row(align=True)
        row.label(text="Mirror")
        row.prop(tool, "mirror_x", toggle=True)
        row.prop(tool, "mirror_y", toggle=True)
        row.prop(tool, "mirror_z", toggle=True)

        if tool.mode == 'BONE':
            arm = rig.get_armature(obj)
            if arm is None:
                layout.label(text="Add an armature in Rigging first", icon='ERROR')
            else:
                layout.prop_search(tool, "active_bone", arm.data, "bones", icon='BONE_DATA')
                layout.label(text="Bright = painted, dim = automatic", icon='INFO')
        else:
            row = layout.row(align=True)
            if len(obj.voxel.palette) == 256:
                row.prop(obj.voxel.palette[tool.color_index], "color", text="")
            row.prop(tool, "color_index", text="#")

        row = layout.row(align=True)
        row.prop(tool, "show_grid", toggle=True, icon='MESH_GRID')
        row.prop(tool, "show_wire", toggle=True, icon='SHADING_WIRE')
        row.prop(tool, "show_help", toggle=True, icon='QUESTION')


class VIEW3D_PT_voxel_palette(VoxelSubPanel, Panel):
    bl_label = "Palette"
    bl_options = {'DEFAULT_CLOSED'}

    def draw(self, context):
        layout = self.layout
        obj = _voxel_object(context)
        tool = context.scene.voxel_tool
        row = layout.row(align=True)
        row.operator_menu_enum("voxel.palette_preset", "preset", text="Presets", icon='COLOR')
        row.operator("voxel.replace_color", text="Replace", icon='FILE_REFRESH')
        layout.label(text="Click colors in the viewport palette to pick", icon='INFO')
        pal = obj.voxel.palette
        if len(pal) != 256:
            return
        grid = layout.grid_flow(row_major=True, columns=8, even_columns=True, even_rows=True, align=True)
        grid.label(text="")  # index 0 is "empty"; keeps each row one hue, light to dark
        for i in range(1, 256):
            cell = grid.row(align=True)
            cell.alert = i == tool.color_index
            cell.prop(pal[i], "color", text="")


class VIEW3D_PT_voxel_model(VoxelSubPanel, Panel):
    bl_label = "Model"
    bl_options = {'DEFAULT_CLOSED'}

    def draw(self, context):
        layout = self.layout
        obj = _voxel_object(context)
        vp = obj.voxel
        X, Y, Z = vp.dims
        col = layout.column(align=True)
        col.label(text=f"Grid {X} x {Y} x {Z}", icon='MESH_GRID')
        row = col.row(align=True)
        row.operator("voxel.resize", text="Resize", icon='FULLSCREEN_ENTER')
        row.operator("voxel.crop", text="Fit to Model", icon='PIVOT_BOUNDBOX')
        layout.prop(vp, "voxel_size")
        row = layout.row(align=True)
        row.prop(vp, "auto_smooth")
        sub = row.row(align=True)
        sub.active = vp.auto_smooth
        sub.prop(vp, "auto_smooth_angle", text="")

        col = layout.column(align=True)
        for label, op in (("Flip", "voxel.flip"), ("Rotate", "voxel.rotate")):
            row = col.row(align=True)
            row.label(text=label)
            for a in "XYZ":
                row.operator(op, text=a).axis = a
        row = col.row(align=True)
        row.label(text="Symmetrize")
        for a in "XYZ":
            row.operator("voxel.symmetrize", text=a).axis = a
        row = col.row(align=True)
        row.label(text="Shift")
        for a in "XYZ":
            sub = row.row(align=True)
            o = sub.operator("voxel.shift", text=f"-{a}")
            o.axis, o.amount = a, -1
            o = sub.operator("voxel.shift", text=f"+{a}")
            o.axis, o.amount = a, 1

        layout.operator("voxel.clear", icon='TRASH')
        row = layout.row(align=True)
        row.operator("voxel.import_vox", text="Import", icon='IMPORT')
        row.operator("voxel.export_vox", text="Export .vox", icon='EXPORT')


class VIEW3D_PT_voxel_rig(VoxelSubPanel, Panel):
    bl_label = "Rigging"

    def draw(self, context):
        layout = self.layout
        obj = _voxel_object(context)
        vp = obj.voxel
        arm = rig.get_armature(obj)
        editing = state.ACTIVE is not None

        if arm is None:
            col = layout.column(align=True)
            col.label(text="Add a skeleton fitted to the model:")
            col.enabled = not editing
            grid = col.grid_flow(columns=2, even_columns=True, align=True)
            icons = {'HUMANOID': 'ARMATURE_DATA', 'QUADRUPED': 'ARMATURE_DATA',
                     'CHAIN': 'BONE_DATA', 'SINGLE': 'BONE_DATA'}
            tool = context.scene.voxel_tool
            for ident, name, _ in rig.TEMPLATE_ITEMS:
                op = grid.operator("voxel.add_armature", text=name, icon=icons[ident])
                op.template, op.ik = ident, tool.rig_ik
            row = col.row(align=True)
            row.prop(tool, "rig_ik")
            sub = row.row(align=True)
            sub.active = tool.rig_ik
            sub.prop(vp, "limit_reach")
            layout.prop(vp, "armature", text="Or use")
            return

        layout.prop(vp, "armature", text="")
        if not vp.bound:
            row = layout.row()
            row.scale_y = 1.4
            row.enabled = not editing
            row.operator("voxel.bind", icon='LINKED')
            return

        col = layout.column(align=True)
        col.enabled = not editing
        row = col.row(align=True)
        row.scale_y = 1.3
        row.operator("voxel.rig_mode", text="Pose", icon='POSE_HLT').mode = 'POSE'
        row.operator("voxel.rig_mode", text="Edit Bones", icon='EDITMODE_HLT').mode = 'EDIT'

        col = layout.column(align=True)
        col.prop(vp, "weight_smooth")
        col.prop(vp, "bend_loops")
        col.prop(vp, "split_parts")
        if any(c.type == 'LIMIT_DISTANCE' for pb in arm.pose.bones for c in pb.constraints):
            col.prop(vp, "limit_reach")

        col = layout.column(align=True)
        col.operator("voxel.paint_bones", icon='BRUSH_DATA')
        row = col.row(align=True)
        row.enabled = not editing
        row.operator("voxel.refresh_weights", text="Refresh", icon='FILE_REFRESH')
        row.operator("voxel.clear_bone_paint", text="Clear Paint", icon='X')
        row.operator("voxel.unbind", icon='UNLINKED')


def context_menu(self, context):
    if mesh.is_voxel(context.active_object) and state.ACTIVE is None:
        self.layout.operator("voxel.edit", icon='EDITMODE_HLT')
        self.layout.separator()


classes = (
    VIEW3D_PT_voxel,
    VIEW3D_PT_voxel_tools,
    VIEW3D_PT_voxel_palette,
    VIEW3D_PT_voxel_model,
    VIEW3D_PT_voxel_rig,
)


def register():
    for c in classes:
        bpy.utils.register_class(c)
    bpy.types.VIEW3D_MT_object_context_menu.prepend(context_menu)


def unregister():
    bpy.types.VIEW3D_MT_object_context_menu.remove(context_menu)
    for c in reversed(classes):
        bpy.utils.unregister_class(c)
