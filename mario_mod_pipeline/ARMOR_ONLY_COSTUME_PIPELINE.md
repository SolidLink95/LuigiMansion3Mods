# Face-working armor-only costume pipeline

This workflow was validated in game on 2026-08-18 for Green Knight HD,
Cap'n Weegee HD, and Amazing Luigi HD. It replaces only Luigi's torso armor
and hat, leaves the original face, eyes, and facial deformation data intact,
and uses one-triangle replacements for unwanted cutscene hands and boots.

## FBX contract

The source FBX must contain these mesh objects:

```text
body
hat
boot
hand
```

`body` and `hat` contain the costume geometry. `boot` and `hand` are safe
three-vertex, one-triangle replacements. Extra Blender helper objects such as
`Cube` are ignored because only names listed in `mesh_targets` are extracted.

Use these Global target mappings:

| FBX object | Slot 27 | Slot 28 | Slot 29 | Slot 30 |
|---|---:|---:|---:|---:|
| body | 12 | 7 | 16 | 13 |
| hat | 14 | 11 | 8 | 3 |
| boot | - | - | 0 | 1 |
| hand | - | - | 12 | 4 |

Do not hide the cutscene hand/boot meshes by setting zero counts or editing
their buffers after import. Both approaches produced exploded geometry. Import
the FBX `boot` and `hand` triangles through the normal replacement path.

## Geometry and skinning rules

- Keep `disable_face_deformation` and `neutralize_auxiliary_vertex_data` false.
- Keep `pad_to_original_vertex_count` true so target-owned fixed buffer layouts
  remain valid.
- Preserve the body's FBX bone assignments. If a bone is absent from a target
  skeleton, merge its weight into the closest parent that exists there.
- The runtime-safe format uses at most two nonzero weights per vertex. For
  costume bodies containing three or more influences, enable
  `truncate_preserved_fbx_weights` for `body`; retain the two strongest and
  normalize them to an exact float32 sum of 1.0.
- Use nearest-original target weights for `hat`, `boot`, and `hand`, with
  semantic `bone_107` only as invalid-weight fallback.
- Slots 27-28 use skeleton group 27. Slots 29-30 use skeleton group 28.

## Exact Persistent material transfer

Deep-copy the complete Persistent body material independently into both the
body and hat target material records. Rewrite its texture hashes to owned
Global allocations, then copy the corresponding Persistent texture headers and
image payloads byte-for-byte.

Do not re-encode imported Persistent textures from PNG. That changed the
metallic/normal data and removed the correct shiny appearance.

The validated costume sources are:

| Costume | Persistent slot | Body material mesh | Parameter file |
|---|---:|---:|---|
| Green Knight HD | 38 | 11 | `green_knight.json` |
| Cap'n Weegee HD | 58 | 4 | `capnweege.json` |
| Amazing Luigi HD | 42 | 4 | `amazing.json` |

Protect every destination hash returned by `apply_material_swap` from the PNG
replacement loop. Also preserve Global texture `90D71FE0`: slot 27 face mesh 7
references it, so overwriting it makes the untouched face appear shiny. If a
costume needs that texture class, point its material to another compatible
owned allocation.

The independent configurations are:

```text
config/green_knight_armor_only
config/capnweege_armor_only
config/amazing_armor_only
```

## Rebuild a costume

`run_pipeline.py` accepts a parameter file through `--params`. Do not pass
`--skip-extract` after an FBX update; Blender must regenerate the mesh JSON.

```powershell
python run_pipeline.py --params capnweege.json --skip-package --skip-deploy
```

When the extracted mesh data is already current:

```powershell
python run_pipeline.py --params capnweege.json --skip-extract True --skip-package --skip-deploy
```

The full build is written to:

```text
../tmp/_mods/<mod_name>/romfs
```

## Compact installer creation

Only decompressed Global sections 52, 54, and 65 change in these armor-only
builds. Section 0 and texture-header section 63 remain unchanged.

Example:

```powershell
python run_pipeline.py `
  --params capnweege.json `
  --skip-extract True `
  --skip-build True `
  --skip-deploy `
  --sections 52 54 65 `
  --package-output "..\mods\capnweege_hd_playable_face_works" `
  --mod-name capnweege_hd_playable_face_works `
  --installer-template ".\installer\install_mario_playable.py"
```

Rename the generated `install_<mod_name>.py` to `install.py`. Keep only:

```text
global_section_52.bin
global_section_54.bin
global_section_65.bin
install.py
README.txt
sections.json
```

The builder may relocate compressed physical archive blocks even when all
decompressed content is correct. A full-file physical delta then becomes
hundreds of megabytes. Use this deterministic canonicalization process:

1. Install the section package into a temporary clean ROMFS under `tmp`.
2. Confirm all decompressed sections equal the validated full build.
3. Copy that deterministic in-place `global.dict` and `global.data` back into
   the full build's `romfs`, retaining its original `global.patch`.
4. Repackage sections 52, 54, and 65.
5. Install the final package into a second temporary clean ROMFS.
6. Confirm the installed full-file SHA-256 hashes exactly match the canonical
   full build for both `global.dict` and `global.data`.
7. Confirm the complete installer is at most 20,000,000 bytes.

## Deployment

Copy the canonical full build's `global.dict`, `global.data`, and `global.patch`
into the requested Yuzu mod's `romfs` directory only after build and installer
verification succeeds. Close Yuzu first if it has `global.data` locked.

Validated Yuzu mod names used during development:

```text
green_knight_hd_playable
capnweege_playable
amazing_luigi_playable
```

## Repository layout and authoritative inputs

All paths below are relative to:

```text
W:\coding\TotkBits\tmp\LuigiMansion3Mods\mario_mod_pipeline
```

The clean archives are resolved from `../local.json` through its `romfs` key:

```text
<romfs>/global.dict
<romfs>/global.data
<romfs>/global.patch
<romfs>/Scarescraper/Persistent.dict
<romfs>/Scarescraper/Persistent.data
```

The authoritative edited FBX files are:

```text
../tmp/green_knight/just_armor.fbx
../tmp/capnweege/just_armor_capnweege.fbx
../tmp/amazing/just_armor_amazing.fbx
```

The parameter files and matching rule directories are:

| Costume | Parameters | Rules | Generated full ROMFS |
|---|---|---|---|
| Green Knight | `green_knight.json` | `config/green_knight_armor_only` | `../tmp/_mods/green_knight_hd_playable/romfs` |
| Cap'n Weegee | `capnweege.json` | `config/capnweege_armor_only` | `../tmp/_mods/capnweege_hd_playable_face_works/romfs` |
| Amazing Luigi | `amazing.json` | `config/amazing_armor_only` | `../tmp/_mods/amazing_luigi_hd_playable_face_works/romfs` |

`run_pipeline.py` reads the selected parameter file before constructing its
defaults. The `--params` option must therefore remain available at process
startup; do not replace it with a late parser-only override.

## Exact validated FBX contents

The FBX extraction step calls Blender 4.0 in background/factory-startup mode
and extracts only the keys in `replacement_rules.json` under `mesh_targets`.

The last validated source geometry was:

| Costume | Object | Vertices | Triangles | Original material label |
|---|---|---:|---:|---|
| Green Knight | body | 3,770 | 5,570 | source armor body |
| Green Knight | hat | 962 | 1,326 | body material duplicated onto hat later |
| Green Knight | boot | 3 | 1 | cutscene boot target material |
| Green Knight | hand | 3 | 1 | cutscene hand target material |
| Cap'n Weegee | body | 3,023 | 4,352 | `mat_slot_27_mesh_04_70E35C42` |
| Cap'n Weegee | hat | 614 | 944 | `mat_slot_27_mesh_04_70E35C42` |
| Cap'n Weegee | boot | 3 | 1 | `mat_slot_30_mesh_01_50BDFBBD` |
| Cap'n Weegee | hand | 3 | 1 | `mat_slot_30_mesh_04_0747FEAB` |
| Amazing Luigi | body | 3,664 | 5,432 | `mat_slot_27_mesh_04_3E73194E` |
| Amazing Luigi | hat | 767 | 1,194 | `mat_slot_27_mesh_04_3E73194E` |
| Amazing Luigi | boot | 3 | 1 | `mat_slot_30_mesh_01_50BDFBBD` |
| Amazing Luigi | hand | 3 | 1 | `mat_slot_30_mesh_04_0747FEAB` |

