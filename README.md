# Voxel Editor for Blender

MagicaVoxel/Goxel-style voxel modeling inside Blender (4.2+, tested on 5.2), with a
one-click armature that fits the model and voxel-based skin weights.

## Install

```sh
cd voxel_editor && blender --command extension build   # makes voxel_editor-1.0.0.zip
```

Then in Blender use **Edit > Preferences > Get Extensions > ⌄ > Install from Disk** and pick
the zip. You can also symlink `voxel_editor/` into your extensions `user_default` folder
while developing.

## Quick start

1. **Add > Voxel Object** (or **Sidebar (N) > Voxel > New Voxel Object**). You get an empty grid and editing starts right away in Box + Attach.
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

In **Sidebar > Voxel > Rigging**, pick a template: **Humanoid**, **Quadruped**, **Chain** or
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
  folding over itself (up to about 150 degrees). **Bend Loops** (Rigging panel, default 4)
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

## Other features

- 256-color palette per object (index 0 = empty, as in MagicaVoxel). The default palette
  has one hue per column with 8 shades; a MagicaVoxel Classic preset is also included.
  Edit colors in the Palette panel. Colors live in a 256×1 texture plus a color attribute,
  so they survive game-engine export.
- Model tools: resize, fit grid to model, flip, rotate 90°, symmetrize, shift, replace color, clear.
- **.vox import/export** (File > Import/Export), including multi-model scenes with transforms.
- Voxel data is stored on the object, so it survives save/reload and Blender's undo.

## Tests

```sh
blender -b --factory-startup --python tests/test_headless.py
# Opens a window and drives the editor with simulated input (real input is ignored):
blender --factory-startup --enable-event-simulate --python tests/test_interactive.py -- /tmp/shots
```
