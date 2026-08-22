"""Build an expanded Global archive: replace slot 27 mesh 14 with a mesh that has
MORE vertices than the original allocation permits.

This deliberately goes beyond the fixed-layout invariant using a pure-append
strategy so that no vanilla byte of ``global.data`` is overwritten:

1. Slot 27's whole B005 chunk is relocated: the vanilla chunk bytes are copied
   verbatim to the end of decompressed section 54 (preserving every other
   mesh's chunk-relative offsets) and the new, larger mesh-14 streams
   (skin/vertex/aux/sentinel/index) are appended after them. The section-0
   B005 record for slot 27 gets the new offset and grown size.
2. Vertices are stored sorted by bone-influence count and the descriptor's
   four u16 group counts (vertices with exactly 1/2/3/4 influences) are
   updated to match. This ordering invariant was verified against every mesh
   of slots 27-30 in the clean archive.
3. The mesh-14 aux stream bytes are copied verbatim (stale aux is the
   validated behaviour for replaced hats) and the per-vertex 8-byte sentinel
   stream is extended with the vanilla ``ffffffffff7fff7f`` pattern.
4. The recompressed sections 0, 52 and 54 are appended at the end of
   ``global.data`` (8-aligned, like vanilla entries). Only their three dict
   table entries and the header's largest-compressed-size field change.
5. ``global.patch`` is the authoritative runtime metadata: a plain header
   (13 records, stride 0xD0, carrying decompressed section sizes), then 13
   identical zlib copies of the chunk sub-entry table (stride 0x7E310), then
   one cooked data blob. The game reads its chunk table FROM THE PATCH, not
   from entry 0 in ``global.data`` — this is why section-0-only slot
   redirects never worked in-game. Both the header size fields and the B005
   record inside every compressed table copy are rewritten to describe the
   relocated, grown chunk; each re-deflated copy must fit its fixed
   0x7E310-byte slot and is zero-padded to keep the layout intact.

Self-contained archived copy (uses the bundled ``lm3_slot_swap.py``).
Run directly from the repository root: ``python expand_archive/build_expanded_global.py``
In-game validated 2026-08-21. See ``EXPANSION_WORKFLOW.md`` in this folder.
"""

from __future__ import annotations

import json
import math
import shutil
import struct
import sys
import zlib
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))

from lm3_slot_swap import (  # noqa: E402
    decompress_entry,
    group_models,
    parse_subentries,
    read_archive,
)

LOCAL = json.loads((ROOT / "local.json").read_text())
CLEAN = Path(LOCAL["romfs"]) / "global.dict"
MESH_JSON = ROOT / "tmp" / "expand_work" / "expand.meshes.json"
MESH_NAME = "slot_27_mesh_14_A92A6B5E"
MESH_HASH = 0xA92A6B5E
SLOT = 27
MESH_INDEX = 14
SKELETON_GROUP = 27
MOD_NAME = "000_global_expanded"
OUTPUT = ROOT / "tmp" / "_mods" / MOD_NAME / "romfs"
ENTRY_SIZE = 16
SENTINEL = bytes.fromhex("FFFFFFFFFF7FFF7F")
DATA_ALIGN = 8
STREAM_ALIGN = 16


def align_up(value: int, alignment: int) -> int:
    return (value + alignment - 1) & -alignment


def find_chunk(model, kind):
    return next(record for record in model if record.kind == kind)


def b004_cursor_for_mesh(file52, model, mesh_index):
    b003 = find_chunk(model, 0xB003)
    b004 = find_chunk(model, 0xB004)
    cursor = b004.offset
    for index in range(mesh_index):
        marker = struct.unpack_from("<I", file52, b003.offset + index * 0x40 + 0x28)[0]
        cursor += 16 if marker != 0xFFFFFFFF else 12
    return cursor


def skeleton_groups(table):
    groups = []
    current = None
    for record in parse_subentries(table):
        if record.kind == 0x7101:
            current = []
            groups.append(current)
        if current is not None and 0x7101 <= record.kind <= 0x7106:
            current.append(record)
    return groups


def skeleton_id_to_hash(file53, group):
    record = next(item for item in group if item.kind == 0x7105)
    result = {}
    for offset in range(record.offset, record.offset + record.size, 8):
        bone_hash, bone_id = struct.unpack_from("<II", file53, offset)
        result[bone_id] = bone_hash
    return result


