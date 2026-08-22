Ghost Luigi compact installer
=============================

This package installs the Ghost Luigi mod from a clean Luigi's Mansion 3
Global archive. It includes the corrected UV maps, custom textures, reduced
glow, and confirmed 0.35 material opacity.

Requirements
------------

- Python 3.9 or newer
- A clean game ROMFS containing global.dict, global.data, and global.patch
- Approximately 1 GB of temporary free disk space while rebuilding

Install
-------

Keep every file in this folder together, then run:

    python install.py "X:\path\to\clean\romfs"

The clean ROMFS is validated and read only. The installer writes the completed
LayeredFS files under this package's new `romfs` folder. Copy the package folder
containing that `romfs` folder to:

    %APPDATA%\yuzu\load\0100DCA0064A6000\ghost_luigi

Do not enable this alongside another mod that replaces the same global.* files.
