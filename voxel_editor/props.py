import math

import bpy
from bpy.props import (
    BoolProperty,
    CollectionProperty,
    EnumProperty,
    FloatProperty,
    FloatVectorProperty,
    IntProperty,
    IntVectorProperty,
    PointerProperty,
    StringProperty,
)
from bpy.types import PropertyGroup

# Set while bulk-assigning palette colours so the per-colour update doesn't fire 255 times.
suspend_palette_updates = False


def _on_palette_color(self, context):
    if suspend_palette_updates:
        return
    from . import mesh
    mesh.palette_changed(self.id_data)


def _on_geometry_setting(self, context):
    from . import mesh
    mesh.refresh(self.id_data)


def _on_limit_reach(self, context):
    from . import rig
    rig.set_limit_reach(rig.get_armature(self.id_data), self.limit_reach)


def _is_armature(self, obj):
    return obj.type == 'ARMATURE'


class VoxelPaletteColor(PropertyGroup):
    color: FloatVectorProperty(
        name="Color", subtype='COLOR_GAMMA', size=3, min=0.0, max=1.0,
        default=(1.0, 1.0, 1.0), update=_on_palette_color,
    )


class VoxelBoneSlot(PropertyGroup):
    # Bone name lives in the built-in ``name`` property.
    pass


class VoxelObjectSettings(PropertyGroup):
    is_voxel: BoolProperty(default=False)
    dims: IntVectorProperty(size=3, default=(32, 32, 32), min=1, max=256)
    voxel_size: FloatProperty(
        name="Voxel Size", default=0.1, min=0.0001, soft_max=10.0, unit='LENGTH',
        description="Edge length of one voxel", update=_on_geometry_setting,
    )
    data: StringProperty()
    bone_data: StringProperty()
    bone_slots: CollectionProperty(type=VoxelBoneSlot)
    palette: CollectionProperty(type=VoxelPaletteColor)
    palette_image: PointerProperty(type=bpy.types.Image)
    armature: PointerProperty(
        name="Armature", type=bpy.types.Object, poll=_is_armature,
        description="Armature that deforms this voxel model",
    )
    bound: BoolProperty(default=False)
    auto_smooth: BoolProperty(
        name="Auto Smooth", default=False, update=_on_geometry_setting,
        description="Shade smooth, keeping edges sharper than the angle below sharp (like "
                    "Shade Auto Smooth). Voxel edges stay crisp while bent elbows and knees "
                    "shade as smooth curves",
    )
    auto_smooth_angle: FloatProperty(
        name="Angle", default=math.radians(30.0), min=0.0, max=math.pi, subtype='ANGLE',
        update=_on_geometry_setting,
        description="Edges between faces at a steeper angle than this stay sharp",
    )
    bend_loops: IntProperty(
        name="Bend Loops", default=2, min=0, max=8, update=_on_geometry_setting,
        description="Loop cuts on each side of an elbow or knee. More gives a rounder bend, "
                    "fewer a blockier one, 0 bends inside a single voxel. Only added to the "
                    "rigged mesh, never shown while editing voxels",
    )
    weight_smooth: IntProperty(
        name="Joint Blend", default=1, min=0, max=8,
        description="How many voxels bone influence blends over at joints (0 = blocky)",
        update=_on_geometry_setting,
    )
    split_parts: BoolProperty(
        name="Rigid Parts", default=False,
        description="Split the mesh into one closed piece per bone so every part moves "
                    "rigidly, like a Minecraft-style character",
        update=_on_geometry_setting,
    )
    limit_reach: BoolProperty(
        name="Limit Reach", default=True, update=_on_limit_reach,
        description="Keep IK hands and feet within reach of the shoulder or hip so limbs "
                    "can't be pulled out and stretched",
    )
    rest_hash: StringProperty()


MODE_ITEMS = [
    ('ATTACH', "Attach", "Add voxels onto surfaces (1)", 'ADD', 0),
    ('ERASE', "Erase", "Remove voxels (2)", 'REMOVE', 1),
    ('PAINT', "Paint", "Recolor existing voxels (3)", 'BRUSH_DATA', 2),
    ('BONE', "Bones", "Assign voxels to the active bone for rigging (4)", 'BONE_DATA', 3),
]

SHAPE_ITEMS = [
    ('BRUSH', "Brush", "Freehand brush; drag to draw (V)", 'MESH_UVSPHERE', 0),
    ('BOX', "Box", "Drag a rectangle; hold Shift while dragging to set its height (B)", 'MESH_PLANE', 1),
    ('LINE', "Line", "Drag a straight line (L)", 'IPO_LINEAR', 2),
    ('FACE', "Face", "Whole connected face; drag to extrude or push in several layers (F)", 'FACESEL', 3),
    ('FILL', "Fill", "Connected region of the same color (C)", 'MOD_SOLIDIFY', 4),
]


def _on_show_wire(self, context):
    from . import state
    if state.ACTIVE is not None:
        state.ACTIVE.apply_wire(self.show_wire)


class VoxelToolSettings(PropertyGroup):
    mode: EnumProperty(name="Mode", items=MODE_ITEMS, default='ATTACH')
    shape: EnumProperty(name="Shape", items=SHAPE_ITEMS, default='BOX')
    brush_size: IntProperty(name="Size", default=1, min=1, max=32, description="Brush size in voxels ([ and ])")
    brush_sphere: BoolProperty(name="Round", default=False, description="Use a round brush instead of a cube")
    mirror_x: BoolProperty(name="X", description="Mirror edits across X (X key)")
    mirror_y: BoolProperty(name="Y", description="Mirror edits across Y (Y key)")
    mirror_z: BoolProperty(name="Z", description="Mirror edits across Z (Z key)")
    color_index: IntProperty(name="Color", default=9, min=1, max=255, description="Active palette index")
    active_bone: StringProperty(name="Bone", description="Bone that Bone mode assigns voxels to")
    show_help: BoolProperty(name="Show Shortcuts", default=True)
    show_grid: BoolProperty(name="Show Grid", default=True)
    show_wire: BoolProperty(name="Wireframe", default=True, update=_on_show_wire,
                            description="Outline every voxel face while editing (W)")
    rig_ik: BoolProperty(name="IK Controls", default=True,
                         description="Add IK controls for arms and legs: foot and hand targets "
                                     "plus knee and elbow poles")


classes = (
    VoxelPaletteColor,
    VoxelBoneSlot,
    VoxelObjectSettings,
    VoxelToolSettings,
)


def register():
    for c in classes:
        bpy.utils.register_class(c)
    bpy.types.Object.voxel = PointerProperty(type=VoxelObjectSettings)
    bpy.types.Scene.voxel_tool = PointerProperty(type=VoxelToolSettings)


def unregister():
    del bpy.types.Scene.voxel_tool
    del bpy.types.Object.voxel
    for c in reversed(classes):
        bpy.utils.unregister_class(c)
