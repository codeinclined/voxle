"""Voxle game engine export: shared options, plus a section per engine (Godot, Unity, Unreal, other)."""

import os
import textwrap

import bpy
from bpy.props import BoolProperty, EnumProperty, FloatProperty, PointerProperty, StringProperty
from bpy.types import Operator, Panel, PropertyGroup

from . import mesh, rig, state

GODOT_COLLISION_SUFFIX = {'NONE': "", 'TRIMESH': "-col", 'CONVEX': "-convcol", 'ONLY': "-colonly"}


def wrapped_label(layout, context, text, icon='NONE'):
    """A label that wraps to the sidebar's width (Blender labels don't wrap on their own)."""
    width = context.region.width if context.region else 300
    scale = context.preferences.system.ui_scale
    chars = max(12, int((width - (50 if icon != 'NONE' else 20) * scale) / (6.5 * scale)))
    col = layout.column(align=True)
    for i, line in enumerate(textwrap.wrap(text, chars) or [""]):
        col.label(text=line, icon=icon if i == 0 else 'BLANK1' if icon != 'NONE' else 'NONE')
    return col


# ----------------------------------------------------------------------------
# Settings


class VoxelExportSettings(PropertyGroup):
    # Shared by every engine.
    target: EnumProperty(
        name="Export", default='ACTIVE',
        items=[
            ('ACTIVE', "Active Model", "The active voxel model (or the model bound to the active armature)"),
            ('SELECTED', "Selected Models", "Every selected voxel model, or models bound to selected armatures"),
            ('ALL', "All Models", "Every voxel model in the scene"),
        ],
    )
    one_file_per_model: BoolProperty(
        name="One File per Model", default=True,
        description="Write each model (with its rig) to its own file, named after the model. "
                    "Off: everything goes into a single file named after the .blend",
    )
    merge_faces: BoolProperty(
        name="Merge Flat Faces", default=True,
        description="Combine flat runs of same-colored voxel faces into larger quads, often "
                    "cutting the triangle count by more than half. Rigged models keep every "
                    "vertex that moves differently, so nothing cracks when they bend",
    )
    deform_bones_only: BoolProperty(
        name="Deform Bones Only", default=True,
        description="Leave out IK controls, poles and helper bones. Animation driven by them is "
                    "baked onto the deforming bones, which is all an engine needs",
    )
    animations: BoolProperty(name="Animations", default=True, description="Export the rig's actions")
    animation_mode: EnumProperty(
        name="Animations", default='ACTIONS',
        items=[
            ('ACTIONS', "All Actions", "Every action that fits the rig, one engine animation each"),
            ('ACTIVE_ACTIONS', "Active Action", "Only the action currently assigned to the rig"),
            ('NLA_TRACKS', "NLA Tracks", "One animation per NLA track"),
        ],
    )
    compact_animations: BoolProperty(
        name="Compact Animations", default=True,
        description="Drop keyframes that don't change anything, for smaller files",
    )
    colors: EnumProperty(
        name="Colors", default='TEXTURE',
        items=[
            ('TEXTURE', "Palette Texture",
             "A tiny 256x1 palette image with nearest filtering. Recoloring a palette entry "
             "recolors every voxel using it"),
            ('VERTEX', "Vertex Colors", "Colors stored on the mesh, with a plain white material"),
        ],
    )

    # Godot
    godot_project: StringProperty(
        name="Godot Project", subtype='DIR_PATH',
        description="Your Godot project folder (the one containing project.godot)",
    )
    godot_folder: StringProperty(
        name="Folder", default="assets/models",
        description="Folder inside the project to export to (created if needed)",
    )
    godot_format: EnumProperty(
        name="Format", default='GLB',
        items=[('GLB', "glTF Binary (.glb)", "One self-contained file"),
               ('GLTF_SEPARATE', "glTF (.gltf + .bin + textures)", "Separate files")],
    )
    godot_collision: EnumProperty(
        name="Collision", default='NONE',
        description="Godot import hint for static (unrigged) models: adds a physics body",
        items=[
            ('NONE', "None", "No collision"),
            ('TRIMESH', "Mesh Collision", "Static body with an exact mesh shape (-col)"),
            ('CONVEX', "Convex Collision", "Static body with a convex hull shape (-convcol)"),
            ('ONLY', "Collision Only", "Invisible collision shape only (-colonly)"),
        ],
    )
    godot_loop: BoolProperty(
        name="Loop Animations", default=False,
        description="Name animations with Godot's -loop hint so they import as looping",
    )

    # Other engines
    other_path: StringProperty(name="Folder", subtype='DIR_PATH', description="Folder to export to")
    other_format: EnumProperty(
        name="Format", default='GLB',
        items=[('GLB', "glTF Binary (.glb)", ""),
               ('GLTF_SEPARATE', "glTF (.gltf + .bin)", ""),
               ('FBX', "FBX (.fbx)", "For engines and tools that prefer FBX")],
    )
    other_up: EnumProperty(
        name="Up", default='Y',
        items=[(a, a, "") for a in ('Y', 'Z', '-Y', '-Z', 'X', '-X')],
        description="Up axis of the target engine (glTF supports Y or Z)",
    )
    other_forward: EnumProperty(
        name="Forward", default='-Z',
        items=[(a, a, "") for a in ('-Z', 'Z', 'Y', '-Y', 'X', '-X')],
        description="Forward axis of the target engine (FBX only; glTF always uses its own)",
    )
    other_scale: FloatProperty(
        name="Scale", default=1.0, min=0.0001, soft_max=1000.0,
        description="Scale everything on export (e.g. 100 for engines working in centimeters)",
    )


