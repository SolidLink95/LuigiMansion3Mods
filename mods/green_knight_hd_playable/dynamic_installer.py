from __future__ import annotations

import hashlib
import sys
from pathlib import Path


EXPECTED_HASHES = {
    "global.dict": "f678a64cc7628f72067131299e6c5f1b0d3f698be39bddaa39036ece56680b78",
    "global.data": "3f70b4b7afff5d67ae052e64ef70ff3c8a07a8e1d3e72f872db8d2b0c42276f0",
    "global.patch": "5266c24437b51a627e42b3a7ca176160eb234250f0bec2cd8ecd716de0b3edc1",
    "Scarescraper/Persistent.dict": "cd70f5c83ac1d396d996dd8a028d7a7f55725af204c811e88b885f561c5ed4f6",
    "Scarescraper/Persistent.data": "05c7aa2add12a99229ebcbea8b61d4718fa16e9a9c001411805c526f8e3034e3",
}


def select_clean_romfs(script_name: str) -> Path:
    if len(sys.argv) > 2:
        raise ValueError(f"usage: python {script_name} [clean_romfs_path]")
    if len(sys.argv) == 2:
        raw_path = sys.argv[1]
    else:
        try:
            raw_path = input(
                "Enter the path to a clean Luigi's Mansion 3 romfs folder: "
            ).strip()
        except (EOFError, KeyboardInterrupt) as error:
            raise ValueError("no ROMFS path was provided") from error
    if not raw_path:
        raise ValueError("no ROMFS path was provided")
    romfs = Path(raw_path.strip('"')).expanduser()
    if not romfs.is_dir():
        raise ValueError(f"ROMFS path is not a directory: {romfs}")
    return romfs


def validate_clean_romfs(romfs: Path) -> None:
    for relative, expected_hash in EXPECTED_HASHES.items():
        path = romfs / Path(relative)
        if not path.is_file():
            raise ValueError(f"clean ROMFS file is missing: {path}")
        if hashlib.sha256(path.read_bytes()).hexdigest() != expected_hash:
            raise ValueError(f"{path} does not match the supported clean LM3 archive")


def run(
    mod_name: str,
    target_source_pairs,
    sources,
    texture_mapping=None,
    skeleton_pairs=(),
    empty_kinds=frozenset(),
    keep_target_kinds=frozenset({0xB008, 0xB009}),
) -> int:
    try:
        romfs = select_clean_romfs(Path(sys.argv[0]).name)
        validate_clean_romfs(romfs)

        import lm3_costume_mod_builder as builder

        package_dir = Path(__file__).resolve().parent
        builder.GLOBAL = romfs / "global.dict"
        builder.PERSISTENT = romfs / "Scarescraper" / "Persistent.dict"
        builder.OUTPUT = package_dir / mod_name / "romfs"
        builder.TARGET_SOURCE_PAIRS = tuple(target_source_pairs)
        builder.TARGETS = (27, 28, 29, 30)
        builder.SOURCES = tuple(sources)
        builder.SHARE_OVERSIZED_BY_KIND = False
        builder.KEEP_TARGET_KINDS = set(keep_target_kinds)
        builder.REDIRECT_COMPLETE_MODEL_PAIRS = {}
        builder.SKELETON_GROUP_PAIRS = tuple(skeleton_pairs)
        builder.EMPTY_SOURCE_KINDS = set(empty_kinds)
        builder.TEXTURE_MAPPING_OVERRIDE = dict(texture_mapping or {})
        builder.main()
    except (KeyError, OSError, ValueError, AssertionError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    print(f"created {mod_name} in {(package_dir / mod_name / 'romfs').resolve()}")
    return 0
