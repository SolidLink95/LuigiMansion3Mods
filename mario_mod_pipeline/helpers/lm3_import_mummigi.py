from __future__ import annotations

import struct
import sys
import zlib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[2] / "src-tauri" / "misc"))
from lm3_slot_swap import ENTRY_SIZE, FileEntry, group_models, parse_subentries, read_archive


GLOBAL = Path(r"W:\coding\TotkBits\tmp\ml3\clean\global.dict")
PERSISTENT = Path(r"E:\Yuzu\dumps\LM3\romfs\Scarescraper\Persistent.dict")
OUTPUT = Path(r"W:\coding\TotkBits\tmp\ml3\mummigi_story_mod\romfs")
# Diagnostic stage 2: global slot 29 has target-owned allocations large enough
# for the primary Mummigi model. Appended geometry ranges parse in tools but are
# rejected by LM3's runtime, matching the behavior of the earlier offset-only
# slot-swap experiments.
TARGETS = (29,)
SOURCES = (38,)
IMPORT_GEOMETRY = False

FILE_FOR_KIND = {
    0xB006: 52, 0xB005: 54, 0xB00C: 52, 0xB004: 52,
    0xB00A: 52, 0xB00B: 52, 0xB003: 52, 0xB007: 52,
    0xB001: 52, 0xB002: 52, 0xB100: 53, 0xB008: 53,
    0xB009: 53, 0xB101: 52, 0xB102: 52, 0xB103: 52,
}


def align(value: int, alignment: int = 16) -> int:
    return (value + alignment - 1) & -alignment


def decode_archive(path: Path):
    dictionary, data, entries, table_offset, compressed = read_archive(path)
    decoded = {}
    for index, entry in enumerate(entries):
        if entry.unknown2 != 0 or entry.decompressed_size == 0:
            continue
        raw_size = entry.compressed_size if compressed else entry.decompressed_size
        raw = data[entry.offset : entry.offset + raw_size]
        try:
            decoded[index] = zlib.decompress(raw) if compressed else bytes(raw)
        except zlib.error:
            # Persistent entry 65 has damaged bytes near the very end of its
            # 194 MB image block, after every Mummigi texture. Preserve the
            # valid prefix instead of discarding the whole entry.
            inflater = zlib.decompressobj()
            output = bytearray()
            for position in range(0, len(raw), 65536):
                try:
                    output.extend(inflater.decompress(raw[position : position + 65536]))
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


def texture_table_end(table: bytes) -> int:
    """Return the byte offset immediately after the leading B501/B502 block."""
    entries = parse_subentries(table)
    index = 0
    while index + 1 < len(entries):
        if entries[index].kind != 0xB501 or entries[index + 1].kind != 0xB502:
            break
        index += 2
    if index == 0:
        raise ValueError("archive has no leading B501/B502 texture block")
    return entries[index - 1].table_offset + 12


def append_chunk(buffer: bytearray, payload: bytes) -> int:
    offset = align(len(buffer))
    buffer.extend(bytes(offset - len(buffer)))
    buffer.extend(payload)
    return offset