def resolve_vertex_bones(weights, id_to_hash, hash_to_local, bone_parents):
    """Resolve FBX vertex-group weights to at most two local B103 bones."""
    resolved_by_bone = {}
    for name, weight in weights:
        if not name.startswith("bone_"):
            continue
        candidate = name
        visited = set()
        local_bone = None
        while candidate is not None and candidate not in visited:
            visited.add(candidate)
            if candidate.startswith("bone_"):
                bone_id = int(candidate[5:])
                bone_hash = id_to_hash.get(bone_id)
                if bone_hash in hash_to_local:
                    local_bone = hash_to_local[bone_hash]
                    break
            candidate = bone_parents.get(candidate)
        if local_bone is None:
            raise ValueError(f"FBX bone {name} and its parents are missing from target B103")
        resolved_by_bone[local_bone] = resolved_by_bone.get(local_bone, 0.0) + float(weight)
    resolved = [
        (bone, weight)
        for bone, weight in resolved_by_bone.items()
        if math.isfinite(weight) and weight > 1e-8
    ]
    resolved.sort(key=lambda item: item[1], reverse=True)
    resolved = resolved[:2]
    if not resolved:
        raise ValueError("vertex has no usable skin weight")
    if len(resolved) == 1:
        repaired = [1.0]
    else:
        total = resolved[0][1] + resolved[1][1]
        dominant = struct.unpack("<f", struct.pack("<f", resolved[0][1] / total))[0]
        repaired = [dominant, struct.unpack("<f", struct.pack("<f", 1.0 - dominant))[0]]
    ids = [item[0] for item in resolved] + [0] * (4 - len(resolved))
    repaired += [0.0] * (4 - len(repaired))
    return ids, repaired, len(resolved)


PATCH_HEADER_LIMIT = 0x1000
PATCH_RECORD_COUNT = 13
PATCH_TABLE_FIRST = 0xAA8
PATCH_TABLE_STRIDE = 0x7E310


def patched_patch(
    patch_path: Path,
    old_section_54: int,
    new_section_54: int,
    old_chunk_offset: int,
    old_chunk_size: int,
    new_chunk_offset: int,
    new_chunk_size: int,
) -> bytes:
    """Rewrite global.patch: header section-54 sizes and the B005 record in
    every compressed chunk-table copy (the table the game actually uses)."""
    payload = bytearray(patch_path.read_bytes())

    # 1. Header: 13 language records carry the decompressed section-54 size.
    needle = struct.pack("<I", old_section_54)
    sites = []
    position = 0
    while True:
        position = payload.find(needle, position)
        if position < 0:
            break
        sites.append(position)
        position += 1
    if len(sites) != PATCH_RECORD_COUNT:
        raise ValueError(
            f"expected {PATCH_RECORD_COUNT} section-54 size fields in global.patch, "
            f"found {len(sites)}"
        )
    if any(site % 4 or site >= PATCH_HEADER_LIMIT for site in sites):
        raise ValueError(f"unexpected section-54 size field locations: {sites}")
    strides = {b - a for a, b in zip(sites, sites[1:])}
    if strides != {0xD0}:
        raise ValueError(f"global.patch language records have unexpected strides {strides}")
    for site in sites:
        struct.pack_into("<I", payload, site, new_section_54)
    print(
        f"global.patch: rewrote {len(sites)} section-54 size fields "
        f"{old_section_54} -> {new_section_54}"
    )

    # 2. The 13 compressed chunk-table copies. All are byte-identical, so
    #    inflate once, patch the single matching B005 record, deflate once,
    #    and splice the same stream into every fixed-stride slot.
    first = payload[PATCH_TABLE_FIRST : PATCH_TABLE_FIRST + PATCH_TABLE_STRIDE]
    for copy in range(1, PATCH_RECORD_COUNT):
        start = PATCH_TABLE_FIRST + copy * PATCH_TABLE_STRIDE
        if payload[start : start + PATCH_TABLE_STRIDE] != first:
            raise ValueError(f"global.patch table copy {copy} is not identical to copy 0")
    table = bytearray(zlib.decompress(bytes(first)))
    record = struct.pack("<II", old_chunk_size, old_chunk_offset)
    hits = []
    position = 0
    while True:
        position = table.find(record, position)
        if position < 0:
            break
        if position % 4 == 0 and struct.unpack_from("<H", table, position - 4)[0] == 0xB005:
            hits.append(position - 4)
        position += 1
    if len(hits) != 1:
        raise ValueError(
            f"expected exactly one B005 record for the target chunk in the "
            f"patch chunk table, found {len(hits)}"
        )
    struct.pack_into("<II", table, hits[0] + 4, new_chunk_size, new_chunk_offset)
    packed = zlib.compress(bytes(table), level=9)
    if len(packed) > PATCH_TABLE_STRIDE:
        raise ValueError(
            f"patched chunk table compresses to {len(packed)} bytes, exceeding "
            f"the fixed {PATCH_TABLE_STRIDE}-byte slot"
        )
    slot = packed + bytes(PATCH_TABLE_STRIDE - len(packed))
    for copy in range(PATCH_RECORD_COUNT):
        start = PATCH_TABLE_FIRST + copy * PATCH_TABLE_STRIDE
        payload[start : start + PATCH_TABLE_STRIDE] = slot
    print(
        f"global.patch: rewrote B005 record in {PATCH_RECORD_COUNT} chunk-table "
        f"copies (0x{old_chunk_offset:X}/0x{old_chunk_size:X} -> "
        f"0x{new_chunk_offset:X}/0x{new_chunk_size:X}; recompressed "
        f"{len(packed)} bytes into 0x{PATCH_TABLE_STRIDE:X}-byte slots)"
    )
    return bytes(payload)


