# Luigi's Mansion 3 costume locations

The ScareScraper Luigi costumes are stored in:

```text
romfs/Scarescraper/Persistent.dict
romfs/Scarescraper/Persistent.data
```

Each DLC costume occupies four consecutive model slots. The four records are
the model variants needed to replace the four global Story Mode Luigi records.

The original costume-to-slot ordering below was speculative and was disproved
by in-game tests.  Keep confirmed observations separate from identities that
still need testing.

| Persistent slots | Detail variant | Costume identity | Evidence |
|---:|---|---|---|
| 38–39 | High detail (13 meshes, 13,852 vertices) | The Green Knight (inferred) | Paired with the confirmed retro records 40–41 by layout and adjacent ordering. |
| 40–41 | Retro/low detail (2 meshes, 2,741 vertices) | **The Green Knight (confirmed)** | Loaded in Story Mode as the low-poly armored knight. |
| 42–43 | High detail (13 meshes, 13,551 vertices) | The Amazing Luigi (inferred) | Paired with the confirmed retro records 44–45 by layout and adjacent ordering. |
| 44–45 | Retro/low detail (2 meshes, 2,567 vertices) | **The Amazing Luigi (confirmed)** | Loaded in Story Mode with the green top hat and magician outfit. |
| 46–47 | High detail (13 meshes, 12,267 vertices) | **Paleontoluigist (confirmed)** | Slot 46 archive SHA-256 matches the tested `Paleontoluigist_playable` mod. |
| 48–49 | Retro/low detail (2 meshes, 2,495 vertices) | Paleontoluigist (inferred) | Low-detail pair associated with confirmed slots 46–47. |
| 50 | High detail (13 meshes, 12,489 vertices) | **Disco Luigi / Groovigi (confirmed)** | Loaded in Story Mode with the disco outfit; the original-hash texture test produced partially broken textures. |
| 51 | High detail (13 meshes, 12,489 vertices) | Disco Luigi / Groovigi (inferred) | Paired high-detail record adjacent to confirmed slot 50. |
| 52–53 | Retro/low detail (2 meshes, 2,302 vertices) | Disco Luigi / Groovigi (inferred) | Low-detail pair associated with confirmed high-detail slots 50–51. |
| 54–55 | High detail (13 meshes, 12,255 vertices) | **Mummigi (confirmed)** | Slot 54 archive SHA-256 matches the tested `Mummigi_playable` mod. |
| 56–57 | Retro/low detail (2 meshes, 2,297 vertices) | Mummigi (inferred) | Low-detail pair associated with confirmed slots 54–55. |
| 58–59 | High detail (13 meshes, 12,757 vertices) | Unknown | Does not fit all four Global allocations without relocating `B00A`. |
| 60–61 | Retro/low detail (2 meshes, 2,741 vertices) | Unknown | Not yet identified in-game. |

