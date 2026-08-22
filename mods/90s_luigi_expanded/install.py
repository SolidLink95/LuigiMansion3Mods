"""Install an LM3 expanded Global archive mod (delta format v1, fixed-size).

Reads a CLEAN ROMFS dump (global.dict / global.data / global.patch), applies
the packaged section deltas and the rewritten patch chunk table, and writes
the three rebuilt files into a ``romfs`` directory NEXT TO THIS SCRIPT — the
supplied ROMFS is never modified. Drop the whole mod folder into the
emulator's LayeredFS load directory, e.g.:

    yuzu:    %APPDATA%/yuzu/load/0100DCA0064A6000/<mod_name>/romfs/
    ryujinx: use the mods manager on title 0100DCA0064A6000

Every input is verified against SHA-256 hashes of the supported clean archive
before anything is written, and every rebuilt section is verified again after
patching and after recompression. Expanded meshes live inside reclaimed
vanilla chunk regions, so no section ever changes size and the global.patch
header stays byte-identical to the original.

Usage:  python install.py <path_to_clean_romfs>

Requires Python 3.9+ and ~1 GB free disk space; the rebuild takes a few
minutes (zlib recompression of the large sections).
"""

from __future__ import annotations

import hashlib
import json
import os
import struct
import sys
import time
import zlib
from pathlib import Path


MAGIC = 0xA9F32458
ENTRY_SIZE = 16
DATA_ALIGN = 8
PACKAGE_DIR = Path(__file__).resolve().parent
MANIFEST = PACKAGE_DIR / "manifest.json"
FORMAT = "LM3 expanded Global archive delta v1"
ROMFS_FILES = ("global.dict", "global.data", "global.patch")


def fail(message: str) -> int:
    print(f"error: {message}", file=sys.stderr)
    return 2