def main() -> None:
    dictionary, data, entries, table_offset, compressed = read_archive(CLEAN)
    if not compressed:
        raise ValueError("expected a compressed Global archive")
    files = {
        index: bytearray(decompress_entry(data, entries[index], compressed))
        for index in (0, 52, 53, 54)
    }
    clean_section_54_len = len(files[54])
    models = group_models(parse_subentries(files[0]))
    model = models[SLOT]
    b003 = find_chunk(model, 0xB003)
    b005 = find_chunk(model, 0xB005)

    descriptor = b003.offset + MESH_INDEX * 0x40
    if struct.unpack_from("<I", files[52], descriptor)[0] != MESH_HASH:
        raise ValueError(f"slot {SLOT} mesh {MESH_INDEX} is not {MESH_HASH:08X}")
    old_index_offset, old_index_flags, old_vertex_count = struct.unpack_from(
        "<III", files[52], descriptor + 4
    )
    if old_index_flags >> 24 == 0x80:
        raise ValueError("expected 16-bit indices on the target mesh")
    b004_cursor = b004_cursor_for_mesh(files[52], model, MESH_INDEX)
    marker = struct.unpack_from("<I", files[52], descriptor + 0x28)[0]
    if marker == 0xFFFFFFFF:
        raise ValueError("target mesh is unexpectedly unskinned")
    old_skin_off, old_vertex_off, old_aux_off, old_sent_off = struct.unpack_from(
        "<IIII", files[52], b004_cursor
    )
    aux_size = old_sent_off - old_aux_off
    old_sentinel = bytes(
        files[54][
            b005.offset + old_sent_off : b005.offset + old_sent_off + old_vertex_count * 8
        ]
    )
    if old_sentinel != SENTINEL * old_vertex_count:
        raise ValueError("clean sentinel stream is not the expected uniform pattern")

    mesh_data = json.loads(MESH_JSON.read_text(encoding="utf-8"))
    source = mesh_data[MESH_NAME]
    positions = source["positions"]
    normals = source["normals"]
    uvs = source["uvs"]
    faces = source["faces"]
    weights = source["weights"]
    bone_parents = mesh_data.get("_bone_parents", {})
    count = len(positions)
    if not (count == len(normals) == len(uvs) == len(weights)):
        raise ValueError("vertex attributes have inconsistent lengths")
    if any(len(face) != 3 for face in faces):
        raise ValueError("replacement contains a non-triangle face")
    if count <= old_vertex_count:
        raise ValueError(
            f"replacement has {count} vertices; this builder is for expansion "
            f"beyond the original {old_vertex_count}"
        )
    if count > 0xFFFF:
        raise ValueError("replacement exceeds 16-bit index range")

    # Skeleton translation tables.
    id_to_hash = skeleton_id_to_hash(files[53], skeleton_groups(files[0])[SKELETON_GROUP])
    b103 = find_chunk(model, 0xB103)
    hashes = [
        struct.unpack_from("<I", files[52], b103.offset + offset)[0]
        for offset in range(0, b103.size, 4)
    ]
    hash_to_local = {bone_hash: index for index, bone_hash in enumerate(hashes)}

    # Resolve skinning, then sort vertices by influence count (1,2,3,4) to
    # keep the verified hardware-skinning batch invariant intact.
    skins = [
        resolve_vertex_bones(weights[i], id_to_hash, hash_to_local, bone_parents)
        for i in range(count)
    ]
    order = sorted(range(count), key=lambda i: skins[i][2])
    remap = [0] * count
    for new_index, old_index in enumerate(order):
        remap[old_index] = new_index
    group_counts = [0, 0, 0, 0]
    for _ids, _weights, influences in skins:
        group_counts[influences - 1] += 1
    if sum(group_counts) != count:
        raise AssertionError("influence group counts do not sum to the vertex count")

    # Template records come from the nearest original vertex, like the
    # validated pipeline, so the unknown trailing 0x14 bytes stay plausible.
    old_vertices = [
        bytes(
            files[54][
                b005.offset + old_vertex_off + i * 0x30 : b005.offset
                + old_vertex_off
                + (i + 1) * 0x30
            ]
        )
        for i in range(old_vertex_count)
    ]
    old_positions = [struct.unpack_from("<fff", record) for record in old_vertices]

    vertex_payload = bytearray()
    skin_payload = bytearray()
    for new_index in range(count):
        i = order[new_index]
        px, py, pz = positions[i]
        best = 0
        best_distance = None
        for j, (ox, oy, oz) in enumerate(old_positions):
            distance = (ox - px) ** 2 + (oy - py) ** 2 + (oz - pz) ** 2
            if best_distance is None or distance < best_distance:
                best_distance = distance
                best = j
        record = bytearray(old_vertices[best])
        struct.pack_into("<fff", record, 0, px, py, pz)
        struct.pack_into("<f", record, 0x0C, uvs[i][0])
        struct.pack_into("<fff", record, 0x10, *normals[i])
        struct.pack_into("<f", record, 0x1C, 1.0 - uvs[i][1])
        vertex_payload.extend(record)
        ids, repaired, _influences = skins[i]
        skin_payload.extend(struct.pack("<BBBBffff", *ids, *repaired))

    flat_indices = [remap[index] for face in faces for index in face]
    if max(flat_indices) >= count:
        raise ValueError("replacement contains an invalid vertex index")
    index_payload = struct.pack(f"<{len(flat_indices)}H", *flat_indices)

    # Relocated B005 chunk: vanilla bytes verbatim, new streams appended.
    chunk = bytearray(files[54][b005.offset : b005.offset + b005.size])
    if len(chunk) != b005.size:
        raise AssertionError("failed to copy the vanilla B005 chunk")

    def append_stream(payload: bytes) -> int:
        start = align_up(len(chunk), STREAM_ALIGN)
        chunk.extend(bytes(start - len(chunk)))
        chunk.extend(payload)
        return start

    new_skin_off = append_stream(skin_payload)
    new_vertex_off = append_stream(vertex_payload)
    aux_bytes = bytes(files[54][b005.offset + old_aux_off : b005.offset + old_sent_off])
    new_aux_off = append_stream(aux_bytes + SENTINEL * count)
    new_sent_off = new_aux_off + aux_size
    new_index_off = append_stream(index_payload)

    new_chunk_offset = align_up(len(files[54]), STREAM_ALIGN)
    files[54].extend(bytes(new_chunk_offset - len(files[54])))
    files[54].extend(chunk)

    # Patch section 52: descriptor and B004 record of the target mesh only.
    struct.pack_into(
        "<III", files[52], descriptor + 4, new_index_off, len(flat_indices), count
    )
    struct.pack_into(
        "<II",
        files[52],
        descriptor + 0x28,
        (group_counts[1] << 16) | group_counts[0],
        (group_counts[3] << 16) | group_counts[2],
    )
    struct.pack_into(
        "<IIII",
        files[52],
        b004_cursor,
        new_skin_off,
        new_vertex_off,
        new_aux_off,
        new_sent_off,
    )

    # Patch section 0: slot 27's B005 record gets the new size and offset.
    struct.pack_into("<II", files[0], b005.table_offset + 4, len(chunk), new_chunk_offset)

    print(
        f"slot {SLOT} mesh {MESH_INDEX} ({MESH_HASH:08X}): {count} vertices "
        f"({old_vertex_count} original), {len(faces)} triangles, "
        f"influence groups {tuple(group_counts)}"
    )
    print(
        f"B005 chunk relocated: 0x{b005.offset:X} -> 0x{new_chunk_offset:X}, "
        f"size 0x{b005.size:X} -> 0x{len(chunk):X}; section 54 "
        f"{clean_section_54_len} -> {len(files[54])} bytes"
    )

    # Append the three changed sections at the end of global.data; leave every
    # vanilla byte and every other entry untouched.
    output_data = bytearray(data)
    for index in (0, 52, 54):
        payload = bytes(files[index])
        packed = zlib.compress(payload, level=9)
        start = align_up(len(output_data), DATA_ALIGN)
        output_data.extend(bytes(start - len(output_data)))
        output_data.extend(packed)
        struct.pack_into(
            "<III",
            dictionary,
            table_offset + index * ENTRY_SIZE,
            start,
            len(payload),
            len(packed),
        )
        entries[index].offset = start
        entries[index].decompressed_size = len(payload)
        entries[index].compressed_size = len(packed)
        print(
            f"entry {index}: appended at 0x{start:X} "
            f"({len(payload)} decompressed, {len(packed)} compressed)"
        )

    largest = max(entry.compressed_size for entry in entries)
    struct.pack_into("<I", dictionary, 8, largest)

    OUTPUT.mkdir(parents=True, exist_ok=True)
    (OUTPUT / "global.dict").write_bytes(dictionary)
    (OUTPUT / "global.data").write_bytes(output_data)
    (OUTPUT / "global.patch").write_bytes(
        patched_patch(
            CLEAN.with_suffix(".patch"),
            clean_section_54_len,
            len(files[54]),
            b005.offset,
            b005.size,
            new_chunk_offset,
            len(chunk),
        )
    )

    verify(bytes(data), files, clean_section_54_len, b005, count, group_counts,
           new_chunk_offset, len(chunk), new_skin_off, new_vertex_off,
           new_aux_off, new_sent_off, new_index_off, len(flat_indices))
    print(f"wrote expanded Global archive to {OUTPUT}")


