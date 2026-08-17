# Mario replacement pipeline snapshot

This folder contains the current code and configuration needed to reproduce the
Luigi's Mansion 3 Mario replacement workflow. It is a snapshot of the proven
pipeline under `tmp/ml3`; the live scripts retain their original repository-root
path assumptions.

## Code

- `res/build_mario_fbx_mod.py` — builds and validates the Global replacement.
- `res/extract_fbx_replacement.py` — extracts the four FBX submeshes through Blender.
- `res/export_clean_slot_fbx.py` and `res/lm3_full_fbx_blender.py` — export and validate built slots.
- `res/package_mario_global_sections.py` — creates compact SHA-256-verified installers.
- `helpers/lm3_slot_swap.py` and `helpers/lm3_import_mummigi.py` — archive and texture helpers required by the generator.
- `helpers/extract_lm3_slot_textures.py` — extracts hash-named inspection textures.
- `installer/install_mario_playable.py` — current installer implementation.
- `INSTALLER_PIPELINE.md` — reproducible compact-installer packaging and
  verification procedure.

## Configuration

- `config/replacement_rules.json` is the authoritative, self-contained build setup.
- `config/textures.json` contains Global texture redirects.
- `lm3_costume_slots.md` documents the established model-replacement rules.

## External inputs

The scripts expect the live source assets documented in `replacement_rules.json`,
including `Mario_to_luigi6.fbx`, hash-named PNG textures, and a clean LM3 Global
archive. Blender 4.0, Pillow, and `astcenc-avx2.exe` are required.
