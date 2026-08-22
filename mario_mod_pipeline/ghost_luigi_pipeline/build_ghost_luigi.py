"""Ghost Luigi: SELECTED meshes of Story Luigi slots 27/28 render with Global
slot 13's ghost materials (shading + semi-transparency); every other mesh
keeps its vanilla material and shader; slots 29/30 mirror slot 27.

Per-mesh transfer (the validated technique from the all-mesh build, now
selective): a ghosted mesh needs the ghost SHADER (B003 descriptor +0x10
format and +0x18 layout/shader hash pair), the ghost mesh's whole B007 block
(binding + marker + shader-parameter offsets), and the ghost B006 chunk
content those offsets resolve against. Vanilla meshes need their vanilla
material bytes, blocks and descriptors.

Combined layout per target slot (both chunks relocated into slot 29's freed
B00B region -- dead space once 29/30 are mirrored, proven orphaned in-game):

- new B006 chunk = slot 13's whole B006 chunk at base 0 (so ghost B007
  blocks copy VERBATIM, exactly the configuration validated in-game) + the
  slot's vanilla B006 chunk at base 0x380.
- new B007 chunk = per mesh, in order: the mapped ghost submesh's block
  verbatim for ghosted meshes; the vanilla block with every chunk-relative
  offset word rebased by +0x380 for the rest. (Verified across slots 27/28:
  every non-0/0xFFFFFFFF u32 in a block is a B006-chunk-relative offset --
  same-hash blocks are byte-identical, and the one mesh with a shifted
  material shifts ALL such words by the same delta.)
- B002's B007 u32 count updated; B006/B007 records redirected in section 0
  AND the 13 patch chunk-table copies. Section 52 keeps its vanilla size and
  the patch header stays byte-identical.

Run directly: ``python res/build_ghost_luigi_material_mod.py``
"""

from __future__ import annotations

import json
import subprocess
import struct
import sys
import zlib
from pathlib import Path

HERE = Path(__file__).resolve().parent
PIPELINE = HERE.parent
ROOT = PIPELINE.parent
sys.path[:0] = [str(PIPELINE / "helpers"), str(PIPELINE / "res")]

from lm3_slot_swap import (  # noqa: E402
    decompress_entry,
    group_models,
    parse_subentries,
    read_archive,
)
from build_expanded_global import (  # noqa: E402
    DATA_ALIGN,
    ENTRY_SIZE,
    PATCH_RECORD_COUNT,
    PATCH_TABLE_FIRST,
    PATCH_TABLE_STRIDE,
    STREAM_ALIGN,
    align_up,
    apply_table_edits,
    encode_texture,
    find_chunk,
    patched_patch,
    texture_records,
)

SOURCE_SLOT = 13
# slot -> {mesh index: (source submesh in SOURCE_SLOT, expected mesh hash)}
TARGETS = {
    27: {
        5: (1, 0x3D4EA53B),
        6: (0, 0x40242EA2),
        7: (0, 0x40243E90),
        12: (0, 0x202F334F),
        14: (0, 0xA92A6B5E),
    },
    28: {
        0: (1, 0x3D4EA53B),
        1: (0, 0x40242EA2),
        2: (0, 0x40243E90),
        7: (0, 0x202F334F),
        11: (0, 0xA92A6B5E),
    },
}
MIRROR_SLOTS = {29: 27, 30: 27}
# CHUNK-relative float tweaks applied to the copied slot-13 B006 chunk ->
# (expected vanilla value, scale). These are the validated glow controls,
# reduced by a further 20% from the previous 0.25 scale. Candidate transparency
# controls at +0x80/+0x240 and +0xAC/+0x26C were tested and remain untouched.
# The same B007 parameter slot resolves to +0x84/+0x244 in the two source
# materials; across other materials using this shader it naturally ranges from
# 0.7 to 1.0. In-game testing confirmed it as the material opacity multiplier.
# Scale 0.35 makes the confirmed 0.7 build 50% more transparent.
MATERIAL_TWEAKS = {
    0xA0: (20.0, 0.2),
    0xA4: (10.0, 0.2),
    0x100: (5.0, 0.2),
    0x84: (1.0, 0.35),
    0x244: (1.0, 0.35),
}
MOD_NAME = "ghost_luigi"
OUTPUT = ROOT / "tmp" / "_mods" / MOD_NAME / "romfs"
HOST_SLOT, HOST_KIND = 29, 0xB00B  # freed region hosting the new chunks