def verify(clean_data, files, clean_section_54_len, old_b005, count, group_counts,
           new_chunk_offset, new_chunk_size, new_skin_off, new_vertex_off,
           new_aux_off, new_sent_off, new_index_off, index_count) -> None:
    emitted_dict, emitted_data, emitted_entries, emitted_table, emitted_compressed = (
        read_archive(OUTPUT / "global.dict")
    )
    # 1. The vanilla data file is a byte-exact prefix of the emitted file.
    if bytes(emitted_data[: len(clean_data)]) != clean_data:
        raise AssertionError("vanilla global.data bytes were modified")
    # 2. Changed sections round-trip.
    for index in (0, 52, 54):
        actual = decompress_entry(emitted_data, emitted_entries[index], emitted_compressed)
        if actual != bytes(files[index]):
            raise AssertionError(f"entry {index} did not round-trip")
    # 3. Every other entry still decompresses from its untouched allocation.
    for index, entry in enumerate(emitted_entries):
        if index in (0, 52, 54):
            continue
        if entry.offset + entry.compressed_size > len(clean_data):
            raise AssertionError(f"entry {index} unexpectedly points beyond vanilla data")
    # 4. Structural checks on the emitted model.
    table = decompress_entry(emitted_data, emitted_entries[0], emitted_compressed)
    section52 = decompress_entry(emitted_data, emitted_entries[52], emitted_compressed)
    section54 = decompress_entry(emitted_data, emitted_entries[54], emitted_compressed)
    if section54[: clean_section_54_len] != bytes(files[54][:clean_section_54_len]):
        raise AssertionError("emitted section 54 lost its vanilla prefix")
    models = group_models(parse_subentries(table))
    b005 = find_chunk(models[SLOT], 0xB005)
    if (b005.offset, b005.size) != (new_chunk_offset, new_chunk_size):
        raise AssertionError("slot B005 record does not reference the relocated chunk")
    chunk = section54[b005.offset : b005.offset + b005.size]
    if chunk[: old_b005.size] != section54[old_b005.offset : old_b005.offset + old_b005.size]:
        raise AssertionError("relocated chunk is not a verbatim copy of the vanilla chunk")
    b003 = find_chunk(models[SLOT], 0xB003)
    descriptor = b003.offset + MESH_INDEX * 0x40
    index_offset, index_flags, vertex_count = struct.unpack_from(
        "<III", section52, descriptor + 4
    )
    counts_lo, counts_hi = struct.unpack_from("<II", section52, descriptor + 0x28)
    stored_counts = (counts_lo & 0xFFFF, counts_lo >> 16, counts_hi & 0xFFFF, counts_hi >> 16)
    if (index_offset, index_flags, vertex_count) != (new_index_off, index_count, count):
        raise AssertionError("emitted descriptor mismatch")
    if list(stored_counts) != group_counts:
        raise AssertionError("emitted influence group counts mismatch")
    b004_cursor = b004_cursor_for_mesh(section52, models[SLOT], MESH_INDEX)
    if struct.unpack_from("<IIII", section52, b004_cursor) != (
        new_skin_off, new_vertex_off, new_aux_off, new_sent_off
    ):
        raise AssertionError("emitted B004 record mismatch")
    # 5. Skin stream is sorted by influence count and indices are in range.
    accumulator = 0
    for group_index, group_count in enumerate(stored_counts):
        for vertex in range(accumulator, accumulator + group_count):
            weights = struct.unpack_from(
                "<4f", chunk, new_skin_off + vertex * 0x14 + 4
            )
            influences = sum(1 for weight in weights if weight > 0.0)
            if influences != group_index + 1:
                raise AssertionError(
                    f"vertex {vertex} has {influences} influences, "
                    f"expected {group_index + 1}"
                )
        accumulator += group_count
    indices = struct.unpack_from(f"<{index_count}H", chunk, new_index_off)
    if max(indices) >= vertex_count:
        raise AssertionError("emitted index buffer references a missing vertex")
    if new_sent_off + vertex_count * 8 > len(chunk):
        raise AssertionError("sentinel stream exceeds the relocated chunk")
    if chunk[new_sent_off : new_sent_off + vertex_count * 8] != SENTINEL * vertex_count:
        raise AssertionError("sentinel stream is not the expected pattern")
    # 6. Header bookkeeping.
    stored_largest = struct.unpack_from("<I", emitted_dict, 8)[0]
    actual_largest = max(entry.compressed_size for entry in emitted_entries)
    if stored_largest != actual_largest:
        raise AssertionError("dictionary largest-compressed-size field is stale")
    # 7. Emitted global.patch: every chunk-table copy must carry the new B005
    #    record and be otherwise identical to the vanilla table.
    emitted_patch = (OUTPUT / "global.patch").read_bytes()
    vanilla_patch = CLEAN.with_suffix(".patch").read_bytes()
    if len(emitted_patch) != len(vanilla_patch):
        raise AssertionError("emitted global.patch changed length")
    vanilla_table = zlib.decompress(
        vanilla_patch[PATCH_TABLE_FIRST : PATCH_TABLE_FIRST + PATCH_TABLE_STRIDE]
    )
    for copy in range(PATCH_RECORD_COUNT):
        start = PATCH_TABLE_FIRST + copy * PATCH_TABLE_STRIDE
        emitted_table = zlib.decompress(
            emitted_patch[start : start + PATCH_TABLE_STRIDE]
        )
        if len(emitted_table) != len(vanilla_table):
            raise AssertionError(f"patch table copy {copy} changed length")
        deltas = [
            index
            for index in range(0, len(emitted_table), 4)
            if emitted_table[index : index + 4] != vanilla_table[index : index + 4]
        ]
        if len(deltas) != 2 or deltas[1] - deltas[0] != 4:
            raise AssertionError(
                f"patch table copy {copy} has unexpected changes at {deltas[:6]}"
            )
        size_value, offset_value = struct.unpack_from("<II", emitted_table, deltas[0])
        kind = struct.unpack_from("<H", emitted_table, deltas[0] - 4)[0]
        if (kind, size_value, offset_value) != (0xB005, new_chunk_size, new_chunk_offset):
            raise AssertionError(
                f"patch table copy {copy} B005 record mismatch: "
                f"{kind:04X} 0x{size_value:X} 0x{offset_value:X}"
            )
    print(
        "verification passed: vanilla data intact, sections round-trip, "
        "structure valid, patch tables updated"
    )


if __name__ == "__main__":
    main()
