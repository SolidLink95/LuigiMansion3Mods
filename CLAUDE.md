# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

Tooling that replaces Luigi's models, materials, and textures in *Luigi's Mansion 3* (Switch, title `0100DCA0064A6000`) by patching the game's `global.dict`/`global.data` archive pair in place. There is no build system, package manifest, linter, or test suite — everything is standalone Python 3 run by hand.

External dependencies (not vendored): **Blender 4.0** (headless FBX extraction), **Pillow**, **`astcenc-avx2.exe`** on PATH.

## Required local setup

`local.json` at the repo root is gitignored and **must be created before `run_pipeline.py` will import**:

```json
{ "blender": "C:/Program Files/Blender Foundation/Blender 4.0/blender.exe",
  "romfs":   "X:/path/to/clean/LM3/romfs" }
```

`tmp/`, `res/`, and any `romfs/` are also gitignored. The authoritative FBX and PNG source assets live under `../tmp/<costume>/` relative to `mario_mod_pipeline` and are **not in this repository** — a checkout alone cannot rebuild a mod.

## Commands

All pipeline commands run from `mario_mod_pipeline/`. `run_pipeline.py` is the only entry point; it drives Blender, the builder, the packager, verification, and Yuzu deployment.

```powershell
# Full rebuild of a costume (extract FBX -> build archive)
python run_pipeline.py --params capnweege.json --skip-package --skip-deploy

# Reuse already-extracted mesh JSON (skip Blender)
python run_pipeline.py --params capnweege.json --skip-extract True --skip-package --skip-deploy

# Package the compact installer from an existing build
python run_pipeline.py --params capnweege.json --skip-extract True --skip-build True --skip-deploy `
  --sections 52 54 65 `
  --package-output "..\mods\capnweege_hd_playable_face_works" `
  --mod-name capnweege_hd_playable_face_works `
  --installer-template ".\installer\install_mario_playable.py"

# End-user installation of a packaged mod
python install.py "X:\path\to\clean\romfs"
```

Parameter files select the costume: `params_mario.json`, `green_knight.json`, `capnweege.json`, `amazing.json`. Each names its `config/<name>/` rules directory, source FBX, and output mod name.

Gotchas in the CLI:
- `--skip-extract` and `--skip-build` use `type=bool`, so **any** value is truthy — `--skip-extract False` skips extraction. Omit the flag entirely to run the step.
- `--params` is read directly from `sys.argv` at module import, before argparse, because module-level constants derive from it. Do not convert it to a parser-only option.
- Always omit `--skip-extract` after editing an FBX; a build on stale mesh JSON succeeds while silently ignoring the new model.

## Architecture

### Archive model

`global.dict` is a table of 16-byte file entries (magic `0xA9F32458`); `global.data` holds the zlib-compressed payloads. `helpers/lm3_slot_swap.py` is the low-level reader: `read_archive` / `decompress_entry` / `replace_entry` / `parse_subentries` / `group_models`.

Section 0 is the chunk sub-entry table, which `group_models` splits into per-slot model records (chunks `0xB002`–`0xB00B`, `0xB103` skeleton, `0xB501`/`0xB502` texture header/data). Section indexes that matter: **0** = table, **52** = model/material data, **54** = vertex & index buffers, **63** = texture headers, **65** = texture image data.

Story Mode Luigi occupies Global slots **27, 28, 29, 30**. ScareScraper DLC costumes live in `romfs/Scarescraper/Persistent.{dict,data}`; see `mario_mod_pipeline/lm3_costume_slots.md` for the slot-to-costume map and which identifications are confirmed vs. inferred.

### Fixed-layout invariant

`replace_entry` and the builder abort rather than grow any allocation. Replacement meshes must fit within the original mesh's vertex count and the slot's `B005` free ranges; encoded textures must fit the original texture allocation. Never work around a size overflow by relocating or enlarging entries.

### Two generations of mod, both shipped under `mods/`