def digest(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def align_up(value: int, alignment: int) -> int:
    return (value + alignment - 1) & -alignment


def package_file(name: str) -> bytes:
    path = PACKAGE_DIR / name
    if not path.is_file():
        raise ValueError(f"packaged file is missing: {name}")
    return path.read_bytes()


def read_dictionary(dict_path: Path):
    """Return (dictionary bytes, entry list, entry-table offset).

    Entry: [offset, decompressed_size, compressed_size] into global.data.
    """
    dictionary = bytearray(dict_path.read_bytes())
    if len(dictionary) < 16:
        raise ValueError("global.dict is truncated")
    magic, _unknown, compressed, _pad, _largest, count, chunks, _strings, _pad2 = (
        struct.unpack_from("<IHBBIBBBB", dictionary, 0)
    )
    if magic != MAGIC:
        raise ValueError(f"global.dict has unexpected magic 0x{magic:08X}")
    if not compressed:
        raise ValueError("expected a compressed Global archive")
    table_offset = 16 + chunks * 24
    if table_offset + count * ENTRY_SIZE > len(dictionary):
        raise ValueError("global.dict file table is truncated")
    entries = [
        list(struct.unpack_from("<III", dictionary, table_offset + index * ENTRY_SIZE))
        for index in range(count)
    ]
    return dictionary, entries, table_offset


def apply_delta(current: bytes, delta: bytes) -> bytes:
    """Apply an MGP0/MGPZ range-replacement delta; the size never changes."""
    if len(delta) < 8:
        raise ValueError("delta file is truncated")
    if delta[:4] == b"MGPZ":
        raw_size = struct.unpack_from("<I", delta, 4)[0]
        delta = zlib.decompress(delta[8:])
        if len(delta) != raw_size or delta[:4] != b"MGP0":
            raise ValueError("delta failed decompression validation")
    elif delta[:4] != b"MGP0":
        raise ValueError("delta has invalid magic")
    count = struct.unpack_from("<I", delta, 4)[0]
    result = bytearray(current)
    cursor = 8
    previous_end = 0
    for _record in range(count):
        if cursor + 8 > len(delta):
            raise ValueError("delta record is truncated")
        offset, size = struct.unpack_from("<II", delta, cursor)
        cursor += 8
        end = offset + size
        if offset < previous_end or end > len(result) or cursor + size > len(delta):
            raise ValueError("delta contains an invalid range")
        result[offset:end] = delta[cursor : cursor + size]
        cursor += size
        previous_end = end
    if cursor != len(delta):
        raise ValueError("delta has trailing data")
    return bytes(result)


def rebuild_sections(manifest, entries, data: bytearray) -> list[tuple[int, bytes]]:
    """Verify and patch every changed section; return [(index, new bytes)]."""
    replacements = []
    for item in manifest["sections"]:
        index = int(item["index"])
        if item.get("mode") != "patch":
            raise ValueError(
                f"unsupported section mode {item.get('mode')!r}: this installer "
                "handles fixed-size sections only (expanded meshes are stored "
                "inside reclaimed vanilla regions)"
            )
        if index < 0 or index >= len(entries):
            raise ValueError(f"Global section {index} is absent")
        offset, decompressed_size, compressed_size = entries[index]
        current = zlib.decompress(bytes(data[offset : offset + compressed_size]))
        if len(current) != decompressed_size:
            raise ValueError(f"Global section {index} decompressed to a wrong size")
        current_hash = digest(current)
        if current_hash == item["replacement_sha256"]:
            raise ValueError(
                f"Global section {index} already contains this mod; start from a "
                "clean dump"
            )
        if current_hash != item["original_sha256"]:
            raise ValueError(
                f"Global section {index} does not match the supported clean archive "
                "(wrong game version or already modded)"
            )
        replacement = apply_delta(current, package_file(item["file"]))
        if len(replacement) != int(item["section_size"]) or len(replacement) != len(current):
            raise ValueError(f"Global section {index} changed size")
        if digest(replacement) != item["replacement_sha256"]:
            raise ValueError(f"Global section {index} failed its SHA-256 result check")
        replacements.append((index, replacement))
        print(f"section {index}: verified and patched ({len(replacement):,} bytes)")
    return replacements


def rebuild_patch(manifest, romfs: Path) -> bytes:
    """Verify global.patch, apply the header delta, splice the chunk table."""
    info = manifest["patch"]
    payload = bytearray((romfs / "global.patch").read_bytes())
    if len(payload) != int(info["file_size"]):
        raise ValueError("global.patch has an unexpected size")
    original_hash = digest(bytes(payload))
    if original_hash == info["replacement_sha256"]:
        raise ValueError("global.patch already contains this mod; start from a clean dump")
    if original_hash != info["original_sha256"]:
        raise ValueError(
            "global.patch does not match the supported clean archive "
            "(wrong game version or already modded)"
        )
    header_size = int(info["header"]["size"])
    payload[:header_size] = apply_delta(
        bytes(payload[:header_size]), package_file(info["header"]["file"])
    )
    table = info["table"]
    slot = zlib.decompress(package_file(table["file"]))
    first, stride, count = int(table["first"]), int(table["stride"]), int(table["count"])
    if len(slot) != stride or digest(slot) != table["slot_sha256"]:
        raise ValueError("packaged chunk-table slot failed its checksum")
    for copy in range(count):
        payload[first + copy * stride : first + (copy + 1) * stride] = slot
    if digest(bytes(payload)) != info["replacement_sha256"]:
        raise ValueError("rebuilt global.patch failed its SHA-256 result check")
    print("global.patch: verified and rebuilt")
    return bytes(payload)


def append_recompressed(dictionary: bytearray, entries, table_offset: int,
                        data: bytearray, replacements) -> None:
    """Append recompressed sections to global.data pure-append and update
    the dict entries plus the largest-compressed-size header field."""
    for index, replacement in replacements:
        started = time.monotonic()
        packed = zlib.compress(replacement, level=9)
        start = align_up(len(data), DATA_ALIGN)
        data.extend(bytes(start - len(data)))
        data.extend(packed)
        struct.pack_into(
            "<III", dictionary, table_offset + index * ENTRY_SIZE,
            start, len(replacement), len(packed),
        )
        entries[index] = [start, len(replacement), len(packed)]
        print(f"section {index}: recompressed ({len(packed):,} bytes, "
              f"{time.monotonic() - started:.0f}s)")
    largest = max(entry[2] for entry in entries)
    struct.pack_into("<I", dictionary, 8, largest)
    # Self-check: every rewritten entry must round-trip to its verified bytes.
    for index, replacement in replacements:
        offset, _decompressed, compressed = entries[index]
        if zlib.decompress(bytes(data[offset : offset + compressed])) != replacement:
            raise ValueError(f"Global section {index} did not round-trip")


def main() -> int:
    if len(sys.argv) != 2:
        return fail(f"usage: python {Path(sys.argv[0]).name} <path_to_clean_romfs>")
    romfs = Path(sys.argv[1]).expanduser()
    if not romfs.is_dir():
        return fail(f"ROMFS path is not a directory: {romfs}")
    for name in ROMFS_FILES:
        if not (romfs / name).is_file():
            return fail(f"ROMFS path must contain {name}")
    if not MANIFEST.is_file():
        return fail(f"package manifest is missing: {MANIFEST.name}")
    output_dir = PACKAGE_DIR / "romfs"
    try:
        resolved_output = output_dir.resolve()
        resolved_romfs = romfs.resolve()
        if resolved_output == resolved_romfs or resolved_romfs in resolved_output.parents:
            return fail("refusing to write into the supplied ROMFS; copy the mod "
                        "folder elsewhere and re-run")
    except OSError:
        pass

    try:
        manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
        if manifest.get("format") != FORMAT:
            raise ValueError(f"unsupported package format: {manifest.get('format')}")
        mod_name = manifest["mod_name"]
        print(f"installing {mod_name}")

        print("reading the clean Global archive (this takes a moment)...")
        dictionary, entries, table_offset = read_dictionary(romfs / "global.dict")
        data = bytearray((romfs / "global.data").read_bytes())
        for index, (offset, _decompressed, compressed) in enumerate(entries):
            if offset + compressed > len(data):
                raise ValueError(f"Global section {index} exceeds global.data")

        replacements = rebuild_sections(manifest, entries, data)
        patch_payload = rebuild_patch(manifest, romfs)

        print("recompressing sections (this takes a few minutes)...")
        append_recompressed(dictionary, entries, table_offset, data, replacements)

        output_dir.mkdir(parents=True, exist_ok=True)
        for name, payload in (
            ("global.dict", bytes(dictionary)),
            ("global.data", bytes(data)),
            ("global.patch", patch_payload),
        ):
            temp = output_dir / f"{name}.{mod_name}.tmp"
            temp.write_bytes(payload)
            os.replace(temp, output_dir / name)

        expected_dict = manifest.get("built_dict_sha256")
        if expected_dict and digest(bytes(dictionary)) != expected_dict:
            raise ValueError("rebuilt global.dict does not match the author's build")
        expected_data = manifest.get("built_data_sha256")
        if expected_data and digest(bytes(data)) != expected_data:
            print("note: global.data differs from the author's build only in zlib "
                  "encoder output; the archive is self-consistent and valid")
    except (KeyError, OSError, ValueError, zlib.error, json.JSONDecodeError) as error:
        return fail(str(error))

    print(f"installed {mod_name} into {output_dir.resolve()}")
    print("copy this mod folder (containing romfs/) into the emulator's load "
          f"directory, e.g. %APPDATA%/yuzu/load/0100DCA0064A6000/{mod_name}/")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