The body must not exceed its smallest target allocation: slots 29-30 have
3,834 available body vertices. The hat must not exceed 1,004 vertices in slots
27-28. The builder aborts rather than growing archive entries.

## Why the four target mappings are correct

The same logical Story Luigi meshes appear at different indices in the four
Global models. The mappings were verified by clean FBX exports and matching
mesh hashes:

| Role | Slots 27-28 hash | Slots 29-30 hash | Target indices |
|---|---|---|---|
| body armor | `202F334F` | `2615B329` | 27:12, 28:7, 29:16, 30:13 |
| hat | `A92A6B5E` | `CE77ED18` | 27:14, 28:11, 29:8, 30:3 |
| unwanted boots | - | `50BDFBBD` | 29:0, 30:1 |
| unwanted hands | - | `0747FEAB` | 29:12, 30:4 |

Slots 27-28 do not receive the tiny boot/hand meshes because those unwanted
costume meshes occur only in the cutscene models 29-30.

## Skin-buffer behavior

Each imported vertex record is 0x30 bytes. A skinned mesh also has one 0x14-byte
skin record per stored vertex. The B004 layout for a skinned mesh is:

```text
u32 skin_offset
u32 vertex_offset
```

Do not reverse these fields. A previous hide-mesh experiment treated
`skin_offset` as `vertex_offset` and wrote 0x30-byte vertex data over 0x14-byte
skin records, causing violently exploded meshes.

For body meshes:

1. Read FBX groups named `bone_<semantic_id>`.
2. Translate semantic IDs through the target skeleton's bone hash table and
   target B103 local indices.
3. When a semantic bone is absent, walk `_bone_parents` until an available
   parent is found.
4. Merge weights that resolve to the same local bone.
5. Sort by descending weight.
6. For bodies listed in `truncate_preserved_fbx_weights`, retain two weights.
7. Normalize the dominant float32 weight and store the second as exactly
   `1.0 - dominant`; remaining IDs and weights are binary zero.

Green Knight currently validates its preserved source weights directly.
Cap'n Weegee and Amazing Luigi enable two-strongest truncation because their
body FBXs contain vertices with three or more usable influences.

For hats and the tiny cutscene triangles, nearest-original target weights are
used. This keeps those meshes compatible with each target model's native
skeleton layout.

## Material-binding behavior

The mesh-to-material relationship is not stored directly in B003. It is
resolved by scanning B007 for this 28-byte marker:

```text
FFFFFFFFFFFFFFFFFFFFFFFF00000000FFFFFFFFFFFFFFFF00000000
```

The preceding u32 points into B006 after subtracting eight. Material record
boundaries are derived from all unique offsets plus the B006 size.

`apply_material_swap` performs these operations:

1. Decode Persistent sections 0, 52, 63, and 65.
2. Resolve the configured source slot and body mesh's complete material record.
3. Copy that record separately into every configured body and hat destination.
4. Replace Persistent texture hashes inside each copy with Global-owned hashes.
5. Assert that the body and hat copies are byte-identical per target slot.
6. Copy each Persistent texture header and pixel allocation into its Global
   destination, replacing only the header's leading hash with the destination
   hash.
7. Return all destination hashes so the later PNG stage skips them.

The 200-byte Cap'n and Amazing source materials fit the 192-byte destinations
because the discarded eight-byte tail is zero. The builder aborts if any
truncated tail byte is nonzero.

## Exact texture mappings

These mappings are authoritative. All target hashes are existing Global
allocations with compatible header and image sizes.

### Green Knight HD

Persistent slot 38, body material mesh 11:

| Persistent hash | Size | Slots 27-28 target | Slots 29-30 target |
|---|---:|---|---|
| `94A3CFD9` | 1,400,320 | `1A783B98` | `43FAD324` |
| `94EC31DD` | 948,736 | `F7B6BF2B` | `F7B6BF2B` |
| `93B8914C` | 701,440 | `198CFD0B` | `4345DE1A` |
| `9491B758` | 701,440 | `F6831E9A` | `33BC6B5E` |