def settings(context):
    return context.scene.voxel_export


# ----------------------------------------------------------------------------
# Helpers


def godot_project_root(path):
    """The folder at or above path containing project.godot, or None."""
    if not path:
        return None
    p = os.path.abspath(bpy.path.abspath(path))
    while True:
        if os.path.isfile(os.path.join(p, "project.godot")):
            return p
        parent = os.path.dirname(p)
        if parent == p:
            return None
        p = parent


def godot_project_name(root):
    try:
        with open(os.path.join(root, "project.godot"), encoding="utf-8") as f:
            for line in f:
                if line.startswith("config/name="):
                    return line.split("=", 1)[1].strip().strip('"')
    except OSError:
        pass
    return os.path.basename(root)


def godot_export_dir(s):
    root = godot_project_root(s.godot_project)
    base = root or os.path.abspath(bpy.path.abspath(s.godot_project))
    return os.path.join(base, *[p for p in s.godot_folder.replace("\\", "/").split("/") if p])


def models_to_export(context, target):
    voxels = [o for o in context.scene.objects if mesh.is_voxel(o)]
    if target == 'ALL':
        return voxels
    pool = [context.active_object] if target == 'ACTIVE' else list(context.selected_objects)
    out = []
    for o in pool:
        if o is None:
            continue
        if mesh.is_voxel(o):
            out.append(o)
        elif o.type == 'ARMATURE':
            out.extend(rig.bound_objects(o))
    seen, unique = set(), []
    for o in out:
        if o.name not in seen and o.name in context.scene.objects:
            seen.add(o.name)
            unique.append(o)
    return unique


def extension(fmt):
    return {'GLB': ".glb", 'GLTF_SEPARATE': ".gltf", 'FBX': ".fbx"}[fmt]


