"""Package an EXPANDED Global archive build for distribution.

Unlike ``package_mario_global_sections.py`` (fixed-layout, in-place section
deltas), an expanded build grows section 54 and rewrites ``global.patch``,
so the package carries:

- in-place MGP0/MGPZ deltas for the same-length sections (0, 52, 65),
- the appended section-54 tail (the vanilla prefix is untouched by design),
- a MGP0/MGPZ delta for the plain global.patch header, and
- ONE copy of the rewritten 516,880-byte compressed chunk-table slot
  (the 13 vanilla copies are byte-identical, and so are the 13 emitted ones,
  so shipping one and splicing it 13 times saves ~6 MB).

The installer template ``installer/install_expanded_global.py`` rebuilds
``global.dict``/``global.data``/``global.patch`` into a LayeredFS ``romfs``
directory next to itself; it never modifies the supplied clean ROMFS.

Usage (from repo root):
    python mario_mod_pipeline/res/package_expanded_global.py \
        --original <clean>/global.dict \
        --built tmp/_mods/000_global_expanded/romfs/global.dict \
        --output mods/<name> --mod-name <name>
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import struct
import sys
import zlib
from pathlib import Path


HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(HERE.parent / "helpers"))
from lm3_slot_swap import decompress_entry, read_archive  # noqa: E402
from package_mario_global_sections import encode_patch  # noqa: E402

PATCH_HEADER_SIZE = 0xAA8
PATCH_TABLE_STRIDE = 0x7E310
PATCH_TABLE_COUNT = 13


def digest(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--original", type=Path, required=True)
    parser.add_argument("--built", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--mod-name", required=True)
    parser.add_argument(
        "--installer-template",
        type=Path,
        default=HERE.parent / "installer" / "install_expanded_global.py",
    )
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    for stale in args.output.glob("global_*.bin"):
        stale.unlink()

    _cd, clean_data, clean_entries, _ct, clean_compressed = read_archive(args.original)
    _bd, built_data, built_entries, _bt, built_compressed = read_archive(args.built)
    if not clean_compressed or not built_compressed:
        raise ValueError("expected compressed Global archives")
    if len(clean_entries) != len(built_entries):
        raise ValueError("built archive entry count differs from clean")
    if bytes(built_data[: len(clean_data)]) != bytes(clean_data):
        raise ValueError("built global.data is not a pure-append rebuild")

    sections = []
    for index in range(len(clean_entries)):
        clean_entry, built_entry = clean_entries[index], built_entries[index]
        # The builder rewrites the dict record of every section it changes;
        # an identical record points at identical bytes (the pure-append
        # check above proved the vanilla data region is untouched).
        if (clean_entry.offset, clean_entry.decompressed_size, clean_entry.compressed_size) == (
            built_entry.offset, built_entry.decompressed_size, built_entry.compressed_size
        ):
            continue
        before = bytes(decompress_entry(clean_data, clean_entry, True))
        after = bytes(decompress_entry(built_data, built_entry, True))
        if before == after:
            continue
        if len(before) == len(after):
            filename = f"global_section_{index}.bin"
            patch, range_count, changed = encode_patch(before, after)
            (args.output / filename).write_bytes(patch)
            sections.append({
                "index": index,
                "mode": "patch",
                "file": filename,
                "section_size": len(after),
                "range_count": range_count,
                "covered_changed_bytes": changed,
                "original_sha256": digest(before),
                "replacement_sha256": digest(after),
            })
            print(f"section {index}: in-place delta, {changed} changed bytes "
                  f"in {range_count} ranges -> {(args.output / filename).stat().st_size} bytes")
        else:
            if after[: len(before)] != before:
                raise ValueError(f"grown section {index} modified its vanilla prefix")
            tail = after[len(before):]
            filename = f"global_section_{index}_tail.bin"
            packed = zlib.compress(tail, level=9)
            (args.output / filename).write_bytes(packed)
            sections.append({
                "index": index,
                "mode": "append",
                "file": filename,
                "section_size": len(after),
                "prefix_size": len(before),
                "tail_sha256": digest(tail),
                "original_sha256": digest(before),
                "replacement_sha256": digest(after),
            })
            print(f"section {index}: grown {len(before)} -> {len(after)}, "
                  f"tail packed to {len(packed)} bytes")

    clean_patch = args.original.with_suffix(".patch").read_bytes()
    built_patch = args.built.with_suffix(".patch").read_bytes()
    if len(clean_patch) != len(built_patch):
        raise ValueError("global.patch changed length")
    table_end = PATCH_HEADER_SIZE + PATCH_TABLE_COUNT * PATCH_TABLE_STRIDE
    if built_patch[table_end:] != clean_patch[table_end:]:
        raise ValueError("global.patch changed beyond the header and table copies")
    slot = built_patch[PATCH_HEADER_SIZE : PATCH_HEADER_SIZE + PATCH_TABLE_STRIDE]
    for copy in range(PATCH_TABLE_COUNT):
        start = PATCH_HEADER_SIZE + copy * PATCH_TABLE_STRIDE
        if built_patch[start : start + PATCH_TABLE_STRIDE] != slot:
            raise ValueError(f"built global.patch table copy {copy} is not identical to copy 0")
        if clean_patch[start : start + PATCH_TABLE_STRIDE] != clean_patch[
            PATCH_HEADER_SIZE : PATCH_HEADER_SIZE + PATCH_TABLE_STRIDE
        ]:
            raise ValueError(f"clean global.patch table copy {copy} is not identical to copy 0")

    header_patch, header_ranges, header_changed = encode_patch(
        clean_patch[:PATCH_HEADER_SIZE], built_patch[:PATCH_HEADER_SIZE]
    )
    (args.output / "global_patch_header.bin").write_bytes(header_patch)
    table_packed = zlib.compress(slot, level=9)
    (args.output / "global_patch_table.bin").write_bytes(table_packed)
    print(f"global.patch: header delta {header_changed} bytes in {header_ranges} ranges; "
          f"table slot packed to {len(table_packed)} bytes")

    manifest = {
        "format": "LM3 expanded Global archive delta v1",
        "mod_name": args.mod_name,
        "sections": sections,
        "patch": {
            "file_size": len(clean_patch),
            "original_sha256": digest(clean_patch),
            "replacement_sha256": digest(built_patch),
            "header": {
                "file": "global_patch_header.bin",
                "size": PATCH_HEADER_SIZE,
            },
            "table": {
                "file": "global_patch_table.bin",
                "first": PATCH_HEADER_SIZE,
                "stride": PATCH_TABLE_STRIDE,
                "count": PATCH_TABLE_COUNT,
                "slot_sha256": digest(slot),
            },
        },
        "built_dict_sha256": digest(args.built.read_bytes()),
        "built_data_sha256": digest(bytes(built_data)),
    }
    (args.output / "manifest.json").write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
    )
    shutil.copyfile(args.installer_template, args.output / "install.py")
    total = sum(item.stat().st_size for item in args.output.iterdir() if item.is_file())
    print(f"packaged {args.mod_name}: {total / 1e6:.1f} MB total in {args.output}")


if __name__ == "__main__":
    main()
