# Ghost Luigi pipeline

`build_ghost_luigi.py` builds the Ghost Luigi material, UV, transparency, glow,
and custom-texture replacement from a clean Global archive.

Inputs outside this folder are machine-local/generated and remain under
`tmp/ghost_luigi`:

- `ghost_info.fbx` supplies UV maps only.
- `tex/*.png` supplies the custom texture pixels.

`textures.json` maps each custom/source PNG hash to the original slot-13
texture hash it replaces in the copied ghost materials. Only listed hashes are
redirected; every other material texture reference remains unchanged.

Run from the repository root:

```powershell
python mario_mod_pipeline/ghost_luigi_pipeline/build_ghost_luigi.py
```

The expanded Global build is written to
`tmp/_mods/ghost_luigi/romfs` and is packaged separately with
`mario_mod_pipeline/res/package_expanded_global.py`.
