# Ghost Luigi transparency: failed attempts

This file records transparency changes that were tested in game and did not
make the Story Luigi model transparent. Do not reuse these changes as opacity
controls without new evidence.

The tests apply to the slot-13 ghost materials copied onto selected meshes in
Story Luigi slots 27 and 28 by
`mario_mod_pipeline/res/build_ghost_luigi_material_mod.py`. Slots 29 and 30
mirror slot 27.

## B006 `+0x80` and `+0x240`: `0.5` scalars

These are corresponding scalar parameters in slot 13's two source materials.
They were initially suspected to multiply opacity or additive glow.

Tested changes included:

- `0.5 -> 0.35` (scale `0.7`)
- `0.5 -> 0.245` (scale `0.49`)
- `0.5 -> 0.1715` (scale `0.343`)

Observed result: the model did not gain the intended transparency. These
values are not the material transparency control.

Current status: both values are restored to the clean slot-13 value `0.5`.

## B006 `+0xAC` and `+0x26C`: apparent RGBA alpha components

These offsets contain the fourth components of two parameter groups:

- Source material 0: `(20, 10, 4, 1.0)` at `+0xA0..+0xAC`
- Source material 1: `(0, 0.1, 0, 0.2)` at `+0x260..+0x26C`

The matching B007 parameter position made the fourth components look like
possible alpha values.

Tested changes:

- B006 `+0xAC`: `1.0 -> 0.7`
- B006 `+0x26C`: `0.2 -> 0.14`

Observed result: the model had no transparency. The fourth components do not
control final model opacity in this material transfer.

Current status: restored to `1.0` and `0.2`.

## B003 descriptor `+0x24`: source descriptor hash

The initial material transfer copied the slot-13 B003 vertex format at `+0x10`
and the eight-byte shader/layout pair at `+0x18`. The hash-like field at
descriptor `+0x24` was then suspected to select an alpha-blended pipeline or
render state.

Tested change: copy slot 13's four-byte `+0x24` field from the mapped source
submesh into every ghosted target-mesh descriptor.

Observed result: the model remained opaque. Copying this field does not enable
transparency.

Current status: the target meshes retain their original `+0x24` values.

## Values that are not transparency controls

B006 `+0xA0`, `+0xA4`, and `+0x100` are validated glow/intensity controls.
Changing them affects glow, not transparency. Their treatment should remain
independent of opacity experiments.

## Confirmed transparency control

B006 `+0x84` and `+0x244` occupy the same B007 parameter slot in the two
source materials. A cross-model scan found natural values from `0.7` to `1.0`
for this slot among materials using the same shader. Changing both from `1.0`
to `0.7` produced working transparency in game, confirming this slot as the
material opacity multiplier.

The subsequent build uses `0.35`, which is 50% of the confirmed `0.7` opacity.
