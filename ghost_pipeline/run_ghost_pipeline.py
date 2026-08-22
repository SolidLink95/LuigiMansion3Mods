r"""Build, package, and byte-verify the complete Ghost Luigi mod.

Usage from any working directory:

    python W:\coding\LuigiMansion3Mods\ghost_pipeline\run_ghost_pipeline.py

The script intentionally accepts no arguments. Machine-specific Blender and
clean-ROMFS paths are read from the repository's read-only ``local.json``.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path


HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
LOCAL = ROOT / "local.json"
BUILDER = ROOT / "mario_mod_pipeline" / "ghost_luigi_pipeline" / "build_ghost_luigi.py"
PACKAGER = ROOT / "mario_mod_pipeline" / "res" / "package_expanded_global.py"
BUILT_ROMFS = ROOT / "tmp" / "_mods" / "ghost_luigi" / "romfs"
PACKAGE = ROOT / "mods" / "ghost_luigi_compact_installer"
PACKAGE_ZIP = ROOT / "mods" / "ghost_luigi_compact_installer.zip"
TEST_PACKAGE = ROOT / "tmp" / "ghost_luigi" / "compact_installer_pipeline_test"
README_TEMPLATE = HERE / "README.txt"
ARCHIVE_FILES = ("global.dict", "global.data", "global.patch")
FIXED_ZIP_TIME = (2026, 1, 1, 0, 0, 0)


def run(command: list[str]) -> None:
    print("RUN", subprocess.list2cmdline(command), flush=True)
    subprocess.run(command, cwd=ROOT, check=True)


def digest(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            hasher.update(block)
    return hasher.hexdigest()


def deterministic_zip(source: Path, destination: Path) -> None:
    """Write stable ZIP bytes independent of filesystem modification times."""
    with zipfile.ZipFile(
        destination, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9
    ) as archive:
        for path in sorted(source.iterdir(), key=lambda item: item.name.lower()):
            if not path.is_file():
                continue
            info = zipfile.ZipInfo(path.name, FIXED_ZIP_TIME)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.create_system = 3
            info.external_attr = 0o100644 << 16
            archive.writestr(info, path.read_bytes(), compresslevel=9)


def main() -> None:
    if len(sys.argv) != 1:
        raise SystemExit("run_ghost_pipeline.py accepts no arguments")

    local = json.loads(LOCAL.read_text(encoding="utf-8"))
    clean_dict = Path(local["romfs"]) / "global.dict"
    for path in (
        clean_dict,
        clean_dict.with_suffix(".data"),
        clean_dict.with_suffix(".patch"),
        BUILDER,
        PACKAGER,
        README_TEMPLATE,
    ):
        if not path.is_file():
            raise FileNotFoundError(path)

    run([sys.executable, str(BUILDER)])
    run([
        sys.executable,
        str(PACKAGER),
        "--original",
        str(clean_dict),
        "--built",
        str(BUILT_ROMFS / "global.dict"),
        "--output",
        str(PACKAGE),
        "--mod-name",
        "ghost_luigi",
    ])
    shutil.copyfile(README_TEMPLATE, PACKAGE / "README.txt")

    TEST_PACKAGE.mkdir(parents=True, exist_ok=True)
    for path in PACKAGE.iterdir():
        if path.is_file():
            shutil.copyfile(path, TEST_PACKAGE / path.name)
    run([sys.executable, str(TEST_PACKAGE / "install.py"), str(Path(local["romfs"]))])

    for name in ARCHIVE_FILES:
        expected = digest(BUILT_ROMFS / name)
        actual = digest(TEST_PACKAGE / "romfs" / name)
        if actual != expected:
            raise AssertionError(
                f"compact installer mismatch for {name}: {actual} != {expected}"
            )
        print(f"BYTE_MATCH {name} {actual}")

    deterministic_zip(PACKAGE, PACKAGE_ZIP)
    package_size = sum(path.stat().st_size for path in PACKAGE.iterdir() if path.is_file())
    zip_size = PACKAGE_ZIP.stat().st_size
    if package_size >= 50_000_000 or zip_size >= 50_000_000:
        raise AssertionError(
            f"compact installer exceeds 50 MB: folder={package_size}, zip={zip_size}"
        )

    print(f"PACKAGE {PACKAGE} ({package_size} bytes)")
    print(f"ZIP {PACKAGE_ZIP} ({zip_size} bytes, sha256={digest(PACKAGE_ZIP)})")
    print("Ghost Luigi pipeline completed with byte-identical installer output")


if __name__ == "__main__":
    main()
