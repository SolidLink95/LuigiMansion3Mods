"""Build the packaged replacement in a separate mod directory."""

from __future__ import annotations

import hashlib
import json
import struct
import sys
import zlib
from dataclasses import dataclass
from pathlib import Path


MAGIC = 0xA9F32458
ENTRY_SIZE = 16
PACKAGE_DIR = Path(__file__).resolve().parent
MANIFEST = PACKAGE_DIR / "sections.json"


@dataclass
class Entry:
    offset: int
    decompressed_size: int
    compressed_size: int


def fail(message: str) -> int:
    print(f"error: {message}", file=sys.stderr)
    return 2


def read_archive(dict_path: Path):
    dictionary = bytearray(dict_path.read_bytes())
    data = bytearray(dict_path.with_suffix(".data").read_bytes())
    if len(dictionary) < 16:
        raise ValueError("global.dict is truncated")
    magic, _unknown, compressed, _padding, _largest, count, chunks, _strings, _pad = (
        struct.unpack_from("<IHBBIBBBB", dictionary, 0)
    )
    if magic != MAGIC:
        raise ValueError(f"global.dict has unexpected magic 0x{magic:08X}")
    table_offset = 16 + chunks * 24
    if table_offset + count * ENTRY_SIZE > len(dictionary):
        raise ValueError("global.dict file table is truncated")
    entries = []
    for index in range(count):
        offset, unpacked, packed = struct.unpack_from(
            "<III", dictionary, table_offset + index * ENTRY_SIZE
        )
        stored_size = packed if compressed else unpacked
        if offset + stored_size > len(data):
            raise ValueError(f"Global section {index} exceeds global.data")
        entries.append(Entry(offset, unpacked, packed))
    return dictionary, data, entries, table_offset, bool(compressed)


def unpack_section(data: bytes, entry: Entry, compressed: bool, storage: str) -> bytes:
    size = entry.compressed_size if compressed else entry.decompressed_size
    payload = bytes(data[entry.offset:entry.offset + size])
    if storage == "zlib":
        return zlib.decompress(payload)
    if storage == "raw":
        return payload
    raise ValueError(f"unsupported section storage mode: {storage}")


def apply_patch(current: bytes, patch: bytes, expected_size: int) -> bytes:
    if len(patch) < 8:
        raise ValueError("compact section patch is truncated")
    magic = patch[:4]
    if magic == b"MGPZ":
        raw_size = struct.unpack_from("<I", patch, 4)[0]
        patch = zlib.decompress(patch[8:])
        if len(patch) != raw_size or patch[:4] != b"MGP0":
            raise ValueError("compact section patch failed decompression validation")
    elif magic != b"MGP0":
        raise ValueError("compact section patch has invalid magic")
    if len(current) != expected_size:
        raise ValueError(
            f"Global section size {len(current)} does not match expected {expected_size}"
        )
    count = struct.unpack_from("<I", patch, 4)[0]
    result = bytearray(current)
    cursor = 8
    previous_end = 0
    for _record in range(count):
        if cursor + 8 > len(patch):
            raise ValueError("compact section patch record is truncated")
        offset, size = struct.unpack_from("<II", patch, cursor)
        cursor += 8
        end = offset + size
        if offset < previous_end or end > len(result) or cursor + size > len(patch):
            raise ValueError("compact section patch has an invalid range")
        result[offset:end] = patch[cursor:cursor + size]
        cursor += size
        previous_end = end
    if cursor != len(patch):
        raise ValueError("compact section patch has trailing data")
    return bytes(result)


