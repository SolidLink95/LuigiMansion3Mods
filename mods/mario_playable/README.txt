Mario playable compact installer
================================

Requirements:
- Python 3
- A clean Luigi's Mansion 3 ROMFS containing global.dict and global.data from
  the supported game version

Run:
    python install_mario_playable.py "X:\path\to\romfs"

The installer validates the clean archive by SHA-256 before changing anything,
applies the packaged compact deltas to global.dict and global.data, and validates
the finished files against the replacement hashes in sections.json.

Keep these files together:
- install_mario_playable.py
- sections.json
- global_dict.delta.bin
- global_data.delta.bin

The legacy assets folder, builder.py, and util.py are not used by this compact
installer.