class ExportCopies:
    """Temporary export copies of voxel models, standing in for the originals under their
    real names (and with Godot suffixes if any). Everything is restored on exit."""

    def __init__(self, context, models, s, name_suffix="", scale=1.0):
        self.context, self.models, self.s = context, models, s
        self.name_suffix, self.scale = name_suffix, scale

    def __enter__(self):
        ctx = self.context
        self.renamed, self.temps, self.scaled = [], [], []
        self.selection = [o.name for o in ctx.selected_objects]
        self.active = ctx.view_layer.objects.active.name if ctx.view_layer.objects.active else None
        self.mode = ctx.object.mode if ctx.object is not None else 'OBJECT'
        if self.mode != 'OBJECT':
            bpy.ops.object.mode_set(mode='OBJECT')
        for o in ctx.selected_objects:
            o.select_set(False)
        coll = ctx.scene.collection
        self.export_objects = []
        armatures = []
        for obj in self.models:
            arm = rig.get_armature(obj) if obj.voxel.bound else None
            name = obj.name
            obj.name = name + ".voxel_src"
            self.renamed.append((obj, name))
            me = bpy.data.meshes.new(name + ".export")
            tmp = bpy.data.objects.new(name + self.name_suffix, me)
            coll.objects.link(tmp)
            self.temps.append(tmp)
            mesh.rebuild(obj, target=tmp, optimize=self.s.merge_faces)
            if arm is not None:
                tmp.parent = arm
                tmp.parent_type = 'OBJECT'
                tmp.matrix_parent_inverse = obj.matrix_parent_inverse.copy()
                tmp.matrix_basis = obj.matrix_basis.copy()
                mod = tmp.modifiers.new("Armature", 'ARMATURE')
                mod.object = arm
                if arm not in armatures:
                    armatures.append(arm)
            else:
                tmp.matrix_world = mesh.world_matrix(obj)
            if self.s.colors == 'VERTEX' and tmp.data.color_attributes:
                tmp.data.color_attributes.active_color = tmp.data.color_attributes[0]
            self.export_objects.append(tmp)
        self.export_objects += armatures
        if self.scale != 1.0:
            for o in self.export_objects:
                if o.parent is None:
                    self.scaled.append((o, o.scale.copy(), o.location.copy()))
                    o.scale = o.scale * self.scale
                    o.location = o.location * self.scale
        for o in self.export_objects:
            o.select_set(True)
        ctx.view_layer.objects.active = self.export_objects[0]
        return self

    def __exit__(self, *exc):
        ctx = self.context
        for o, scale, loc in self.scaled:
            o.scale, o.location = scale, loc
        for tmp in self.temps:
            me = tmp.data
            bpy.data.objects.remove(tmp)
            if me.users == 0:
                bpy.data.meshes.remove(me)
        for obj, name in self.renamed:
            obj.name = name
        for o in ctx.view_layer.objects:
            o.select_set(o.name in self.selection)
        if self.active and self.active in ctx.view_layer.objects:
            ctx.view_layer.objects.active = ctx.view_layer.objects[self.active]
        if self.mode != 'OBJECT' and ctx.object is not None:
            try:
                bpy.ops.object.mode_set(mode=self.mode)
            except RuntimeError:
                pass
        return False


class LoopNames:
    """Temporarily give every action Godot's -loop suffix."""

    def __init__(self, enabled):
        self.enabled = enabled

    def __enter__(self):
        self.renamed = []
        if self.enabled:
            for a in bpy.data.actions:
                if "loop" not in a.name.lower() and "cycle" not in a.name.lower():
                    self.renamed.append((a, a.name))
                    a.name = a.name + "-loop"
        return self

    def __exit__(self, *exc):
        for a, name in self.renamed:
            a.name = name
        return False


def gltf_export(filepath, fmt, s, yup=True):
    bpy.ops.export_scene.gltf(
        filepath=filepath,
        export_format=fmt,
        use_selection=True,
        export_apply=False,
        export_skins=True,
        export_def_bones=s.deform_bones_only,
        export_animations=s.animations,
        export_animation_mode=s.animation_mode,
        export_optimize_animation_size=s.compact_animations,
        export_force_sampling=True,
        export_yup=yup,
        export_materials='EXPORT' if s.colors == 'TEXTURE' else 'PLACEHOLDER',
        export_vertex_color='NONE' if s.colors == 'TEXTURE' else 'ACTIVE',
        export_cameras=False,
        export_lights=False,
    )


def fbx_export(filepath, s, up, forward, scale):
    bpy.ops.export_scene.fbx(
        filepath=filepath,
        use_selection=True,
        object_types={'ARMATURE', 'MESH'},
        use_armature_deform_only=s.deform_bones_only,
        add_leaf_bones=False,
        bake_anim=s.animations,
        bake_anim_use_all_actions=s.animations and s.animation_mode == 'ACTIONS',
        bake_anim_use_nla_strips=s.animations and s.animation_mode == 'NLA_TRACKS',
        global_scale=scale,
        apply_scale_options='FBX_SCALE_ALL',
        axis_up=up,
        axis_forward=forward,
        mesh_smooth_type='FACE',
        path_mode='COPY',
        embed_textures=True,
    )