def main() -> int:
    if len(sys.argv) != 2:
        return fail(f"usage: python {Path(sys.argv[0]).name} <romfs_path>")
    romfs = Path(sys.argv[1]).expanduser()
    if not romfs.is_dir():
        return fail(f"ROMFS path is not a directory: {romfs}")
    source_dict_path = romfs / "global.dict"
    source_data_path = romfs / "global.data"
    if not source_dict_path.is_file() or not source_data_path.is_file():
        return fail("ROMFS path must contain global.dict and global.data")
    if not MANIFEST.is_file():
        return fail(f"package manifest is missing: {MANIFEST.name}")

    try:
        manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
        mod_name = manifest.get("mod_name", "playable_mod")
        output = PACKAGE_DIR / mod_name / "romfs"
        try:
            output.resolve().relative_to(romfs.resolve())
        except ValueError:
            pass
        else:
            raise ValueError("output directory must be outside the supplied ROMFS path")
        if manifest.get("format") == "LM3 compact physical Global delta v3":
            prepared_files = []
            for item in manifest["files"]:
                target = item["target"]
                if target not in ("global.dict", "global.data"):
                    raise ValueError(f"unsupported target file: {target}")
                source_path = romfs / target
                current = source_path.read_bytes()
                current_hash = hashlib.sha256(current).hexdigest()
                allowed = {item["original_sha256"], item["replacement_sha256"]}
                if current_hash not in allowed:
                    raise ValueError(f"{target} does not match the supported original archive")
                patch_path = PACKAGE_DIR / item["file"]
                if not patch_path.is_file():
                    raise ValueError(f"packaged delta is missing: {patch_path.name}")
                replacement = apply_patch(current, patch_path.read_bytes(), int(item["file_size"]))
                if hashlib.sha256(replacement).hexdigest() != item["replacement_sha256"]:
                    raise ValueError(f"{target} delta failed its SHA-256 result check")
                prepared_files.append((target, replacement))
            output.mkdir(parents=True, exist_ok=True)
            for target, replacement in prepared_files:
                (output / target).write_bytes(replacement)
            patch_path = romfs / "global.patch"
            if patch_path.is_file():
                (output / "global.patch").write_bytes(patch_path.read_bytes())
            print(f"created {mod_name} in {output.resolve()}")
            return 0

        dictionary, data, entries, table_offset, compressed = read_archive(source_dict_path)
        prepared = []
        for item in manifest["sections"]:
            index = int(item["index"])
            if index < 0 or index >= len(entries):
                raise ValueError(f"Global section {index} is absent")
            bin_path = PACKAGE_DIR / item["file"]
            if not bin_path.is_file():
                raise ValueError(f"packaged section is missing: {bin_path.name}")
            patch = bin_path.read_bytes()
            storage = item.get("storage", "zlib" if compressed else "raw")
            current = unpack_section(data, entries[index], compressed, storage)
            current_hash = hashlib.sha256(current).hexdigest()
            allowed = {item["original_sha256"], item["replacement_sha256"]}
            if current_hash not in allowed:
                raise ValueError(
                    f"Global section {index} does not match the supported original archive"
                )
            replacement = apply_patch(current, patch, int(item["section_size"]))
            replacement_hash = hashlib.sha256(replacement).hexdigest()
            if replacement_hash != item["replacement_sha256"]:
                raise ValueError(f"compact section patch {index} failed its checksum")
            packed = zlib.compress(replacement, level=9) if storage == "zlib" else replacement
            capacity = entries[index].compressed_size if compressed else entries[index].decompressed_size
            if len(packed) > capacity:
                raise ValueError(
                    f"Global section {index} needs {len(packed)} bytes but allocation is {capacity}"
                )
            prepared.append((index, replacement, packed, capacity))

        # No target file is changed until every input and replacement has validated.
        for index, replacement, packed, capacity in prepared:
            entry = entries[index]
            data[entry.offset:entry.offset + capacity] = packed + bytes(capacity - len(packed))
            struct.pack_into(
                "<II", dictionary, table_offset + index * ENTRY_SIZE + 4,
                len(replacement), len(packed),
            )

        output.mkdir(parents=True, exist_ok=True)
        (output / "global.dict").write_bytes(dictionary)
        (output / "global.data").write_bytes(data)
        patch_path = romfs / "global.patch"
        if patch_path.is_file():
            (output / "global.patch").write_bytes(patch_path.read_bytes())
    except (KeyError, OSError, ValueError, zlib.error, json.JSONDecodeError) as error:
        return fail(str(error))

    print(f"created {mod_name} in {output.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