1. **Compact section-delta mods** (`mario_playable`, `*_hd_playable_face_works`) — the current approach. `res/package_mario_global_sections.py` diffs a validated build against the clean archive per decompressed section, emits `MGP0` (or zlib-wrapped `MGPZ`) range-replacement deltas plus a SHA-256 `sections.json`, and copies the installer template. Ships as `install.py` + `sections.json` + `global_section_*.bin` + `README.txt` and needs no source assets.
2. **Dynamic slot-clone mods** (`N64_*`, `Mummigi_playable`, `Paleontoluigist_playable`, `groovigi_playable`, `gooigi_playable`) — earlier approach. Ships `lm3_costume_mod_builder.py` + `lm3_slot_swap.py` + `util.py` and rebuilds the archive on the user's machine by cloning a Persistent costume slot over the Story slots. `dynamic_installer.run(...)` is the shared driver for the newer ones.

Note `mario_mod_pipeline/installer/install_mario_playable.py` (the template) patches the supplied ROMFS **in place**, while the copies shipped in `mods/*/` were modified to write into a sibling `<mod_name>/romfs` directory and refuse an output path inside the supplied ROMFS. Keep that divergence in mind when regenerating an installer — the packaged `install_<mod_name>.py` must be renamed to `install.py` and re-hardened.

### Code convention: set module globals, then call `main()`

Both builders (`res/build_mario_fbx_mod.py`, `lm3_costume_mod_builder.py`) are configured by assigning module-level constants from the caller rather than by arguments. `run_pipeline.py` loads them via `importlib.util.spec_from_file_location`, overwrites `CLEAN`, `OUTPUT`, `TARGETS`, `SKELETON_GROUP_FOR_SLOT`, etc., then calls `builder.main()`. The mod installers do the same with `builder.GLOBAL`, `builder.TARGET_SOURCE_PAIRS`, `builder.KEEP_TARGET_KINDS`, …

### Import-path hazard

`res/build_mario_fbx_mod.py` computes `ROOT = HERE.parents[2]` and pushes `src-tauri/misc` and `tmp/ml3` onto `sys.path` — leftovers from the original project location. It resolves in practice only because `run_pipeline.py` inserts `mario_mod_pipeline/helpers/` at the front of `sys.path` first. Run these scripts through `run_pipeline.py`, not directly.

### `misc/`

Historical snapshots of earlier builders and one-off per-costume driver scripts, kept for reference. They retain original repository-root path assumptions and are not runnable in place. `mario_mod_pipeline/` is the maintained copy of everything that matters.

## Rules the code depends on (documented in detail, do not re-derive)

- **`mario_mod_pipeline/ARMOR_ONLY_COSTUME_PIPELINE.md`** — the validated armor-only workflow: the `body`/`hat`/`boot`/`hand` FBX contract, per-slot mesh target indices, skin-weight rules, exact Persistent-material transfer, texture mappings, the two-stage installer canonicalization, and a regression checklist. Read this before touching a costume build.
- **`mario_mod_pipeline/lm3_costume_slots.md`** — costume slot map plus the authoritative Mario replacement rules (mesh targets, skin-weight rule, fixed-layout rule, texture/material rules).
- **`mario_mod_pipeline/INSTALLER_PIPELINE.md`** — compact-installer packaging and hash verification procedure.

Two invariants worth repeating because they caused shipped regressions:

- Global textures are **shared across slots**. Overwriting hash `90D71FE0` makes the untouched face shiny even though its material was never edited. Every armor-only config must list it in `preserve_texture_hashes`.
- Never re-encode a Persistent-imported material texture from PNG; copy the header and image bytes byte-for-byte or the metallic/normal data is destroyed.

Skinned vertex records are `0x30` bytes with a parallel `0x14`-byte skin record; the `B004` layout is `u32 skin_offset` then `u32 vertex_offset`. Reversing those two fields produces violently exploded geometry.

## Verification expectations

There are no automated tests. A change is "done" when the two-stage hash verification in `ARMOR_ONLY_COSTUME_PIPELINE.md` passes: install the package into a temporary clean ROMFS under `tmp/`, confirm the resulting `global.dict`/`global.data` SHA-256 hashes match the canonical full build, and confirm the installer stays under the size limit. `run_pipeline.py --verify` automates the section-delta half of this. Final validation is in-game.