# ----------------------------------------------------------------------------
# Operator


class VOXEL_OT_export_engine(Operator):
    bl_idname = "voxel.export_engine"
    bl_label = "Export"
    bl_description = "Export voxel models (with their rigs and animations) for a game engine"

    engine: EnumProperty(items=[('GODOT', "Godot", ""), ('OTHER', "Other", "")], default='GODOT')

    @classmethod
    def poll(cls, context):
        return state.ACTIVE is None

    def execute(self, context):
        s = settings(context)
        models = models_to_export(context, s.target)
        if not models:
            self.report({'ERROR'}, "No voxel models to export (check the Export setting)")
            return {'CANCELLED'}

        if self.engine == 'GODOT':
            if not s.godot_project:
                self.report({'ERROR'}, "Choose your Godot project folder first")
                return {'CANCELLED'}
            folder = godot_export_dir(s)
            fmt, up, forward, scale = s.godot_format, 'Y', '-Z', 1.0
            suffix = GODOT_COLLISION_SUFFIX[s.godot_collision]
            if suffix and any(o.voxel.bound for o in models):
                self.report({'WARNING'}, "Collision hints skipped for rigged models")
        else:
            if not s.other_path:
                self.report({'ERROR'}, "Choose a folder to export to first")
                return {'CANCELLED'}
            folder = os.path.abspath(bpy.path.abspath(s.other_path))
            fmt, up, forward, scale = s.other_format, s.other_up, s.other_forward, s.other_scale
            suffix = ""
            if fmt != 'FBX' and up not in {'Y', 'Z'}:
                self.report({'ERROR'}, "glTF can only use Y or Z up")
                return {'CANCELLED'}
        os.makedirs(folder, exist_ok=True)

        if s.one_file_per_model:
            batches = [[o] for o in models]
        else:
            batches = [models]
        written = []
        for batch in batches:
            if len(batch) == 1:
                stem = bpy.path.clean_name(batch[0].name)
            else:
                stem = bpy.path.clean_name(bpy.path.display_name_from_filepath(bpy.data.filepath) or "voxle")
            path = os.path.join(folder, stem + extension(fmt))
            name_suffix = suffix if not any(o.voxel.bound for o in batch) else ""
            try:
                with ExportCopies(context, batch, s, name_suffix, scale if fmt != 'FBX' else 1.0), \
                        LoopNames(self.engine == 'GODOT' and s.godot_loop):
                    if fmt == 'FBX':
                        fbx_export(path, s, up, forward, scale)
                    else:
                        gltf_export(path, fmt, s, yup=(up == 'Y'))
            except Exception as e:  # report exporter failures instead of a traceback popup
                self.report({'ERROR'}, f"Export failed: {e}")
                return {'CANCELLED'}
            written.append(path)

        where = folder
        if self.engine == 'GODOT':
            root = godot_project_root(s.godot_project)
            if root:
                rel = os.path.relpath(folder, root).replace(os.sep, "/")
                where = "res://" + ("" if rel == "." else rel)
        self.report({'INFO'}, f"Exported {len(written)} file(s) to {where}")
        return {'FINISHED'}


# ----------------------------------------------------------------------------
# Panels (their own "Voxle Export" sidebar tab)


class ExportPanel:
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "Voxle Export"


class VIEW3D_PT_voxel_export(ExportPanel, Panel):
    bl_label = "Game Engine Export"

    def draw(self, context):
        layout = self.layout
        s = settings(context)
        layout.use_property_split = True
        layout.use_property_decorate = False
        models = models_to_export(context, s.target)
        layout.prop(s, "target")
        if not models:
            wrapped_label(layout, context, "No voxel models to export", icon='INFO')
        elif s.target != 'ACTIVE':
            layout.label(text=f"{len(models)} model(s)", icon='OUTLINER_OB_MESH')
        if len(models) > 1:
            layout.prop(s, "one_file_per_model")

        col = layout.column(heading="Geometry")
        col.prop(s, "merge_faces")
        col.prop(s, "colors")
        col = layout.column(heading="Rig")
        col.prop(s, "deform_bones_only")
        col.prop(s, "animations")
        sub = col.column()
        sub.active = s.animations
        sub.prop(s, "animation_mode", text="Mode")
        sub.prop(s, "compact_animations")
        if state.ACTIVE is not None:
            wrapped_label(layout, context, "Leave voxel editing to export", icon='ERROR')


