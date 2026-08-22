# Repository Guidelines

## Project Structure & Module Organization

This repository automates Luigi's Mansion 3 model and texture replacements. `mario_mod_pipeline/` contains the reproducible build pipeline: `run_pipeline.py` orchestrates builds, `res/` holds builders and Blender helpers, `helpers/` contains archive utilities, and `config/` stores JSON replacement rules. Per-mod installers and packaged assets live under `mods/<mod_name>/`. Treat `misc/` as experimental or historical scripts and `tmp/` as generated working data, not source. The root `README.md` is only an overview; pipeline-specific details belong in `mario_mod_pipeline/*.md`.

## Build, Test, and Development Commands

Run commands from the repository root with Python 3:

```powershell
python mario_mod_pipeline/run_pipeline.py --help
python mario_mod_pipeline/run_pipeline.py --params mario_mod_pipeline/params_mario.json
python mods/mario_playable/install.py "X:\path\to\romfs"
```

The first command lists pipeline overrides; the second runs a configured replacement build; the third applies a packaged mod to a ROMFS copy. Builds require the external inputs named in the selected JSON configuration, plus Blender 4.0, Pillow, and `astcenc-avx2.exe`. Paths are read from root `local.json`; this file is machine-specific and strictly read-only—never edit, format, or regenerate it.

## Agent File Access

Agents may copy files to %userprofile%\AppData\Roaming\yuzu\load\0100DCA0064A6000 freely. Agents may read files and make code-related changes within `W:\coding\LuigiMansion3Mods`. Paths declared in `W:\coding\LuigiMansion3Mods\local.json` are also authorized for read-only access: agents may inspect files at those paths and execute referenced programs or commands, but must never write to, modify, delete, move, or create files there. Create every temporary file and temporary directory under `W:\coding\LuigiMansion3Mods\tmp`; do not use system or external temporary locations. Keep `local.json` itself strictly read-only—never edit, format, replace, or regenerate it.

## Coding Style & Naming Conventions

Use four-space indentation and standard Python conventions: `snake_case` for functions and files, `UPPER_CASE` for module constants, and `PascalCase` for classes. Prefer `pathlib.Path`, explicit validation, descriptive exceptions, and type hints for new public helpers. Keep configuration keys consistent with existing uppercase pipeline keys. No formatter or linter is configured, so match nearby code and keep imports grouped as standard library, third-party, then local.

## Testing Guidelines

There is no automated test suite. Validate changes with the smallest relevant pipeline or installer workflow. Installer changes must be tested against a disposable clean ROMFS copy, then compare SHA-256 hashes with the validated build as documented in `mario_mod_pipeline/INSTALLER_PIPELINE.md`. Never test by overwriting the source ROMFS dump. Record the configuration and output checked in the pull request.

## Commit & Pull Request Guidelines

History uses short, imperative summaries such as `prevent install to romfs dump` and `fix normal maps decoding`. Keep each commit focused and avoid committing generated `tmp/` data or proprietary game assets. Pull requests should explain the affected mod or slot, list verification commands, link relevant issues, and include screenshots for visible model or texture changes. Call out required external assets or configuration assumptions.
