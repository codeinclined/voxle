# Voxle

MagicaVoxel/Goxel-style voxel modeling inside Blender® (4.2+, tested on 5.2), with a
one-click armature that fits the model, voxel-based skin weights, and export to game engines.

> **Made with Claude Code.** Voxle was created with [Claude Code](https://claude.com/claude-code),
> Anthropic's AI coding assistant, working from directions and feedback by Josh Taylor.

## Install

```sh
cd voxle && blender --command extension build --output-dir ..   # makes voxle-1.0.0.zip
```

Then in Blender use **Edit > Preferences > Get Extensions > ⌄ > Install from Disk** and pick
the zip. You can also symlink `voxle/` into your extensions `user_default` folder
while developing.

If you installed the add-on before it was renamed Voxle ("Voxel Editor"), uninstall that
one first: both register the same tools. Files saved with it open fine in Voxle.

## Quick start

1. **Add > Voxle Object** (or **Sidebar (N) > Voxle > New Voxle Object**). You get an empty grid and editing starts right away in Box + Attach.
2. Click on surfaces to build. A palette sits in the bottom-left corner of the viewport; click a color to select it.
3. Press **Tab** when you're done. The model is an ordinary mesh, so modifiers, materials and exporters like glTF and FBX all work.

## Edit mode controls

| Key | Action |
| --- | --- |
| LMB | Apply the current tool (drag to paint strokes / boxes / lines) |
| Ctrl + LMB | Inverse: Attach ↔ Erase; in Bones mode, un-assign |
| Alt + LMB, or I | Eyedropper: pick a color (or a bone in Bones mode) |
| 1 2 3 4 | Attach · Erase · Paint · Bones |
| V B L F C | Brush · Box · Line · Face · Fill |
| [ ] | Brush size |
| X Y Z | Toggle mirror on that axis |
| Ctrl+Z / Ctrl+Shift+Z | Undo / redo (each stroke is a step) |
| G / W / H | Toggle the grid / wireframe / shortcut help |
| Tab or Esc | Leave edit mode |

MMB, the scroll wheel, numpad views and the navigation gizmo work as usual while editing.

**Shapes**
- **Box**: drag a rectangle on any face or the floor. Hold **Shift** while dragging to raise it into a 3D block.
- **Face**: click a face to select the whole flat, connected surface (like MagicaVoxel's Face tool). Drag to extrude it (Attach) or push it in (Erase) by several layers.
- **Fill**: works on every connected voxel of the same color, so you can recolor or delete a whole region.

## Rigging

In **Sidebar > Voxle > Rigging**, pick a template: **Humanoid**, **Quadruped**, **Chain** or
**Single Bone**. The humanoid template reads the model's shape to place the bones. It finds the
crotch from the gap between the legs, the neck from where the head narrows, and whether the
arms are in a T-pose or hanging down. The armature is created, parented and bound right away.

**IK Controls** (on by default) adds IK to the limbs. In Pose Mode, grab a hand or foot (G)
and the whole arm or leg follows. Hands and feet stay put when you move the hips or chest, so
feet stay planted.
- **Arms:** drag the red `hand_ik` control. The hand stays on the end of the forearm and
  turns with it; rotate `hand_ik` to turn the hand. The elbow pole (`elbow_pole`) is carried
  by an aim bone that follows the hand, so box-shaped arms bend flat instead of rolling or
  twisting. Helper bones like that live in a hidden "Mechanism" bone collection.
- **Elbows and knees** bend as a smooth curve: on the bound mesh the joint region is
  loop-cut into thin slices with weight easing from the upper to the lower limb, and the
  Armature modifier uses Preserve Volume, so a box limb bends into a clean arc instead of
  folding over itself (up to about 150 degrees). **Bend Loops** (Rigging panel, default 2)
  sets how many loop cuts go on each side of the joint: more for a rounder bend, fewer for
  a blockier one, 0 to bend inside a single voxel. The extra loops only exist on the rigged
  mesh: voxel editing always shows plain voxels.
- **Legs:** the feet are the IK targets and keep their orientation, so they stay flat; rotate
  a foot to turn it. Each red `knee_pole` sits out to the side of its foot and moves with it,
  so turning a foot aims its knee.
- An IK limb only flips when its target lines up with its pole. With this setup that only
  happens for a foot straight out to the side at hip height, or a hand pointing straight
  opposite its rest direction (into the chest for a T-pose, straight overhead for arms at
  the sides). If a joint spins around, move its pole bone.
- A quadruped gets a small red `*_foot_ik` control at the bottom of each leg plus a pole.

**Limit Reach** (on by default, also in the Rigging panel once bound) stops hands and feet
from being dragged farther than the limb can reach, so arms and legs can't be stretched.

The rest pose is unchanged by any of this. Turn the IK checkbox off before clicking a template to get plain FK bones.

- **Pose / Edit Bones** buttons jump straight to the armature. When you move bones in
  Edit Mode, the weights recompute automatically as you leave it.
- Weights come from voxels, not Blender's heat method, so the result is always clean.
  Each voxel belongs to its nearest bone, measured relative to how thick that body part is,
  and a limb never claims voxels on the body's side of its joint. Joints bend inside the
  limb, so the torso stays a solid box when an arm or leg moves. **Joint Blend** sets how
  many voxels into the limb the bend spreads (0 = crisp and blocky, 1 = a little smoother).
  Each level down the bone hierarchy is also a tiny bit thinner (0.6% of a voxel), so where
  a bent elbow or knee slides over its parent the faces don't z-fight. The offset disappears
  if you unbind.
- The skin is joined only at joints. Where a limb merely touches the body, such as arms
  resting against the sides or legs touching each other, each part gets its own surface, so
  it swings away cleanly instead of dragging the body with it.
- **Rigid Parts** splits the mesh into one closed piece per bone, for a Minecraft-style rig with no stretching.
- **Paint Bones** (or key 4 in edit mode) shows each bone's voxels in its own color.
  Painted voxels are bright and automatic ones are dim. Paint voxels to fix any
  assignment you don't like. Ctrl+click sends a voxel back to automatic.
- **Symmetrize X** and **Flip X** swap `.L`/`.R` bone assignments.

## Game engine export

Open the **Voxle Export** tab in the sidebar (N). Settings at the top apply to every engine:

- **Export**: the active model, selected models (selecting a rig picks its models) or every
  voxel model in the scene, one file per model by default.
- **Merge Flat Faces** (on): greedy meshing merges flat runs of same-colored faces into
  larger quads, often cutting triangles by more than half. On rigged models only faces that
  move rigidly with one bone are merged, so nothing cracks when they bend.
- **Colors**: the 256x1 palette texture (nearest filtering, so colors never bleed) or vertex
  colors with a plain material.
- **Deform Bones Only** (on): IK controls, poles and helper bones are left out; animation
  they drive is baked onto the deforming bones.
- **Animations**: all actions, the active action, or NLA tracks, with optional keyframe
  compaction.

Export never touches your scene: it builds a temporary optimized copy, exports it under the
model's real name, and removes it again.

**Godot** — exports glTF™ (`.glb` or `.gltf`), which Godot 4 imports natively and which is a
stable format, unlike `.tscn` scenes. Pick your project folder (Voxle finds
`project.godot` and shows the `res://` path) and a folder inside it (`assets/models` by
default), then press **Export to Godot**. Godot import hints are optional: a **Collision** suffix (`-col`, `-convcol`,
`-colonly`) for static models, and **Loop Animations** (`-loop`).

**Unity®** and **Unreal® Engine** sections are placeholders for now; use **Other Engine**
meanwhile (Unity: FBX, Y up, Z forward; Unreal: FBX, Z up, scale 100).

**Other Engine** — glTF or FBX to any folder, with the up axis, forward axis (FBX) and scale.

Note that engines skin with plain linear blending, while Blender's preview uses Preserve
Volume, so very tight elbow and knee bends can look slightly thinner in the engine.

## Other features

- 256-color palette per object (index 0 = empty, as in MagicaVoxel). The default palette
  has one hue per column with 8 shades; a MagicaVoxel Classic preset is also included.
  Edit colors in the Palette panel. Colors live in a 256×1 texture plus a color attribute,
  so they survive game-engine export.
- **Auto Smooth** (Model panel, off by default): shade smooth while keeping edges sharper
  than the angle (30° by default) sharp, like Shade Auto Smooth. Voxel edges stay crisp and
  bent elbows and knees shade as smooth curves. It's reapplied whenever the mesh rebuilds.
- Model tools: resize, fit grid to model, flip, rotate 90°, symmetrize, shift, replace color, clear.
- **.vox import/export** (File > Import/Export), including multi-model scenes with transforms.
- Voxel data is stored on the object, so it survives save/reload and Blender's undo.

## Tests

```sh
blender -b --factory-startup --python tests/test_headless.py
# Opens a window and drives the editor with simulated input (real input is ignored):
blender --factory-startup --enable-event-simulate --python tests/test_interactive.py -- /tmp/shots
```

## License

Voxle is free software under the [GNU General Public License v3.0 or later](LICENSE), like
Blender itself.

## Trademarks

Voxle is an independent project. It is not sponsored by, endorsed by or affiliated with the
Blender Foundation, the Godot Foundation, Unity Technologies, Epic Games, the Khronos Group,
Autodesk or ephtracy. Engine and format names are used only to say what Voxle works with.

- Blender is a registered trademark of the Blender Foundation in the EU and USA.
- GODOT and GODOT ENGINE are trademarks of the Godot Foundation.
- Unity is a trademark or registered trademark of Unity Technologies or its affiliates in the
  U.S. and elsewhere.
- Unreal and Unreal Engine are trademarks or registered trademarks of Epic Games, Inc. in the
  United States of America and elsewhere.
- glTF and the glTF logo are trademarks of the Khronos Group Inc.
- FBX is a registered trademark of Autodesk, Inc.
- MagicaVoxel is developed by ephtracy; Goxel by Guillaume Chereau.
- All other names are the property of their respective owners.

## Credits

Voxle is maintained by Josh Taylor <josh@codeinclined.com> and was built with
[Claude Code](https://claude.com/claude-code).