class VIEW3D_PT_voxel_export_godot(ExportPanel, Panel):
    bl_label = "Godot"
    bl_parent_id = "VIEW3D_PT_voxel_export"

    def draw(self, context):
        layout = self.layout
        s = settings(context)
        layout.use_property_split = True
        layout.use_property_decorate = False
        layout.prop(s, "godot_project", text="Project")
        root = godot_project_root(s.godot_project)
        if s.godot_project:
            if root:
                wrapped_label(layout, context, f"Godot project: {godot_project_name(root)}", icon='CHECKMARK')
            else:
                wrapped_label(layout, context, "No project.godot found here", icon='ERROR')
        layout.prop(s, "godot_folder")
        layout.prop(s, "godot_format")
        layout.prop(s, "godot_collision")
        layout.prop(s, "godot_loop")
        if root:
            rel = os.path.relpath(godot_export_dir(s), root).replace(os.sep, "/")
            layout.label(text="res://" + ("" if rel == "." else rel + "/"), icon='FILE_FOLDER')
        row = layout.row()
        row.scale_y = 1.5
        row.enabled = bool(s.godot_project)
        row.operator("voxel.export_engine", text="Export to Godot", icon='EXPORT').engine = 'GODOT'


class VIEW3D_PT_voxel_export_unity(ExportPanel, Panel):
    bl_label = "Unity"
    bl_parent_id = "VIEW3D_PT_voxel_export"
    bl_options = {'DEFAULT_CLOSED'}

    def draw(self, context):
        wrapped_label(self.layout, context, "Coming soon. For now, use Other Engine with FBX, "
                      "Y up and Z forward.", icon='INFO')


class VIEW3D_PT_voxel_export_unreal(ExportPanel, Panel):
    bl_label = "Unreal Engine"
    bl_parent_id = "VIEW3D_PT_voxel_export"
    bl_options = {'DEFAULT_CLOSED'}

    def draw(self, context):
        wrapped_label(self.layout, context, "Coming soon. For now, use Other Engine with FBX, "
                      "Z up and scale 100.", icon='INFO')


class VIEW3D_PT_voxel_export_other(ExportPanel, Panel):
    bl_label = "Other Engine"
    bl_parent_id = "VIEW3D_PT_voxel_export"
    bl_options = {'DEFAULT_CLOSED'}

    def draw(self, context):
        layout = self.layout
        s = settings(context)
        layout.use_property_split = True
        layout.use_property_decorate = False
        layout.prop(s, "other_path")
        layout.prop(s, "other_format")
        col = layout.column(align=True)
        col.prop(s, "other_up")
        sub = col.row()
        sub.active = s.other_format == 'FBX'
        sub.prop(s, "other_forward")
        layout.prop(s, "other_scale")
        if s.other_format != 'FBX' and s.other_up not in {'Y', 'Z'}:
            wrapped_label(layout, context, "glTF supports Y or Z up only", icon='ERROR')
        row = layout.row()
        row.scale_y = 1.5
        row.enabled = bool(s.other_path)
        row.operator("voxel.export_engine", text="Export", icon='EXPORT').engine = 'OTHER'


classes = (
    VoxelExportSettings,
    VOXEL_OT_export_engine,
    VIEW3D_PT_voxel_export,
    VIEW3D_PT_voxel_export_godot,
    VIEW3D_PT_voxel_export_unity,
    VIEW3D_PT_voxel_export_unreal,
    VIEW3D_PT_voxel_export_other,
)


def register():
    for c in classes:
        bpy.utils.register_class(c)
    bpy.types.Scene.voxel_export = PointerProperty(type=VoxelExportSettings)


def unregister():
    del bpy.types.Scene.voxel_export
    for c in reversed(classes):
        bpy.utils.unregister_class(c)