def main() -> None:
    gd, gdata, ge, gto, gc, gf = decode_archive(GLOBAL)
    _, _, _, _, pc, pf = decode_archive(PERSISTENT)
    if not gc or not pc:
        raise ValueError("the importer expects compressed LM3 archives")

    changed = {index: bytearray(gf[index]) for index in (0, 52, 53, 54, 63, 65)}
    global_models = group_models(parse_subentries(changed[0]))
    source_models = group_models(parse_subentries(pf[0]))

    # Import each Mummigi LOD into newly owned global ranges and point the
    # four Story Mode Luigi records at those ranges.
    for target, source in zip(TARGETS, SOURCES) if IMPORT_GEOMETRY else ():
        source_by_kind = {}
        for chunk in source_models[source]:
            source_by_kind.setdefault(chunk.kind, []).append(chunk)
        occurrences = {}
        for target_chunk in global_models[target]:
            occurrence = occurrences.get(target_chunk.kind, 0)
            occurrences[target_chunk.kind] = occurrence + 1
            candidates = source_by_kind.get(target_chunk.kind, [])
            if occurrence >= len(candidates):
                raise ValueError(
                    f"Mummigi slot {source} lacks target chunk {target_chunk.kind:04X}/{occurrence}"
                )
            source_chunk = candidates[occurrence]
            file_index = FILE_FOR_KIND[target_chunk.kind]
            if source_chunk.size > target_chunk.size:
                raise ValueError(
                    f"global slot {target} chunk {target_chunk.kind:04X} allocation "
                    f"is too small: 0x{target_chunk.size:X} < 0x{source_chunk.size:X}"
                )
            payload = pf[file_index][source_chunk.offset : source_chunk.offset + source_chunk.size]
            new_offset = target_chunk.offset
            changed[file_index][new_offset : new_offset + source_chunk.size] = payload
            changed[file_index][
                new_offset + source_chunk.size : new_offset + target_chunk.size
            ] = bytes(target_chunk.size - source_chunk.size)
            struct.pack_into(
                "<II", changed[0], target_chunk.table_offset + 4,
                source_chunk.size, new_offset,
            )

    # Discover textures referenced by the imported Mummigi material blocks.
    persistent_textures = texture_records(pf[0], pf[63])
    global_textures = texture_records(changed[0], changed[63])
    needed = set()
    for source in SOURCES:
        material = next(c for c in source_models[source] if c.kind == 0xB006)
        payload = pf[52][material.offset : material.offset + material.size]
        for position in range(0, len(payload) - 3, 4):
            value = struct.unpack_from("<I", payload, position)[0]
            if value in persistent_textures and value not in global_textures:
                needed.add(value)

    # Texture subrecords are self-describing through the B501 hash header.
    # Appending them keeps all existing global record offsets stable.
    added_records = bytearray()
    for texture_hash in sorted(needed):
        header_record, data_record = persistent_textures[texture_hash]
        if data_record is None:
            raise ValueError(f"texture {texture_hash:08X} has no B502 data record")
        header = pf[63][header_record.offset : header_record.offset + header_record.size]
        image = pf[65][data_record.offset : data_record.offset + data_record.size]
        header_offset = append_chunk(changed[63], header)
        image_offset = append_chunk(changed[65], image)
        added_records += struct.pack(
            "<HHII", 0xB501, header_record.flags, len(header), header_offset
        )
        added_records += struct.pack(
            "<HHII", 0xB502, data_record.flags, len(image), image_offset
        )
    # The game expects texture records to remain a single leading block.
    # Insert before the first non-texture record instead of appending after the
    # model and animation tables.
    insertion = texture_table_end(changed[0])
    changed[0][insertion:insertion] = added_records

    # Rebuild global.data so expanded compressed entries are not constrained by
    # their original physical allocations. Source-1 dictionary records belong
    # to the companion patch source and retain their own offsets.
    rebuilt = bytearray()
    for index, entry in enumerate(ge):
        if entry.unknown2 != 0 or entry.decompressed_size == 0:
            continue
        rebuilt.extend(bytes(align(len(rebuilt), 8) - len(rebuilt)))
        new_offset = len(rebuilt)
        payload = bytes(changed[index]) if index in changed else gf[index]
        packed = zlib.compress(payload, level=9)
        rebuilt.extend(packed)
        struct.pack_into(
            "<III", gd, gto + index * ENTRY_SIZE,
            new_offset, len(payload), len(packed),
        )

    # The archive header's largest-file field is used by the runtime to size
    # its decompression buffer. Entry 65 grows when the DLC textures are
    # imported, so retaining the original value can cause an overrun/crash.
    largest_file = max(
        struct.unpack_from("<I", gd, gto + index * ENTRY_SIZE + 4)[0]
        for index, entry in enumerate(ge)
        if entry.decompressed_size != 0
    )
    struct.pack_into("<I", gd, 8, largest_file)

    OUTPUT.mkdir(parents=True, exist_ok=True)
    (OUTPUT / "global.dict").write_bytes(gd)
    (OUTPUT / "global.data").write_bytes(rebuilt)
    (OUTPUT / "global.patch").write_bytes(GLOBAL.with_suffix(".patch").read_bytes())

    # Full archive reopen plus model and texture dependency verification.
    _, _, _, _, _, emitted = decode_archive(OUTPUT / "global.dict")
    emitted_header = (OUTPUT / "global.dict").read_bytes()
    header_largest = struct.unpack_from("<I", emitted_header, 8)[0]
    if header_largest != max(len(payload) for payload in emitted.values()):
        raise AssertionError("largest-file archive header is inconsistent")
    models = group_models(parse_subentries(emitted[0]))
    textures = texture_records(emitted[0], emitted[63])
    emitted_entries = parse_subentries(emitted[0])
    first_non_texture = next(
        index for index, record in enumerate(emitted_entries)
        if record.kind not in (0xB501, 0xB502)
    )
    if any(
        record.kind in (0xB501, 0xB502)
        for record in emitted_entries[first_non_texture:]
    ):
        raise AssertionError("emitted texture records are not contiguous")
    if not needed <= textures.keys():
        raise AssertionError("not all Mummigi textures were emitted")
    for target, source in zip(TARGETS, SOURCES) if IMPORT_GEOMETRY else ():
        if len(models[target]) != len(source_models[source]):
            raise AssertionError(f"slot {target} chunk count mismatch")
        for target_chunk, source_chunk in zip(models[target], source_models[source]):
            if (target_chunk.kind, target_chunk.size) != (source_chunk.kind, source_chunk.size):
                raise AssertionError(f"slot {target} differs from Mummigi slot {source}")
    print(
        f"wrote verified Mummigi Story Mode import with {len(needed)} textures to {OUTPUT}"
    )


if __name__ == "__main__":
    main()