Known theme associations remain useful clues, but are not yet tied to exact
model slots: `Sand.*` (Mummigi), `Castle.*` (The Green Knight),
`Nightclub.*` (Groovigi), `Maze.*` (The Amazing Luigi), `Museum.*`
(Paleontoluigist), and `Seafood.*` (Cap'n Weegee).

The ordinary ScareScraper outfit records precede the DLC groups. Its main
high-detail pair begins at Persistent slots 24–25, with associated variants in
the following records before slot 38.

## Story Mode replacement

The four global Story Mode Luigi records are:

```text
global slots 27, 28, 29, 30
```

Each apparent costume group contains a high-detail pair followed by a
retro/low-detail pair.  The earlier assumption that slots 38–41 were Mummigi
was incorrect.  Once a group is positively identified, its four records should
normally map to the Global slots in order:

| Persistent source | Global target |
|---:|---:|
| group slot + 0 | 27 |
| group slot + 1 | 28 |
| group slot + 2 | 29 |
| group slot + 3 | 30 |

## Mod completion status

Status reconciled on 2026-08-15 from `tmp/ml3/_FINISHED.json`,
`tmp/ml3/_mods/_FINISHED.json`, and the generated mod folders.

| Costume | HD | N64/retro | What is still missing |
|---|---|---|---|
| The Green Knight | **Done** (`green_knight_hd`): confirmed gameplay-safe using slot 38 with skeleton group 30 and empty `B00A`/`B00B` deformation payloads | Done (`green_knight_retro`) | Nothing |
| The Amazing Luigi | Built (`amazing_luigi_hd`) | Done (`amazing_luigi_retro`) | Nothing currently unbuilt; HD still needs in-game confirmation if it has not been tested |
| Paleontoluigist | Done (`paleontoluigist_hd`) | **Missing** | `paleontoluigist_retro` |
| Groovigi | Done (`groovigi_hd`) | **Missing** | `groovigi_retro` |
| Mummigi | Done (`mummigi_hd`) | **Missing** | `mummigi_retro` |
| Cap'n Weegee | **Missing** | Done (`capn_weegee_retro`) | `capn_weegee_hd` |

Therefore, four variants have not yet been built: Paleontoluigist retro,
Groovigi retro, Mummigi retro, and Cap'n Weegee HD.

The costume models are not self-contained in `global.data`. Their geometry and
materials come from Persistent entries 52, 53, and 54. Their texture headers
and image data come from Persistent entries 63 and 65. A working Story Mode
conversion must import the referenced textures into `global.data` and add the
corresponding `B501`/`B502` chunk-table records; redirecting only model offsets
produces missing or incorrect materials.

The archive builders under test are:

```text
tmp/ml3/lm3_import_mummigi.py
```

`lm3_import_mummigi.py` contains the earlier expanding archive experiment.
`lm3_import_mummigi_owned.py` performs the working fixed-layout, owned-range
replacement.  Its source slot is currently changed between tests and should
not be assumed to be Mummigi based on the filename.

## Mario replacement rules

These are the authoritative rules for regenerating the Mario replacement mod.
They supersede the earlier one-submesh and three-weight experiments.

### Inputs and targets

- Use `tmp/ml3/Mario LM3/Mario_to_luigi3.fbx` as the authoritative replacement
  model. Re-extract its mesh data whenever that FBX changes.
- Read `tmp/ml3/Mario LM3/tex/replacement_rules.json` and enforce its explicit
  per-slot mesh-index mappings during every regeneration.
- Extract the Blender objects named `submesh_7` and `submesh_14`; replace both
  objects and no other geometry.
- Apply the replacement to Global Story Mode slots 27, 28, 29, and 30.
- The target hashes for `submesh_7` are `40243E90` in slots 27–28 and
  `E4882C37` in slots 29–30.
- The target hashes for `submesh_14` are `A92A6B5E` in slots 27–28 and
  `CE77ED18` in slots 29–30.
- Preserve each target slot's skeleton and translate Blender bone IDs through
  that slot's `B103` bone-hash table. Slots 27–28 use skeleton group 27;
  slots 29–30 use skeleton group 28.
- Preserve positions, normals, topology, UV maps, and skin weights from the
  authoritative Blender objects. Store the game UV V coordinate as
  `1.0 - Blender V`.
- Preserve unknown per-vertex fields by copying the nearest original target
  vertex record before overwriting position, normal, and UV fields.

### Skin-weight rule

For every replacement vertex:

1. For each replacement vertex, find the nearest original target vertex and
   copy its target-native bone influences. Do not use the FBX bone assignments
   directly for the generated game mesh.
2. Sort the copied influences by descending weight and retain at most the two strongest.
3. Redistribute all discarded influence proportionally between the retained
   influences by normalizing the retained weights.
4. Store one influence as `1.0`; for two influences, store the secondary as
   the exact complement of the dominant.
5. The final vertex must have no more than two nonzero bone weights, and the
   decoded weights must sum to exactly `1.0`.

Do not assign every vertex rigidly to its dominant bone. Do not retain three
or more influences.

For temporary troubleshooting, `temporary_rigid_bone` assigns every imported
vertex exclusively to that skeleton bone. `fallback_rigid_bone` is used only
when a normal source vertex has missing, non-finite, or unusable weights. The
current diagnostic/fallback bone is `bone_107` with weight `1.0`; all other IDs
and weights must be binary zero. Geometry that exceeds the original vertex
count still aborts because changing weights cannot make oversized data fit.

### Fixed-layout geometry rule

- Do not grow the Global archive entries.
- Abort the entire import if any replacement mesh has more vertices than its
  original target mesh. Do not relocate, simplify, or partially import it to
  work around vertex-count growth.
- Release only the existing buffer ranges owned by the two target submeshes,
  then repack their new index, skin, and vertex payloads into free ranges of
  the same costume slot's `B005` buffer.
- Do not overwrite or relocate untouched submeshes.
- Use 16-bit indices and validate that every index is below the new vertex
  count.
- After writing, update the `B003` index offset/count/vertex count and the
  `B004` skin/vertex offsets for each replacement mesh.

### Texture and material rules

- Use custom texture assets from `tmp/ml3/Mario LM3/tex`.
- Read `tmp/ml3/Mario LM3/tex/textures.json` before replacing PNG data. Each
  entry maps a material's source texture hash to an existing Global texture
  hash.
- If both the JSON key and value exist in the Global texture dictionary,
  redirect every matching material reference to the value hash and skip PNG
  replacement for that source texture.
- If either the key or value is absent from the Global dictionary, retain the
  material hash and perform the regular hash-named PNG replacement.
- PNG filenames are texture hashes in uppercase hexadecimal form.
- Diffuse textures use the existing working encoding path. Normal maps must
  use their correct texture format; do not encode BC5 normal data as diffuse
  ASTC data. Existing valid Global normal-map redirects are preferred when
  configured in `textures.json`.
- New encoded texture data must never exceed the original texture allocation.
  If necessary, reduce PNG dimensions or use stronger compression, accepting
  some quality loss. Never enlarge the archive to fit a texture.

### Generation and validation

The generator is:

```text
tmp/ml3/res/build_mario_fbx_mod.py
```

It writes the mod to:

```text
tmp/ml3/_mods/mario_fbx_replacement/romfs
```

Before considering a build complete, validate all eight replacement meshes,
their index bounds, the two-influence/exact-sum weight invariant, texture
allocation sizes, material redirects, and decompressed archive round trips.