MARKER = bytes.fromhex("FFFFFFFFFFFFFFFFFFFFFFFF00000000FFFFFFFFFFFFFFFF00000000")
UV_SOURCE = ROOT / "tmp" / "ghost_luigi" / "ghost_info.fbx"
UV_DATA = ROOT / "tmp" / "ghost_luigi" / "ghost_uv_build.json"
TEXTURE_SOURCE = ROOT / "tmp" / "ghost_luigi" / "tex"
TEXTURE_CONFIG = HERE / "textures.json"


def mesh_blocks(file52, model):
    """Split a model's B007 chunk into per-mesh blocks (binding + marker + words)."""
    b003 = find_chunk(model, 0xB003)
    b007 = find_chunk(model, 0xB007)
    mesh_count = b003.size // 0x40
    positions = []
    position = b007.offset
    while True:
        position = file52.find(MARKER, position, b007.offset + b007.size)
        if position < 0:
            break
        positions.append(position - 4)
        position += 4
    if len(positions) != mesh_count or positions[0] != b007.offset:
        raise ValueError("unexpected B007 block layout")
    ends = positions[1:] + [b007.offset + b007.size]
    return [bytes(file52[start:end]) for start, end in zip(positions, ends)]


def rebased_block(block: bytes, delta: int, limit: int) -> bytes:
    """Shift every chunk-relative offset word (non-0/FF u32) by delta."""
    words = list(struct.unpack(f"<{len(block) // 4}I", block))
    for index, word in enumerate(words):
        if word in (0, 0xFFFFFFFF):
            continue
        if word > limit:
            raise ValueError(f"block word {index} = {word:#x} exceeds chunk limit")
        words[index] = word + delta
    return struct.pack(f"<{len(words)}I", *words)


def collect_mesh_records(file52, model):
    """Return each mesh descriptor and B004 stream-offset record."""
    b003 = find_chunk(model, 0xB003)
    b004 = find_chunk(model, 0xB004)
    cursor = b004.offset
    records = []
    for index in range(b003.size // 0x40):
        descriptor = b003.offset + index * 0x40
        skinned = struct.unpack_from("<I", file52, descriptor + 0x28)[0] != 0xFFFFFFFF
        records.append((descriptor, cursor, skinned))
        cursor += 16 if skinned else 12
    if cursor != b004.offset + b004.size:
        raise AssertionError("B004 walk does not consume the whole chunk")
    return records


def extract_uv_maps(blender: Path) -> dict:
    """Extract FBX data through Blender; callers consume only its UV arrays."""
    names = [
        f"slot_{slot}_mesh_{mesh:02d}_{expected_hash:08X}"
        + ("#slot13_submesh1" if submesh == 1 else "")
        for slot, meshes in sorted(TARGETS.items())
        for mesh, (submesh, expected_hash) in sorted(meshes.items())
    ]
    UV_DATA.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [str(blender), "--background", "--factory-startup", "--python",
         str(PIPELINE / "res" / "extract_fbx_replacement.py"), "--", str(UV_SOURCE),
         str(UV_DATA), *names],
        check=True,
    )
    return json.loads(UV_DATA.read_text(encoding="utf-8"))