`94EC31DD` deliberately shares `F7B6BF2B` across all slots. Do not map it to
`90D71FE0` again.

### Cap'n Weegee HD

Persistent slot 58, body material mesh 4. All four Story slots share the same
Global allocations:

| Persistent hash | Size | Global target |
|---|---:|---|
| `5D2EF9A7` | 1,400,320 | `1A783B98` |
| `5D775BAB` | 948,736 | `F7B6BF2B` |
| `BA12DCB6` | 701,440 | `198CFD0B` |
| `5D1CE126` | 948,736 | `1AC09D9C` |

### Amazing Luigi HD

Persistent slot 42, body material mesh 4. All four Story slots share:

| Persistent hash | Size | Global target |
|---|---:|---|
| `8F9521FB` | 1,400,320 | `1A783B98` |
| `8FDD83FF` | 948,736 | `F7B6BF2B` |
| `8EA9E36E` | 701,440 | `198CFD0B` |

## The `90D71FE0` regression and required protection

Clean slot 27 mesh 7 uses these texture references:

```text
6E5EBC43
87F73D5D
8FA37F4F
908EBDDC
90D71FE0
```

Its B006 material was never directly replaced, but an early Green Knight build
used `90D71FE0` as a destination for a Persistent armor texture. Because Global
textures are shared, mesh 7 became shiny indirectly. Every armor-only config
must contain:

```json
"preserve_texture_hashes": ["90D71FE0"]
```

The validated clean payload is:

```text
SHA-256: ec3c1fef97134d0017f199ea86265610cb5eccf54e734a4eb32bbc8cd561fc68
```

Both its section-63 header and section-65 image bytes must compare equal to the
clean Global archive after every build.

## Complete rebuild commands

Run from `mario_mod_pipeline`.

### Green Knight

```powershell
python run_pipeline.py --params green_knight.json --skip-package --skip-deploy
```

### Cap'n Weegee

```powershell
python run_pipeline.py --params capnweege.json --skip-package --skip-deploy
```

### Amazing Luigi

```powershell
python run_pipeline.py --params amazing.json --skip-package --skip-deploy
```

Always omit `--skip-extract` for the first build after editing an FBX. A build
using stale extracted JSON can succeed while silently ignoring the new model.

Expected successful logs include:

- 12 mesh redirects;
- body and hat replacements in all four slots;
- boot and hand replacements in slots 29-30;
- an independent Persistent material copy to body and hat in each slot;
- every native costume texture imported into its configured Global target;
- no replacement of `90D71FE0`;
- decompressed archive round-trip success.

## Per-costume installer commands

Run each command only after its full build succeeds.

### Green Knight installer

```powershell
python run_pipeline.py `
  --params green_knight.json --skip-extract True --skip-build True --skip-deploy `
  --sections 52 54 65 `
  --package-output "..\mods\green_knight_hd_playable_face_works" `
  --mod-name green_knight_hd_playable_face_works `
  --installer-template ".\installer\install_mario_playable.py"
```

### Cap'n Weegee installer

```powershell
python run_pipeline.py `
  --params capnweege.json --skip-extract True --skip-build True --skip-deploy `
  --sections 52 54 65 `
  --package-output "..\mods\capnweege_hd_playable_face_works" `
  --mod-name capnweege_hd_playable_face_works `
  --installer-template ".\installer\install_mario_playable.py"
```

### Amazing Luigi installer

```powershell
python run_pipeline.py `
  --params amazing.json --skip-extract True --skip-build True --skip-deploy `
  --sections 52 54 65 `
  --package-output "..\mods\amazing_luigi_hd_playable_face_works" `
  --mod-name amazing_luigi_hd_playable_face_works `
  --installer-template ".\installer\install_mario_playable.py"
