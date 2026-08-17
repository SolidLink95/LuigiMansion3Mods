from __future__ import annotations

import struct
import zlib

from lm3_slot_swap import parse_subentries, read_archive


def decode_archive(path):
    dictionary, data, entries, table_offset, compressed = read_archive(path)
    decoded = {}
    for index, entry in enumerate(entries):
        if entry.unknown2 != 0 or entry.decompressed_size == 0:
            continue
        size = entry.compressed_size if compressed else entry.decompressed_size
        raw = data[entry.offset:entry.offset + size]
        try:
            decoded[index] = zlib.decompress(raw) if compressed else bytes(raw)
        except zlib.error:
            inflater = zlib.decompressobj()
            output = bytearray()
            for position in range(0, len(raw), 65536):
                try:
                    output.extend(inflater.decompress(raw[position:position + 65536]))
                except zlib.error:
                    break
            if output:
                decoded[index] = bytes(output)
    return dictionary, data, entries, table_offset, compressed, decoded


def texture_records(table: bytes, file63: bytes):
    records = {}
    entries = parse_subentries(table)
    for index, entry in enumerate(entries):
        if entry.kind != 0xB501:
            continue
        if index + 1 >= len(entries) or entries[index + 1].kind != 0xB502:
            raise ValueError("B501 texture header is not followed by B502 image data")
        texture_hash = struct.unpack_from("<I", file63, entry.offset)[0]
        records[texture_hash] = [entry, entries[index + 1]]
    return records
