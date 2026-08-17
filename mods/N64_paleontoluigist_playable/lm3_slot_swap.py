from __future__ import annotations

import struct
import zlib
from dataclasses import dataclass
from pathlib import Path

ENTRY_SIZE = 16
MODEL_START = 0xB006


@dataclass
class FileEntry:
    offset: int
    decompressed_size: int
    compressed_size: int
    unknown1: int
    unknown2: int
    unknown3: int


@dataclass
class SubEntry:
    table_offset: int
    kind: int
    flags: int
    size: int
    offset: int


def read_archive(dict_path: Path):
    dictionary = bytearray(dict_path.read_bytes())
    data = bytearray(dict_path.with_suffix(".data").read_bytes())
    if len(dictionary) < 16:
        raise ValueError("dictionary header is truncated")
    magic, _unknown, compressed, _padding, _largest, count, chunks, _strings, _pad = struct.unpack_from(
        "<IHBBIBBBB", dictionary, 0
    )
    if magic != 0xA9F32458:
        raise ValueError(f"unexpected dictionary magic 0x{magic:08X}")
    table_offset = 16 + chunks * 24
    entries = [
        FileEntry(*struct.unpack_from("<IIIHBB", dictionary, table_offset + index * ENTRY_SIZE))
        for index in range(count)
    ]
    return dictionary, data, entries, table_offset, bool(compressed)


def decompress_entry(data: bytes, entry: FileEntry, compressed: bool) -> bytes:
    size = entry.compressed_size if compressed else entry.decompressed_size
    payload = data[entry.offset:entry.offset + size]
    return zlib.decompress(payload) if compressed else bytes(payload)


def replace_entry(dictionary, data, entries, table_offset, index, payload, compressed):
    entry = entries[index]
    packed = zlib.compress(payload, level=9) if compressed else payload
    capacity = entry.compressed_size if compressed else entry.decompressed_size
    if packed and len(packed) > capacity:
        raise ValueError(f"file entry {index} grew beyond its allocation: {len(packed)} > {capacity}")
    data[entry.offset:entry.offset + capacity] = packed + bytes(capacity - len(packed))
    struct.pack_into("<II", dictionary, table_offset + index * ENTRY_SIZE + 4, len(payload), len(packed))
    entry.decompressed_size = len(payload)
    entry.compressed_size = len(packed)


def parse_subentries(table: bytes):
    position = 0
    while position + 2 <= len(table) and struct.unpack_from("<H", table, position)[0] == 0x1301:
        position += 24
    result = []
    while position + 12 <= len(table):
        kind, flags, size, offset = struct.unpack_from("<HHII", table, position)
        result.append(SubEntry(position, kind, flags, size, offset))
        position += 12
    return result


def group_models(entries):
    models = []
    current = None
    for entry in entries:
        if entry.kind == MODEL_START:
            current = []
            models.append(current)
        if current is not None:
            current.append(entry)
    return models
