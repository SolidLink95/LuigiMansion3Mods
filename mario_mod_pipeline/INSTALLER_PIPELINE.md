# Mario playable installer pipeline

This pipeline creates the compact, self-contained Mario playable installer 

The installer patches a clean Luigi's Mansion 3 `global.dict` and
`global.data`. Mario's meshes, materials, custom textures, and texture redirects
are already embedded in the packaged Global section deltas. Source FBX, PNG,
raw texture, and builder files are not required by the end user.

## Inputs

- Clean archive:
  - `ROMFS_PATH/global.dict`
  - `ROMFS_PATH/global.data`
- Validated Mario build:
  - `_mods/mario_fbx_replacement/romfs/global.dict`
  - `mario_fbx_replacement/romfs/global.data`
- Packager:
  - `res/package_mario_global_sections.py`
- Installer template:
  - `install_mario_playable.py`

The clean archive and Mario build must have the same archive structure and the
same decompressed sizes for the changed sections. The Mario build must already
have passed the validation described in `config/replacement_rules.json`.

## Create the package

Run from the repository root:

```powershell
python ext_projects\LuigiMansion3\mario_mod_pipeline\res\package_mario_global_sections.py
```

The packager processes Global sections `0`, `52`, `54`, and `65`. For each
section it:

1. Reads and decompresses the clean and built versions.
2. Rejects unexpected archive, storage-mode, or section-size changes.
3. Finds changed byte ranges, joining nearby changes when that reduces overhead.
4. Stores the replacement ranges in the `MGP0` delta format.
5. Compresses the delta with zlib level 9 and uses the compressed `MGPZ` form
   when it is smaller.
6. Records original and replacement SHA-256 hashes in `sections.json`.

The generated package contains only:

```text
global_section_0.bin
global_section_52.bin
global_section_54.bin
global_section_65.bin
install_mario_playable.py
README.txt
sections.json
```

Do not distribute `assets/`, `builder.py`, or `util.py`; they are build-time
inputs and unnecessarily increase the installer size.

## Verify the installer

Create a temporary ROMFS directory inside `tmp`, copy the clean archive into
it, and run the generated installer:

```powershell
New-Item -ItemType Directory -Force mario_installer_test
Copy-Item clean\global.dict,clean\global.data `
    -Destination mario_installer_test -Force
python clean_tex\_gb_result\mario_playable\install_mario_playable.py `
    mario_installer_test
```

Compare the installed files with the validated build:

```powershell
Get-FileHash -Algorithm SHA256 `
    mario_installer_test\global.dict, `
    mario_installer_test\global.data, `
    _mods\mario_fbx_replacement\romfs\global.dict, `
    _mods\mario_fbx_replacement\romfs\global.data
```

The test and built `global.dict` hashes must match, and the test and built
`global.data` hashes must match. This proves that the compact deltas reproduce
the complete validated build, including custom textures.

Check the complete distribution size:

```powershell
$package = 'clean_tex\_gb_result\mario_playable'
$size = (Get-ChildItem -File $package | Measure-Object Length -Sum).Sum
"$size bytes; under 10 MB: $($size -lt 10000000)"
```

The current package is 3,274,667 bytes (about 3.275 MB), below the 10,000,000
byte distribution limit.

## End-user installation

The user keeps all seven generated files together and runs:

```powershell
python install_mario_playable.py "X:\path\to\romfs"
```

Before writing anything, the installer validates every source section against
its clean or already-installed SHA-256 hash. It builds and validates all
replacement sections first, then atomically replaces `global.data` and
`global.dict`. Running it again on an already-installed matching archive is
supported.
