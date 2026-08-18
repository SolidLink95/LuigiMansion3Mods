Mario playable compact installer
================================

Requirements:
- Python 3
- A clean Luigi's Mansion 3 ROMFS containing global.dict and global.data from
  the supported game version

Run:
    python install.py "X:\path\to\clean\romfs"

The installer treats the supplied clean ROMFS as read-only. It validates the
source archive by SHA-256, applies the packaged compact deltas, validates the
finished data, and writes the mod to:

    mario_playable\romfs

under the directory containing install.py.

Keep these files together:
- install.py
- sections.json
- global_section_0.bin
- global_section_52.bin
- global_section_54.bin
- global_section_65.bin

The legacy assets folder, builder.py, and util.py are not used by this compact
installer.