def main() -> None:
    local = json.loads((ROOT / "local.json").read_text(encoding="utf-8"))
    uv_maps = extract_uv_maps(Path(local["blender"]))
    clean = Path(local["romfs"]) / "global.dict"
    dictionary, data, entries, table_offset, compressed = read_archive(clean)
    if not compressed:
        raise ValueError("expected a compressed Global archive")
    file0 = bytearray(decompress_entry(data, entries[0], compressed))
    file52 = bytearray(decompress_entry(data, entries[52], compressed))
    file54 = bytearray(decompress_entry(data, entries[54], compressed))
    file63 = bytearray(decompress_entry(data, entries[63], compressed))
    file65 = bytearray(decompress_entry(data, entries[65], compressed))
    clean54 = bytes(file54)
    models = group_models(parse_subentries(file0))

    # Replace UVs in place. Every other byte in section 54 remains untouched.
    uv_offsets = set()
    for slot, meshes in sorted(TARGETS.items()):
        model = models[slot]
        b005 = find_chunk(model, 0xB005)
        records = collect_mesh_records(file52, model)
        for mesh, (submesh, expected_hash) in sorted(meshes.items()):
            name = f"slot_{slot}_mesh_{mesh:02d}_{expected_hash:08X}"
            if submesh == 1:
                name += "#slot13_submesh1"
            descriptor, b004_cursor, skinned = records[mesh]
            if not skinned:
                raise ValueError(f"{name} unexpectedly uses an unskinned stream")
            actual_hash = struct.unpack_from("<I", file52, descriptor)[0]
            vertex_count = struct.unpack_from("<I", file52, descriptor + 0x0C)[0]
            vertex_offset = struct.unpack_from("<I", file52, b004_cursor + 4)[0]
            uvs = uv_maps[name]["uvs"]
            if actual_hash != expected_hash or len(uvs) != vertex_count:
                raise ValueError(f"{name} does not exactly match the game mesh")
            for index, (u, v) in enumerate(uvs):
                base = b005.offset + vertex_offset + index * 0x30
                struct.pack_into("<f", file54, base + 0x0C, u)
                struct.pack_into("<f", file54, base + 0x1C, 1.0 - v)
                uv_offsets.update(range(base + 0x0C, base + 0x10))
                uv_offsets.update(range(base + 0x1C, base + 0x20))
            print(f"slot {slot} mesh {mesh}: applied {vertex_count} UV coordinates")

    # --- source: slot 13's chunk, per-submesh blocks and descriptor fields ---
    source_model = models[SOURCE_SLOT]
    source_b006 = find_chunk(source_model, 0xB006)
    source_b003 = find_chunk(source_model, 0xB003)
    ghost_chunk = bytearray(
        file52[source_b006.offset : source_b006.offset + source_b006.size]
    )
    for offset, (expected, scale) in sorted(MATERIAL_TWEAKS.items()):
        value = struct.unpack_from("<f", ghost_chunk, offset)[0]
        if abs(value - expected) > 1e-4:
            raise AssertionError(
                f"material tweak at +{offset:#x}: expected {expected}, found {value}"
            )
        struct.pack_into("<f", ghost_chunk, offset, value * scale)
        print(f"material tweak: +{offset:#x} {value:.4f} -> {value * scale:.4f}")

    configured = json.loads(TEXTURE_CONFIG.read_text(encoding="utf-8"))
    if not isinstance(configured, dict):
        raise ValueError("textures.json must contain a hash-to-hash object")
    texture_redirects = {
        int(target, 16): int(source, 16)
        for source, target in configured.items()
    }
    for target_hash, source_hash in sorted(texture_redirects.items()):
        needle = struct.pack("<I", target_hash)
        replacement = struct.pack("<I", source_hash)
        count = ghost_chunk.count(needle)
        if count == 0:
            raise ValueError(
                f"ghost material does not reference configured target {target_hash:08X}"
            )
        ghost_chunk = bytearray(bytes(ghost_chunk).replace(needle, replacement))
        print(
            f"ghost texture redirect: {target_hash:08X} -> {source_hash:08X} "
            f"({count} references)"
        )

    textures = texture_records(file0, file63)
    for source_name in sorted(configured):
        source_hash = int(source_name, 16)
        png = TEXTURE_SOURCE / f"{source_hash:08X}.png"
        if not png.is_file():
            raise FileNotFoundError(f"configured ghost texture is missing: {png}")
        if source_hash not in textures:
            raise ValueError(f"texture {source_hash:08X} has no Global allocation")
        header, image = textures[source_hash]
        header_bytes = file63[header.offset : header.offset + header.size]
        original = bytes(file65[image.offset : image.offset + image.size])
        encoded = encode_texture(png, header_bytes[12], original)
        if len(encoded) != image.size:
            raise ValueError(
                f"texture {source_hash:08X} encoded size {len(encoded)} "
                f"!= allocation {image.size}"
            )
        file65[image.offset : image.offset + image.size] = encoded
        print(f"encoded ghost texture {source_hash:08X} ({len(encoded)} bytes)")
    ghost_chunk = bytes(ghost_chunk)
    ghost_blocks = mesh_blocks(file52, source_model)
    ghost_fields = {}
    for submesh in range(source_b003.size // 0x40):
        base = source_b003.offset + submesh * 0x40
        ghost_fields[submesh] = (
            bytes(file52[base + 0x10 : base + 0x14]),
            bytes(file52[base + 0x18 : base + 0x20]),
        )
    vanilla_base = align_up(len(ghost_chunk), STREAM_ALIGN)
    print(
        f"source slot {SOURCE_SLOT}: chunk {len(ghost_chunk):#x} bytes, "
        f"{len(ghost_blocks)} blocks; vanilla materials relocate to base "
        f"{vanilla_base:#x}"
    )

    # --- per-slot combined chunks -------------------------------------------
    host = find_chunk(models[HOST_SLOT], HOST_KIND)
    cursor = align_up(host.offset, STREAM_ALIGN)
    chunk_edits = []
    payload_expectations = []
    vanilla_descriptors = {}
    for slot in sorted(TARGETS):
        model = models[slot]
        b006 = find_chunk(model, 0xB006)
        b007 = find_chunk(model, 0xB007)
        b003 = find_chunk(model, 0xB003)
        b002 = find_chunk(model, 0xB002)
        mesh_count = b003.size // 0x40
        vanilla_chunk = bytes(file52[b006.offset : b006.offset + b006.size])
        vanilla_blocks = mesh_blocks(file52, model)
        vanilla_descriptors[slot] = bytes(
            file52[b003.offset : b003.offset + b003.size]
        )

        new_b006 = (
            ghost_chunk
            + bytes(vanilla_base - len(ghost_chunk))
            + vanilla_chunk
        )
        parts = []
        for mesh in range(mesh_count):
            mapping = TARGETS[slot].get(mesh)
            if mapping is None:
                parts.append(
                    rebased_block(vanilla_blocks[mesh], vanilla_base, b006.size + 8)
                )
                continue
            submesh, expected_hash = mapping
            actual_hash = struct.unpack_from("<I", file52, b003.offset + mesh * 0x40)[0]
            if actual_hash != expected_hash:
                raise ValueError(
                    f"slot {slot} mesh {mesh} hash {actual_hash:08X} != map's "
                    f"{expected_hash:08X}"
                )
            parts.append(ghost_blocks[submesh])
            fmt, pair = ghost_fields[submesh]
            base = b003.offset + mesh * 0x40
            file52[base + 0x10 : base + 0x14] = fmt
            file52[base + 0x18 : base + 0x20] = pair
        new_b007 = b"".join(parts)

        b006_placed = cursor
        cursor = align_up(cursor + len(new_b006), STREAM_ALIGN)
        b007_placed = cursor
        cursor = align_up(cursor + len(new_b007), STREAM_ALIGN)
        if cursor > host.offset + host.size:
            raise ValueError("combined chunks exceed the freed host region")
        file52[b006_placed : b006_placed + len(new_b006)] = new_b006
        file52[b007_placed : b007_placed + len(new_b007)] = new_b007
        struct.pack_into("<II", file0, b006.table_offset + 4, len(new_b006), b006_placed)
        struct.pack_into("<II", file0, b007.table_offset + 4, len(new_b007), b007_placed)
        chunk_edits.append((0xB006, b006.size, b006.offset, len(new_b006), b006_placed))
        chunk_edits.append((0xB007, b007.size, b007.offset, len(new_b007), b007_placed))
        old_count = struct.unpack_from("<I", file52, b002.offset + 4)[0]
        if old_count != b007.size // 4:
            raise AssertionError(f"slot {slot} B002 u32 count mismatch")
        struct.pack_into("<I", file52, b002.offset + 4, len(new_b007) // 4)
        payload_expectations.append(
            dict(slot=slot, b006=(b006_placed, new_b006), b007=(b007_placed, new_b007))
        )
        print(
            f"slot {slot}: ghosted meshes {sorted(TARGETS[slot])}, B006 "
            f"{b006.size:#x}->{len(new_b006):#x} @0x{b006_placed:X}, B007 "
            f"{b007.size:#x}->{len(new_b007):#x} @0x{b007_placed:X}"
        )

    # --- mirror slots (use CURRENT records, after the redirects) -------------
    current_models = group_models(parse_subentries(file0))
    for mirror_slot, source_slot in sorted(MIRROR_SLOTS.items()):
        source_by_kind = {}
        for record in current_models[source_slot]:
            source_by_kind.setdefault(record.kind, []).append(record)
        occurrences = {}
        redirected = 0
        for record in current_models[mirror_slot]:
            occurrence = occurrences.get(record.kind, 0)
            occurrences[record.kind] = occurrence + 1
            if record.kind == 0xB100:
                continue
            candidates = source_by_kind.get(record.kind, [])
            if occurrence >= len(candidates):
                raise ValueError(
                    f"slot {mirror_slot} chunk {record.kind:04X} occurrence "
                    f"{occurrence} has no slot {source_slot} equivalent"
                )
            source_record = candidates[occurrence]
            if (record.size, record.offset) == (source_record.size, source_record.offset):
                continue
            struct.pack_into(
                "<II", file0, record.table_offset + 4,
                source_record.size, source_record.offset,
            )
            chunk_edits.append(
                (record.kind, record.size, record.offset,
                 source_record.size, source_record.offset)
            )
            redirected += 1
        print(
            f"slot {mirror_slot}: redirected {redirected} chunk records at "
            f"slot {source_slot}'s model"
        )

    # --- rebuild global.data pure-append -------------------------------------
    changed_sections = (0, 52, 54, 65)
    payloads = {
        0: bytes(file0), 52: bytes(file52), 54: bytes(file54), 65: bytes(file65)
    }
    output_data = bytearray(data)
    for index in changed_sections:
        packed = zlib.compress(payloads[index], level=9)
        start = align_up(len(output_data), DATA_ALIGN)
        output_data.extend(bytes(start - len(output_data)))
        output_data.extend(packed)
        struct.pack_into(
            "<III", dictionary, table_offset + index * ENTRY_SIZE,
            start, len(payloads[index]), len(packed),
        )
        entries[index].offset = start
        entries[index].decompressed_size = len(payloads[index])
        entries[index].compressed_size = len(packed)
        print(f"entry {index}: appended at 0x{start:X} ({len(packed)} compressed)")
    largest = max(entry.compressed_size for entry in entries)
    struct.pack_into("<I", dictionary, 8, largest)

    section_54_size = entries[54].decompressed_size
    OUTPUT.mkdir(parents=True, exist_ok=True)
    (OUTPUT / "global.dict").write_bytes(dictionary)
    (OUTPUT / "global.data").write_bytes(output_data)
    (OUTPUT / "global.patch").write_bytes(
        patched_patch(
            clean.with_suffix(".patch"), section_54_size, section_54_size, chunk_edits
        )
    )

    # --- verification --------------------------------------------------------
    _dict2, emitted_data, emitted_entries, _table2, emitted_compressed = (
        read_archive(OUTPUT / "global.dict")
    )
    if bytes(emitted_data[: len(data)]) != bytes(data):
        raise AssertionError("vanilla global.data bytes were modified")
    for index in changed_sections:
        if decompress_entry(emitted_data, emitted_entries[index], emitted_compressed) != (
            payloads[index]
        ):
            raise AssertionError(f"entry {index} did not round-trip")
    emitted0 = decompress_entry(emitted_data, emitted_entries[0], emitted_compressed)
    emitted52 = decompress_entry(emitted_data, emitted_entries[52], emitted_compressed)
    emitted54 = decompress_entry(emitted_data, emitted_entries[54], emitted_compressed)
    changed54 = {
        index for index, (before, after) in enumerate(zip(clean54, emitted54))
        if before != after
    }
    if not changed54:
        raise AssertionError("FBX UV maps did not change any game UV bytes")
    if not changed54 <= uv_offsets:
        raise AssertionError("section 54 changed outside UV fields")
    print(f"UV verification passed: {len(changed54)} changed bytes, all in UV fields")
    emitted_models = group_models(parse_subentries(emitted0))
    for expectation in payload_expectations:
        slot = expectation["slot"]
        for kind, key in ((0xB006, "b006"), (0xB007, "b007")):
            placed, payload = expectation[key]
            record = find_chunk(emitted_models[slot], kind)
            if (record.offset, record.size) != (placed, len(payload)):
                raise AssertionError(f"slot {slot} {kind:04X} record not redirected")
            if emitted52[placed : placed + len(payload)] != payload:
                raise AssertionError(f"slot {slot} {kind:04X} content mismatch")
        b003 = find_chunk(emitted_models[slot], 0xB003)
        b002 = find_chunk(emitted_models[slot], 0xB002)
        for mesh in range(b003.size // 0x40):
            base = b003.offset + mesh * 0x40
            mapping = TARGETS[slot].get(mesh)
            fmt = emitted52[base + 0x10 : base + 0x14]
            pair = emitted52[base + 0x18 : base + 0x20]
            if mapping is not None:
                expected_fmt, expected_pair = ghost_fields[mapping[0]]
                if fmt != expected_fmt or pair != expected_pair:
                    raise AssertionError(f"slot {slot} mesh {mesh} shader fields wrong")
            else:
                vanilla = vanilla_descriptors[slot][mesh * 0x40 : (mesh + 1) * 0x40]
                if fmt != vanilla[0x10:0x14] or pair != vanilla[0x18:0x20]:
                    raise AssertionError(
                        f"slot {slot} mesh {mesh} vanilla shader fields were modified"
                    )
        if struct.unpack_from("<I", emitted52, b002.offset + 4)[0] != (
            len(expectation["b007"][1]) // 4
        ):
            raise AssertionError(f"slot {slot} B002 u32 count not updated")
    for mirror_slot, source_slot in sorted(MIRROR_SLOTS.items()):
        mirror_records = sorted(
            (record.kind, record.size, record.offset)
            for record in emitted_models[mirror_slot]
            if record.kind != 0xB100
        )
        source_records = sorted(
            (record.kind, record.size, record.offset)
            for record in emitted_models[source_slot]
            if record.kind != 0xB100
        )
        if mirror_records != source_records:
            raise AssertionError(
                f"slot {mirror_slot} does not exactly mirror slot {source_slot}"
            )
    emitted_patch = (OUTPUT / "global.patch").read_bytes()
    vanilla_patch = clean.with_suffix(".patch").read_bytes()
    if len(emitted_patch) != len(vanilla_patch):
        raise AssertionError("emitted global.patch changed length")
    if emitted_patch[:PATCH_TABLE_FIRST] != vanilla_patch[:PATCH_TABLE_FIRST]:
        raise AssertionError("global.patch header changed")
    expected_table = bytes(
        apply_table_edits(
            bytearray(
                zlib.decompress(
                    vanilla_patch[PATCH_TABLE_FIRST : PATCH_TABLE_FIRST + PATCH_TABLE_STRIDE]
                )
            ),
            chunk_edits,
        )
    )
    for copy in range(PATCH_RECORD_COUNT):
        start = PATCH_TABLE_FIRST + copy * PATCH_TABLE_STRIDE
        if zlib.decompress(emitted_patch[start : start + PATCH_TABLE_STRIDE]) != (
            expected_table
        ):
            raise AssertionError(f"patch table copy {copy} does not match the edits")
    print(
        "verification passed: vanilla data intact, sections round-trip, combined "
        "chunks valid, mirrors exact, patch header identical, patch tables updated"
    )
    print(f"wrote Ghost Luigi mod: {OUTPUT}")


if __name__ == "__main__":
    main()
