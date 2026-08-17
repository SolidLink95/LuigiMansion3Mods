"""Package changed decompressed Global archive sections for distribution."""

from __future__ import annotations

import hashlib
import argparse
import json
import shutil
import struct
import sys
import zlib
from pathlib import Path


HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[3]
sys.path.insert(0, str(ROOT / "tmp" / "ml3"))
from lm3_slot_swap import decompress_entry, read_archive


ORIGINAL = ROOT / "tmp" / "ml3" / "clean" / "global.dict"
BUILT = ROOT / "tmp" / "ml3" / "_mods" / "mario_fbx_replacement" / "romfs" / "global.dict"
OUTPUT = ROOT / "tmp" / "ml3" / "clean_tex" / "_gb_result" / "mario_playable"
INSTALLER_TEMPLATE = OUTPUT / "install_mario_playable.py"


def digest(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def archive_parts(path: Path):
    dictionary, data, entries, _table, compressed = read_archive(path)
    return dictionary, data, entries, compressed


def stored_payload(data, entry, compressed):
    size = entry.compressed_size if compressed else entry.decompressed_size
    return bytes(data[entry.offset:entry.offset + size])


def decode_section(data, entry, compressed):
    raw = stored_payload(data, entry, compressed)
    if compressed:
        try:
            return zlib.decompress(raw), "zlib"
        except zlib.error:
            return raw, "raw"
    return raw, "raw"


def changed_ranges(before: bytes, after: bytes):
    if len(before) != len(after):
        raise ValueError("compact patches require unchanged section lengths")
    ranges = []
    start = None
    last = None
    for offset, (old, new) in enumerate(zip(before, after)):
        if old == new:
            continue
        if start is None:
            start = last = offset
        elif offset - last <= 9:
            # Including a gap of up to eight bytes costs no more than another
            # offset/length record and usually compresses better.
            last = offset
        else:
            ranges.append((start, last + 1))
            start = last = offset
    if start is not None:
        ranges.append((start, last + 1))
    return ranges


def encode_patch(before: bytes, after: bytes):
    ranges = changed_ranges(before, after)
    raw = bytearray(b"MGP0" + struct.pack("<I", len(ranges)))
    changed_bytes = 0
    for start, end in ranges:
        payload = after[start:end]
        raw.extend(struct.pack("<II", start, len(payload)))
        raw.extend(payload)
        changed_bytes += len(payload)
    compressed = zlib.compress(bytes(raw), level=9)
    packed = b"MGPZ" + struct.pack("<I", len(raw)) + compressed
    return (packed if len(packed) < len(raw) else bytes(raw)), len(ranges), changed_bytes


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--built", type=Path, default=BUILT)
    parser.add_argument("--output", type=Path, default=OUTPUT)
    parser.add_argument("--mod-name", default="mario_playable")
    parser.add_argument(
        "--sections",
        type=int,
        nargs="+",
        default=(0, 52, 54, 65),
        help="Global section indexes expected to differ",
    )
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    for stale in list(args.output.glob("global_section_*.bin")) + list(args.output.glob("global_*.delta.bin")):
        stale.unlink()
    _clean_dictionary, clean_data, clean_entries, clean_compressed = archive_parts(ORIGINAL)
    _built_dictionary, built_data, built_entries, built_compressed = archive_parts(args.built)
    if clean_compressed != built_compressed or len(clean_entries) != len(built_entries):
        raise ValueError("built Global archive structure differs from the supported clean archive")
    sections = []
    for index in args.sections:
        if index < 0 or index >= len(clean_entries):
            raise ValueError(f"Global section {index} is absent")
        before, before_storage = decode_section(clean_data, clean_entries[index], clean_compressed)
        after, after_storage = decode_section(built_data, built_entries[index], built_compressed)
        if before_storage != after_storage:
            raise ValueError(f"Global section {index} storage mode changed")
        if before == after:
            raise ValueError(f"expected Global section {index} did not change")
        if len(before) != len(after):
            raise ValueError(f"Global section {index} size changed")
        filename = f"global_section_{index}.bin"
        patch, range_count, changed_bytes = encode_patch(before, after)
        (args.output / filename).write_bytes(patch)
        sections.append({
            "index": index,
            "file": filename,
            "section_size": len(after),
            "patch_size": len(patch),
            "range_count": range_count,
            "covered_changed_bytes": changed_bytes,
            "storage": after_storage,
            "original_sha256": digest(before),
            "replacement_sha256": digest(after),
        })
    (args.output / "sections.json").write_text(
        json.dumps({
            "format": "LM3 compact Global section delta v2",
            "mod_name": args.mod_name,
            "sections": sections,
        }, indent=2) + "\n",
        encoding="utf-8",
    )
    installer = args.output / f"install_{args.mod_name}.py"
    if INSTALLER_TEMPLATE.resolve() != installer.resolve():
        shutil.copyfile(INSTALLER_TEMPLATE, installer)
    print(
        f"packaged {args.mod_name}: "
        + ", ".join(f'section {item["index"]}={item["patch_size"]} bytes' for item in sections)
    )


if __name__ == "__main__":
    main()
