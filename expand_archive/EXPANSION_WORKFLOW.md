# LM3 Global Archive Expansion — Complete Workflow

**Status: VALIDATED IN-GAME 2026-08-21.** Slot 27 mesh 14 (hat, `A92A6B5E`) expanded
from 1,004 to 2,008 vertices / 1,774 to 3,548 triangles on Yuzu, title `0100DCA0064A6000`.
This document is written for an AI assistant continuing this work. It contains every
format fact needed to expand a mesh beyond its vanilla allocation, the exact algorithm,
and the failure history that led here. Read this INSTEAD of re-deriving anything.

## TL;DR — the one sentence that matters

**Four data stores must agree about a chunk's location and its section's size:
`global.dict` (entry table), `global.data` (section content incl. the section-0
sub-entry table), `global.patch` HEADER (section sizes), and `global.patch`'s 13
COMPRESSED CHUNK-TABLE COPIES (the table the game actually reads).**
The first two are what all prior tooling edited; the last one is what the game
trusts for chunk offsets/sizes — editing only `global.data`'s section 0 does
nothing in-game (this was the root cause of two exploded-mesh iterations, and of
the repo's historical "swap_slots parses in tools but fails in-game" mystery).

## Files in this folder

| File | Role |
|---|---|
| `build_expanded_global.py` | Historical single-mesh proof-of-concept builder (appends at the section end — now known to collide with the 1.4 update's extension region, see below). **The maintained builder is `mario_mod_pipeline/res/build_expanded_global.py`**, which places chunks in the freed vanilla regions instead. |
| `lm3_slot_swap.py` | Bundled archive reader (`read_archive`/`decompress_entry`/`parse_subentries`/`group_models`) |
| `extract_fbx_replacement.py` | Blender 4.0 headless FBX → mesh JSON extractor |
| `EXPANSION_WORKFLOW.md` | This document |

## Making a new expanded mod (the maintained pipeline, validated on `90s_luigi_expanded`)

All steps run from the repo root. The maintained builder is
`mario_mod_pipeline/res/build_expanded_global.py`; configure it by editing its
module constants (same convention as every other builder in this repo).

### 1. Extract the replacement meshes

```powershell
& "C:/Program Files/Blender Foundation/Blender 4.0/blender.exe" --background --factory-startup `
  --python mario_mod_pipeline/res/extract_fbx_replacement.py -- `
  tmp/<work>/<model>.fbx tmp/<work>/<model>.meshes.json <object_name> [<object_name> ...]
```

FBX contract: triangulated meshes, Y-up (the builder converts with
`(x, y, z) -> (x, -z, y)`), vertex groups named `bone_<semantic_id>` matching the
target skeleton group's `0x7105` ids (unresolvable bones fall back through
`_bone_parents`, then to the nearest original vertex's weights).

### 2. Configure the builder constants

Edit the top of `mario_mod_pipeline/res/build_expanded_global.py`:

- `MESH_JSON` — the extracted JSON from step 1.
- `TARGETS` — `{slot: {mesh_index: object_name}}` for every replaced mesh.
- `SKELETON_GROUP_FOR_SLOT` — skeleton group per touched slot (Luigi: 27/28→27, 29/30→28).
- `MIRROR_SLOTS` — `{mirror: source}` for slots that should render the source's
  model verbatim (their whole record set is redirected; skeletons must be
  hash-compatible, e.g. groups 27/28 are hash-identical).
- `MATERIAL_COPIES` — `{slot: [(target_mesh, source_mesh)]}` whole-B006-record copies.
- `TEXTURES_DIR` — folder of `<GlobalTextureHash>.png` files re-encoded into the
  fixed section-65 allocations (formats: 0x16 BC5_SNORM normal maps, 0x1D/0x1E ASTC).
  Remember the shared-texture rule: hashes used by untouched models must not be replaced.
- `MOD_NAME` / `OUTPUT` — build output location under `tmp/_mods/`.

**Capacity budget (the hard constraint):** the new chunks are placed inside the
freed vanilla B005 regions of `TARGETS ∪ MIRROR_SLOTS`. Sum of new chunk sizes
(vanilla chunk size + 16-aligned new streams per replaced mesh) must fit the sum
of those freed regions. For Story Luigi 27–30 the freed space is 26.9 MB and two
~5.9 MB chunks use less than half of it. The builder hard-errors if it doesn't
fit — growing section 54 is NEVER the answer (see the extension-address-space
section below). Also watch the patch-table compression headroom: the rewritten
chunk table must re-deflate into its fixed 516,880-byte slot (the 90s build used
516,625).

### 3. Build + verify

```powershell
python mario_mod_pipeline/res/build_expanded_global.py
# -> tmp/_mods/<MOD_NAME>/romfs/{global.dict,global.data,global.patch}
```

The built-in verification must print `verification passed`; it asserts (among
other things) that section 54 keeps its exact vanilla size, changes are confined
to the freed regions, every placed chunk sits fully inside them, the patch
header is byte-identical, and each patch-table copy carries exactly the intended
record edits.

### 4. Package for distribution

```powershell
python mario_mod_pipeline/res/package_expanded_global.py `
  --original <clean_romfs>/global.dict `
  --built tmp/_mods/<MOD_NAME>/romfs/global.dict `
  --output mods/<mod_name> --mod-name <mod_name>
```

Emits in-place MGP0/MGPZ deltas per changed section, the patch header delta
(empty when nothing grew — the normal case), ONE compressed chunk-table slot
(spliced 13× at install time), `manifest.json` with SHA-256s of everything, and
copies `mario_mod_pipeline/installer/install_expanded_global.py` as `install.py`.
The installer only supports fixed-size sections — a manifest with a grown
section is rejected by design.

### 5. Verify the package end-to-end (required before shipping)

Copy the packaged mod to a scratch folder, run
`python install.py <clean_romfs>` there, and confirm the produced
`romfs/global.{dict,data,patch}` SHA-256s match the canonical build from step 3
byte-for-byte. The installer itself verifies `built_dict_sha256` and reports if
`global.data` differs (zlib version drift — self-consistent but not byte-exact).

### 6. Deploy for testing (Yuzu LayeredFS; close Yuzu first if global.data is locked)

```powershell
Copy-Item tmp\_mods\<MOD_NAME>\romfs\* `
  "$env:USERPROFILE\AppData\Roaming\yuzu\load\0100DCA0064A6000\<MOD_NAME>\romfs" -Force
```

### 7. In-game regression checklist

- The replaced meshes render correctly (all touched slots AND mirror slots).
- The 1.4-update models are intact — these exploded when the section was grown:
  Global slots 478, 480, 481, 484, 485, 808 (and up to 15 update-relocated
  models in total; see the extension-address-space section).
- Faces/morph-target areas of untouched meshes, and shared-texture surfaces
  (the shiny-face regression class).

---

# Format reference (all little-endian; all facts verified against the clean dump)

## global.dict

Header (16 bytes): `<IHBBIBBBB` =
`magic=0xA9F32458, unknown=0x0104, compressed=1, pad, largest, file_count=120, chunk_count=52, strings=3, pad`.

- **`largest` (u32 @ +8) = size of the LARGEST COMPRESSED entry** (clean: 0xCC368E8 =
  entry 65's comp size). Recompute after any entry change. NOT the largest decompressed.
- Then `chunk_count` × 24-byte chunk records (language/audio streaming metadata — never touched).
- Then `file_count` × 16-byte entries at `16 + chunk_count*24`: `<IIIHBB` =
  `offset, decompressed_size, compressed_size, u1, u2, u3`.
  - **`u2` selects the data file**: 0=`.data`, 1=`.debug`, 2=`.nxpc` (the 3 name strings at
    the dict tail). All sections that matter (0, 52, 53, 54, 63, 65) are `u2=0` → `global.data`.
  - `u1`/`u3` unknown; preserve verbatim.
  - Entries are 8-byte aligned in the data file and NOT sorted by offset (multi-file
    interleaving). **The game accepts entries appended past the vanilla EOF** — growing an
    entry = write new compressed payload at 8-aligned EOF, update its offset/sizes. Proven in-game.

Key section indexes: **0** = chunk sub-entry table, **52** = model/material data,
**53** = skeletons/animations (B008/B009/B100/0x71xx), **54** = vertex/index buffers (B005),
**63** = texture headers, **65** = texture image data.

## Section 0 — sub-entry table

- 13,373 × 24-byte `0x1301` records (asset id table; never touched), then 146,018 × 12-byte
  records: `<HHII` = `kind, flags, size, offset`. `offset` is into the DECOMPRESSED section
  that stores that kind. `group_models()` splits per-model at each `0xB006`. Slot 27 = Story
  Luigi (also 28, 29, 30); the Global archive has 815 models.
- **The game does NOT read chunk offsets/sizes from this copy** — see global.patch below.
  Still patch it, so tools and the patch table stay consistent.

## Model chunks (per slot) and their storage sections

`B006`=materials(52), `B005`=vertex+index buffers(54), `B00C`/`B00A`/`B00B`=face
morph/transform metadata(52), `B004`=per-mesh stream offsets(52), `B003`=mesh descriptors(52),
`B007`=material bindings/shader param offsets(52), `B001`=4×4 model matrix(52),
`B002`=`(model_hash, B007_u32_count, (B004_u32_count<<16)|mesh_count)`(52), `B100`=3-byte
name ref(53), `B008`/`B009`=float bbox-ish(53), `B101`=model hash(52), `B102`=inverse bind
matrices 176×64(52), `B103`=bone hash list(52).

## B003 mesh descriptor (0x40 bytes each, `b003.offset + mesh_index*0x40` in section 52)

| Offset | Field | Notes |
|---|---|---|
| +0x00 | u32 mesh hash | asset id; keep on replacement |
| +0x04 | u32 index_offset | B005-chunk-relative |
| +0x08 | u32 index_flags | `count = flags & 0xFFFFFF`; top byte `0x80` = 8-bit indices, `0x00` = 16-bit |
| +0x0C | u32 vertex_count | |
| +0x10 | u32 format | e.g. `0x2280`; keep |
| +0x14 | u32 mesh_index*4 | keep |
| +0x18 | u64 layout/shader hash pair | keep |
| +0x20 | u16 section_count, u16 section_base | cumulative across meshes (`base[k+1]=base[k]+count[k]`); indexes B007-related per-mesh sections; keep |
| +0x24 | u32 per-mesh hash | keep |
| +0x28 | u16 c1, u16 c2 | **vertices with exactly 1 / 2 bone influences** |
| +0x2C | u16 c3, u16 c4 | **vertices with exactly 3 / 4 bone influences** |
| +0x30.. | zeros / variant data | keep |

- `+0x28 == 0xFFFFFFFF` doubles as the "unskinned" marker → 12-byte B004 record.
- **Skinning-batch invariant (verified on all 32 meshes of slots 27–30): the vertex &
  skin buffers are SORTED by influence count — first c1 vertices have 1 influence, next c2
  have 2, etc.** When writing a new mesh: sort vertices by influence count (stable), update
  c1..c4 to sum to the new vertex_count. (Vanilla has a few meshes where counts undercount
  trailing verts; counts ≥ actual grouping is tolerated, counts < vertex_count is what the
  vanilla-stale path relies on and is risky for expanded meshes.)

## B004 per-mesh record (cursor walks 16 bytes per skinned mesh, 12 per unskinned)

Skinned (16 bytes): `<IIII` = `skin_offset, vertex_offset, aux_offset, sentinel_offset`.
Unskinned (12): `vertex_offset, aux_offset, sentinel_offset`. All B005-chunk-relative.
**NEVER swap skin/vertex offsets** (historic exploded-geometry bug).

## B005 chunk stream layout (per mesh, all offsets chunk-relative)

Vanilla order per mesh: `skin (0x14/vtx) → vtx (0x30/vtx) → aux → sentinel (8/vtx)`,
each 16-aligned, meshes sequential, then ALL index buffers at the chunk tail.
- Vertex record 0x30: `float3 pos @0, float u @0x0C, float3 normal @0x10, float v(=1-uv.y) @0x1C,
  0x10 unknown bytes @0x20` (tangent-ish — copy from the nearest-by-position original vertex).
- Skin record 0x14: `4×u8 B103-local bone ids, 4×float weights`. Weights: keep ≤2 strongest,
  dominant as float32, second = exact float32 `1.0 - dominant` (validated convention).
- Aux stream: unknown semantics (48-byte records w/ float3 for the hat; morph-ish). **Stale
  vanilla aux content is validated-safe for replaced hat geometry** — copy the vanilla bytes
  verbatim into the new layout, size unchanged (`sentinel_offset - aux_offset` preserved).
- Sentinel stream: 8 bytes/vertex; for the hat the vanilla content is the uniform pattern
  `FF FF FF FF FF 7F FF 7F` — extend by repeating it `new_vertex_count` times.
- Section 54 is FULLY TILED by the 815 B005 chunks — zero inter-chunk gaps. There is no
  free space; expansion REQUIRES growing the section.

## global.patch — THE CRITICAL FILE (102,961,845 bytes)

| Range | Content |
|---|---|
| `0x000 – 0xAA8` | Plain header: magic `0x01555555`, then 13 records (stride **0xD0**, first record's fields start at +0x18). Each record holds decompressed sizes of the shared sections — section 52 @rec+0x10, 53 @+0x14, **54 @+0x18** — plus aligned pool sizes and per-part offsets. Global fields: `head[6]=0xAA8` (first table copy), `head[7]=0x7E310` (per-copy stride/compressed slot size), `head[2]=0x669278` (cooked blob offset), `head[4]` = cooked blob decompressed size. |
| `0xAA8 – 0x669278` | **13 byte-identical zlib streams (stride 0x7E310 = 516,880), each inflating to a 2,175,456-byte chunk sub-entry table** — same record format and SAME ADDRESS SPACE as section 0, but a superset (153,848 records, 813 models, different model order). **THIS is the chunk table the game reads.** |
| `0x669278 – EOF` | One zlib stream → 156,439,768 bytes of cooked runtime data (matches `head[4]`; does not contain section 52/54 content — left untouched). |

### CRITICAL: the update's extension address space (discovered 2026-08-22)

The patch chunk table is not just a copy of section 0 — it is the **1.4 update's
patched view**, and it maps updated models into address space **beyond the vanilla
section ends**, with the data materialized from the patch at runtime:

- **B005 records extend to `0xBA63260`** — `0x881380` (8.9 MB) past vanilla
  section 54's `0xB1E1EE0`. 15 patch models live there (patch models 113, 121,
  158, 163, 466, 470, 489, 490, 500–503, 804, 805, 807); their old base-region
  chunks became unreferenced gaps in the patch tiling.
- Section-52 kinds (B001–B007, B101–B103) extend ~`0xD000` past vanilla
  section 52's `0x1124284` the same way.

**Consequence: NEVER grow section 54 (or 52).** Any base-section data appended at
`0xB1E1EE0..0xBA63260` overlays the update's relocated models — the base bytes
win at runtime, so those ~15 vanilla models render replacement/garbage vertex
data and explode in-game (observed on Global slots 478/480/481/484/485/808 in
the first `90s_luigi_expanded` build; the modded meshes themselves worked, which
is what made the collision non-obvious). Expanded chunks must instead be placed
**inside the freed vanilla B005 regions** of the slots being relocated/mirrored
(for Story Luigi 27–30 these are contiguous: `0x36405C0..0x4FEF7E0`, 26.9 MB —
verified referenced by nothing else in section 0 or any patch table copy). With
the section size unchanged, the patch header needs no edits at all.

To find a specific model in the patch table, DO NOT use slot index (ordering differs).
Match by the model's chunk-size signature, or simplest: search for the unique 8-byte
`<II>(size, offset)` of its B005 record preceded by u16 kind `0xB005`.

**Slot-size constraint:** the vanilla table re-deflated at zlib level 9 = 516,794 bytes vs
the fixed 516,880-byte slot — only **86 bytes of headroom**. Larger table diffs may
overflow; if `zlib.compress(level=9)` exceeds the slot, try
`zlib.compressobj(9, zlib.DEFLATED, 15, 9)` or restructure. Pad the slot tail with zeros;
the stream self-terminates so padding is ignored.

---

# The expansion algorithm (mirrors `build_expanded_global.py`)

Given: clean ROMFS (`local.json` → `romfs`), extracted mesh JSON, target slot+mesh.

1. **Decompress sections 0, 52, 53, 54** from `global.data` (entry list from `global.dict`).
2. **Locate the target**: B003 descriptor (verify hash), B004 record (must be 16-byte/skinned
   for this path), old `index/skin/vertex/aux/sentinel` offsets, old vertex/index counts.
3. **Resolve skinning**: FBX vertex-group names `bone_<semantic_id>` → skeleton group's
   `0x7105` (id→hash, section 53) → B103 hash list (hash→local index). Walk `_bone_parents`
   for missing bones. Merge per-local-bone, keep 2 strongest, normalize (exact float32
   complement). Record influence count (1 or 2) per vertex.
4. **Sort vertices by influence count** (stable), remap faces, compute `c1..c4`.
5. **Build payloads** in sorted order: vertex records templated from the nearest-by-position
   original vertex (pos/u/normal/v overwritten); skin records; u16 index buffer.
6. **Relocate + grow the chunk**: `new_chunk = vanilla_chunk_bytes` (verbatim — keeps every
   other mesh's chunk-relative offsets valid) `+ [16-aligned: skin | vtx | vanilla_aux_copy +
   sentinel_pattern×new_vc | idx]`. `sentinel_offset = aux_offset + vanilla_aux_size`
   (contiguous, aux size preserved). Place the chunk 16-aligned **inside the freed
   vanilla B005 regions** of the relocated/mirrored slots — NEVER append past the
   vanilla section end (see the extension-address-space section above). Section 54
   keeps its exact vanilla size.
7. **Patch section 52**: descriptor `+0x04..+0x0F` (new index_offset, index_count, vertex_count),
   `+0x28/+0x2C` (c1..c4), and the B004 record (4 new offsets). Nothing else.
8. **Patch section 0**: the slot's B005 record `size, offset` at `table_offset+4`.
9. **Rebuild `global.data` pure-append**: keep every vanilla byte; append zlib-level-9
   payloads of sections 0, 52, 54 at 8-aligned EOF; update those three dict entries
   (offset/dec/comp) and dict `largest` = max comp over all entries.
10. **Rewrite `global.patch`** (the step that makes it actually work in-game):
    a. Header: byte-identical — section sizes do not change with freed-region placement.
    b. Tables: verify the 13 compressed copies are identical; inflate one; find the unique
       `B005 + (old_size, old_offset)` record; write `(new_size, new_offset)`; deflate
       level 9; assert ≤ 0x7E310; zero-pad; splice into all 13 slots.
11. **Verify** (all automated in the builder): vanilla `global.data` is a byte-exact prefix;
    the 3 sections round-trip; relocated chunk's vanilla prefix is verbatim; descriptor/B004
    match intent; skin stream is influence-sorted per stored counts; indices < vertex_count;
    sentinel pattern correct; dict `largest` correct; each emitted patch-table copy differs
    from vanilla in EXACTLY the two u32s of the B005 record.

## Constraint checklist for any new expansion

- [ ] New vertex_count ≤ 65,535 (16-bit indices) and index width matches original.
- [ ] Vertices sorted by influence count; c1..c4 sum == stored vertex_count.
- [ ] ≤ 2 weights/vertex, float32-exact sum 1.0 (3–4 influence support exists in vanilla
      format but is UNTESTED for modified data).
- [ ] Vanilla chunk bytes copied verbatim; new streams strictly appended; 16-aligned.
- [ ] Aux copied byte-for-byte, size unchanged; sentinel = vanilla pattern × new count.
- [ ] Section 54 keeps its EXACT vanilla size; new chunks fit inside the freed vanilla
      B005 regions of the relocated/mirrored slots (nothing else may reference them —
      check section 0 AND the patch table).
- [ ] No vanilla byte of `global.data` overwritten (pure append; entries updated in dict only).
- [ ] Dict `largest` recomputed.
- [ ] global.patch: header byte-identical; 13 table copies rewritten; recompressed table
      fits 516,880; verification confirms the intended record edits only.
- [ ] Face data (B00A/B00B/B00C) and all other meshes untouched.

---

# Failure history (do not repeat)

| Iteration | Change | In-game result | Root cause |
|---|---|---|---|
| 1 | dict + data sections appended, section-0 B005 record patched | Boots; expanded mesh fully exploded; everything else fine | Game never read our section-0 table; loaded old 4.5MB chunk; new B004 offsets read past it |
| 2 | + patched the 13 section-54 sizes in the patch HEADER | Same explosion | Chunk table still came from the patch's compressed copies |
| 3 | + patched the B005 record in all 13 compressed table copies | **WORKS** (target mesh) | — |
| 4 | two-slot 90s_luigi_expanded build: chunks appended at section-54 end, header sizes grown | Replaced meshes fine, but vanilla Global models exploded (slots 478/480/481/484/485/808 observed) | Appended region 0xB1E1EE0..0xBD0320C overlays the 1.4 update's own relocated models (patch-table B005s extend to 0xBA63260); base-section bytes win, updated models read our vertex data. Fixed by placing chunks in the freed vanilla regions and never growing the section. |

Historic corollaries now explained: `swap_slots` (section-0-only redirects) never worked
in-game because the game's table lives in `global.patch`; `clone_slots` worked because it
kept offsets and only changed content.

# Open questions / untested

- 3–4 bone influences on a rebuilt mesh (format supports it; sort+counts logic already handles it).
- Expanding meshes with meaningful aux semantics (face meshes with morphs) — hat's stale-aux
  trick may not transfer; B00A/B00B offsets point into section-52 B00B, not the aux stream.
- Multiple expanded meshes / slots at once: VALIDATED (90s_luigi_expanded, two slots + two
  mirrors) — but only with freed-region placement; watch the 86-byte compression headroom.
- Growing any shared section is now known-UNSAFE without also relocating the 1.4 update's
  extension mappings (B005s to 0xBA63260, section-52 kinds to ~0x1131254) — the update owns
  the address space past every vanilla section end. Freed-region reuse avoids the problem
  entirely; a build that genuinely exceeds the freed space would have to place chunks past
  0xBA63260 AND grow the header sizes, which is untested.
- The patch header's aligned "pool size" fields (e.g. `0x1AFC000` after the section-54 size)
  remain unexplained; with freed-region placement they are never touched.
