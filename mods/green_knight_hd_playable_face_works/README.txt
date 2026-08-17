Green Knight HD Playable (face animations preserved)

Requirements:
- Python 3
- A clean Luigi's Mansion 3 ROMFS containing global.dict and global.data

Install:
    python install.py "X:\path\to\romfs"

The installer validates the supported clean archive before changing it, builds
and checks every replacement section first, then atomically updates global.data
and global.dict. Running it again on an already matching installation is safe.

Keep install.py, sections.json, and all global_section_*.bin files together.
