from __future__ import annotations

import hashlib
import sys
from pathlib import Path


PACKAGE_DIR = Path(__file__).resolve().parent
GLOBAL_HASHES = {'global.dict': 'f678a64cc7628f72067131299e6c5f1b0d3f698be39bddaa39036ece56680b78', 'global.data': '3f70b4b7afff5d67ae052e64ef70ff3c8a07a8e1d3e72f872db8d2b0c42276f0', 'global.patch': '5266c24437b51a627e42b3a7ca176160eb234250f0bec2cd8ecd716de0b3edc1'}
PERSISTENT_HASHES = {'Persistent.dict': 'cd70f5c83ac1d396d996dd8a028d7a7f55725af204c811e88b885f561c5ed4f6', 'Persistent.data': '05c7aa2add12a99229ebcbea8b61d4718fa16e9a9c001411805c526f8e3034e3'}


def fail(message: str) -> int:
    print(f"error: {message}", file=sys.stderr)
    return 2


def select_clean_romfs() -> Path:
    if len(sys.argv) > 2:
        raise ValueError(f"usage: python {Path(sys.argv[0]).name} [clean_romfs_path]")
    if len(sys.argv) == 2:
        raw_path = sys.argv[1]
    else:
        try:
            raw_path = input("Enter the path to a clean Luigi's Mansion 3 romfs folder: ").strip()
        except (EOFError, KeyboardInterrupt) as error:
            raise ValueError("no ROMFS path was provided") from error
    if not raw_path:
        raise ValueError("no ROMFS path was provided")
    romfs = Path(raw_path.strip('"')).expanduser()
    if not romfs.is_dir():
        raise ValueError(f"ROMFS path is not a directory: {romfs}")
    return romfs


def validate_file(path: Path, expected_hash: str) -> None:
    if not path.is_file():
        raise ValueError(f"clean ROMFS file is missing: {path}")
    actual_hash = hashlib.sha256(path.read_bytes()).hexdigest()
    if actual_hash != expected_hash:
        raise ValueError(f"{path} does not match the supported clean LM3 archive")


def validate_global(romfs: Path) -> None:
    for relative, expected_hash in GLOBAL_HASHES.items():
        validate_file(romfs / relative, expected_hash)


def validate_persistent(romfs: Path) -> None:
    for relative, expected_hash in PERSISTENT_HASHES.items():
        validate_file(romfs / "Scarescraper" / relative, expected_hash)


def main() -> int:
    try:
        romfs = select_clean_romfs()
        validate_global(romfs)
        validate_persistent(romfs)

        import lm3_costume_mod_builder as builder

        output = PACKAGE_DIR / "N64_luigi_playable" / "romfs"
        try:
            output.resolve().relative_to(romfs.resolve())
        except ValueError:
            pass
        else:
            raise ValueError("output directory must be outside the supplied ROMFS path")
        builder.GLOBAL = romfs / "global.dict"
        builder.PERSISTENT = romfs / "Scarescraper" / "Persistent.dict"
        builder.OUTPUT = output
        builder.TARGET_SOURCE_PAIRS = tuple((target, 31) for target in (27, 28, 29, 30))
        builder.TARGETS = (27, 28, 29, 30)
        builder.SOURCES = (31,)
        builder.main()
    except (KeyError, OSError, ValueError, AssertionError) as error:
        return fail(str(error))
    print("created N64_luigi_playable in " + str((PACKAGE_DIR / "N64_luigi_playable" / "romfs").resolve()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