```

## Final validated installer results

| Installer | Total bytes | `global.dict` SHA-256 | `global.data` SHA-256 |
|---|---:|---|---|
| Green Knight face works | 6,564,569 | `1A76B2CE65BC1ABCB9DDED88BC3FD7DD0471CACE778CBFD2B2A2B2FAF4CDF182` | `CB4BCA515EE1E381127B6808A5B383BAAA1545E9BC8C042736DEE649A9D3B2E3` |
| Cap'n Weegee face works | 3,064,208 | `2D5C37F7C3EAC05F521686B2630C52C1B504D90CA20B66003A96966C871B0A1C` | `5D8FC6F84E6CDB80930B78FF93093ABDAFDE5970517CBB6AAE835383E949B6D0` |
| Amazing Luigi face works | 2,912,103 | `CC1CA924DC36369BB8E1DBD2185ED5584D5ACB21E0DD3B404B1F509D03864434` | `1700252C10052837A43F5E90DE1C8E332D5F259ED32E9BCBD1D60B7C42341656` |

These hashes describe the canonical deterministic in-place archive layout
produced by the compact installers. If an FBX, material mapping, texture, or
builder rule changes intentionally, new hashes are expected and must be
recorded after repeating the two-stage verification.

## Failure modes and fixes

### Mesh explodes after trying to hide it

Cause: zero index/vertex counts, stale runtime deformation assumptions, or
skin/vertex offset corruption. Fix: restore the clean target and import the
three-vertex `boot`/`hand` mesh through the standard replacement path.

### Face looks metallic or shiny

Cause: a shared face texture allocation, especially `90D71FE0`, was overwritten
even though the face B006 record was untouched. Fix: restore that texture from
clean Global, protect it in config, and remap the costume texture elsewhere.

### Armor loses shine or normal detail

Cause: the native Persistent texture was later re-encoded from a PNG. Fix:
preserve every material-swap destination hash and keep the copied Persistent
header/pixels byte-exact.

### Builder rejects more than two weights

Cause: the costume body contains three or more FBX influences. Fix: list body
in both `preserve_fbx_weights` and `truncate_preserved_fbx_weights`. This keeps
the costume's bones but normalizes its two strongest weights.

### `expected Global section 0 did not change`

Cause: armor-only builds change sections 52, 54, and 65, not section 0. Fix:
package exactly `--sections 52 54 65`.

### Physical installer delta is hundreds of megabytes

Cause: compressed blocks shifted physically even though decompressed content
changed only locally. Fix: use section deltas and the deterministic two-stage
canonicalization workflow. Do not distribute the physical delta.

### Yuzu deployment reports access denied for `global.data`

Cause: Yuzu still has the mod archive open. Close Yuzu fully, then replace the
specific mod's `romfs` directory and verify all three Global files.

## Final regression checklist

Before calling a new costume build complete, confirm every item:

- [ ] The latest FBX was extracted, not a stale mesh JSON.
- [ ] Exactly the configured body, hat, boot, and hand objects were extracted.
- [ ] Body and hat target indices match all four slots in the mapping table.
- [ ] Boot and hand triangles replace only slots 29-30.
- [ ] Every index is within the new vertex count.
- [ ] Stored vertex counts remain within the original allocations.
- [ ] Every skin record has at most two nonzero weights summing exactly to 1.0.
- [ ] Missing bones were merged into an available parent.
- [ ] Face deformation chunks were not disabled or neutralized.
- [ ] The source Persistent body material was copied independently to body and hat.
- [ ] All material texture hashes resolve to existing compatible Global allocations.
- [ ] Native Persistent material textures were not re-encoded from PNG.
- [ ] `90D71FE0` header and image bytes equal clean Global.
- [ ] Only sections 52, 54, and 65 differ from clean Global.
- [ ] The emitted archive reopens and every changed decompressed section round-trips.
- [ ] The final installer succeeds on a fresh clean ROMFS.
- [ ] Installed full-file hashes match the canonical build.
- [ ] The complete installer is no larger than 20,000,000 bytes.
- [ ] `install.py`, `sections.json`, README, and all three section files are present.
- [ ] The full build contains `global.dict`, `global.data`, and `global.patch`.
- [ ] In-game body, hat, materials, face animation, cutscene hands, and boots were tested.
