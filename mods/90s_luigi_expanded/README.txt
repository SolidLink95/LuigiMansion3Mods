90s Luigi (expanded meshes) for Luigi's Mansion 3 (title 0100DCA0064A6000)
==========================================================================

Replaces Story Mode Luigi's hat and body with a high-poly 90s-style costume,
including cutscene/mirror slots, custom textures and normal maps. Uses the
expanded-archive technique: the replacement meshes exceed the vanilla vertex
allocations, so global.dict, global.data AND global.patch are all rebuilt.

Requirements: Python 3.9+ and a CLEAN ROMFS dump of the game containing
global.dict, global.data and global.patch.

Install:

    python install.py <path_to_clean_romfs>

The dump is only read, never modified. The installer verifies every input
against SHA-256 hashes of the supported clean archive, then writes the three
rebuilt files into a "romfs" folder next to install.py (~1 GB free space
needed; the rebuild takes a few minutes).

Then copy this whole mod folder into the emulator's load directory, e.g. for
yuzu:

    %APPDATA%\yuzu\load\0100DCA0064A6000\90s_luigi_expanded\romfs\global.*

If the installer reports a hash mismatch, the dump is either already modded
or from an unsupported game version - re-dump and try again.
