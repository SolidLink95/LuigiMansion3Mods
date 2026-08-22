"""Build an expanded Global archive: replace meshes in one or more model slots
with meshes that may have MORE vertices than their original allocations permit.

Validated technique (in-game 2026-08-21, placement revised 2026-08-22; see
expand_archive/EXPANSION_WORKFLOW.md for the complete format reference):

1. Each touched slot's whole B005 chunk is relocated: the vanilla chunk bytes
   are copied verbatim and the replaced meshes' new streams
   (skin/vertex/aux/sentinel/index) are appended after them. The grown chunk
   is placed INSIDE the freed vanilla B005 regions of the relocated and
   mirrored slots -- section 54 keeps its exact vanilla size. NEVER append
   past the vanilla section end: the game's 1.4 update (global.patch) maps
   ~15 updated models' B005 chunks into 0xB1E1EE0..0xBA63260 BEYOND that end
   (their data is materialized from the patch at runtime), so base-section
   data appended there overlays those models and explodes them in-game
   (observed on Global slots 478/480/481/484/485/808 among others).
2. Vertices are stored sorted by bone-influence count and the descriptor's
   four u16 group counts are updated to match.
3. Aux bytes are copied verbatim; the 8-byte-per-vertex sentinel stream is
   extended (uniform pattern) or replicated from the nearest original vertex.
4. 8-bit index buffers are converted to 16-bit.
5. Sections 0, 52 and 54 are appended at the end of ``global.data``; only
   their dict entries and the header's largest-compressed-size field change.
6. Only the B005 records inside all 13 compressed chunk-table copies of
   ``global.patch`` are rewritten (the game reads its chunk table from the
   patch, not from entry 0 in ``global.data``). The patch header stays
   byte-identical because the section sizes do not change.

Run directly: ``python res/build_expanded_global.py``
"""

from __future__ import annotations

import json
import math
import struct
import subprocess
import sys
import zlib
from pathlib import Path

from PIL import Image

HERE = Path(__file__).resolve().parent
PIPELINE = HERE.parent
ROOT = PIPELINE.parent
sys.path.insert(0, str(PIPELINE / "helpers"))

from lm3_slot_swap import (  # noqa: E402
    decompress_entry,
    group_models,
    parse_subentries,
    read_archive,
)

LOCAL = json.loads((ROOT / "local.json").read_text())
CLEAN = Path(LOCAL["romfs"]) / "global.dict"
MESH_JSON = ROOT / "tmp" / "expand_work" / "expand2.meshes.json"
# PNG files named <GlobalTextureHash>.png; re-encoded into the fixed
# section-65 allocations exactly like the validated Mario pipeline.
TEXTURES_DIR = ROOT / "tmp" / "expand" / "tex"
ASTCENC = "astcenc-avx2.exe"
ASTC_TEMP = ROOT / "tmp" / "expand_work" / "astc_temp"
# slot -> {mesh index in that slot -> object name in the extracted JSON}
TARGETS = {
    27: {12: "submesh_12", 14: "submesh_1"},
    28: {7: "submesh_12", 11: "submesh_1"},
}
SKELETON_GROUP_FOR_SLOT = {27: 27, 28: 27, 29: 28, 30: 28}
# Mirror slots: every chunk record of the key slot is redirected at the value
# slot's chunks (section 0 AND the patch chunk tables), so the game renders the
# exact same model there. Verified safe for 29/30 -> 27: skeleton groups 27 and
# 28 are hash-identical (223/223 bones), so slot 27's B103 binds on the
# cutscene rig. This is the old swap_slots idea, which failed historically only
# because the patch tables were never edited.
MIRROR_SLOTS = {29: 27, 30: 27}
# Material copies: per slot, (target_mesh, source_mesh) pairs. The source
# mesh's whole B006 material record is deep-copied over the target mesh's
# record (the validated armor-pipeline technique), so both meshes render with
# the same material. Mirrored slots inherit this automatically.
MATERIAL_COPIES = {27: [(14, 12)], 28: [(11, 7)]}
MOD_NAME = "000_global_expanded"
OUTPUT = ROOT / "tmp" / "_mods" / MOD_NAME / "romfs"
ENTRY_SIZE = 16
DATA_ALIGN = 8
STREAM_ALIGN = 16

# See EXPANSION_WORKFLOW.md: descriptor +0x20 = (morph_target_count, base) into
# B00A; each target's B00B stream holds one u16 per ORIGINAL vertex, so an
# expanded mesh overruns it once the target activates. Zeroing the count
# disables morph targets for replaced meshes only.
DISABLE_MORPH_TARGETS = True
# The game's model space is Z-up; Y-up FBX exports need (x, y, z) -> (x, -z, y).
Y_UP_TO_Z_UP = True
# Visibility probe: extra model-space offset applied to replacement positions
# after axis conversion. Set to (0, 0, 0) for final builds.
TEST_POSITION_OFFSET = (0.0, 0.0, 0.0)

PATCH_HEADER_LIMIT = 0x1000
PATCH_RECORD_COUNT = 13
PATCH_TABLE_FIRST = 0xAA8
PATCH_TABLE_STRIDE = 0x7E310


def align_up(value: int, alignment: int) -> int:
    return (value + alignment - 1) & -alignment


def find_chunk(model, kind):
    return next(record for record in model if record.kind == kind)


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
        return None  # caller falls back to nearest-original weights
    return normalized_skin(resolved)


def normalized_skin(resolved):
    """Pack up to two (bone, weight) pairs with an exact float32 sum of 1.0."""
    if len(resolved) == 1:
        repaired = [1.0]
    else:
        total = resolved[0][1] + resolved[1][1]
        dominant = struct.unpack("<f", struct.pack("<f", resolved[0][1] / total))[0]
        repaired = [dominant, struct.unpack("<f", struct.pack("<f", 1.0 - dominant))[0]]
    ids = [item[0] for item in resolved] + [0] * (4 - len(resolved))
    repaired += [0.0] * (4 - len(repaired))
    return ids, repaired, len(resolved)


def skin_from_original_record(file54, absolute_skin_offset, original_vertex):
    """Fallback: adopt the nearest original vertex's strongest skin weights."""
    offset = absolute_skin_offset + original_vertex * 0x14
    ids = struct.unpack_from("<BBBB", file54, offset)
    vertex_weights = struct.unpack_from("<ffff", file54, offset + 4)
    resolved = sorted(
        (
            (bone_id, weight)
            for bone_id, weight in zip(ids, vertex_weights)
            if math.isfinite(weight) and weight > 1e-8
        ),
        key=lambda item: item[1],
        reverse=True,
    )[:2]
    if not resolved:
        raise ValueError(
            f"original vertex {original_vertex} has no usable skin weight either"
        )
    return normalized_skin(resolved)


def patched_patch(
    patch_path: Path,
    old_section_54: int,
    new_section_54: int,
    chunk_edits: list[tuple[int, int, int, int]],
) -> bytes:
    """Rewrite global.patch: every touched slot's record in all 13 compressed
    chunk-table copies. The header is left byte-identical -- section sizes must
    not change (growing section 54 collides with the 1.4 update's own
    relocated models past the vanilla end).

    chunk_edits: (kind, old_size, old_offset, new_size, new_offset) per record.
    """
    payload = bytearray(patch_path.read_bytes())

    if new_section_54 != old_section_54:
        raise ValueError(
            "section 54 must keep its vanilla size: the 1.4 update occupies "
            "0xB1E1EE0..0xBA63260 past the vanilla end and growing the base "
            "section overlays it (exploded vanilla models in-game)"
        )
    print("global.patch: section-54 size unchanged; header left byte-identical")

    # The 13 language records carry the decompressed section-54 size; assert
    # they still hold the expected value (sanity, no rewrite).
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

    # 2. The 13 compressed chunk-table copies (byte-identical): inflate once,
    #    apply every record edit, deflate once, splice into every slot.
    first = payload[PATCH_TABLE_FIRST : PATCH_TABLE_FIRST + PATCH_TABLE_STRIDE]
    for copy in range(1, PATCH_RECORD_COUNT):
        start = PATCH_TABLE_FIRST + copy * PATCH_TABLE_STRIDE
        if payload[start : start + PATCH_TABLE_STRIDE] != first:
            raise ValueError(f"global.patch table copy {copy} is not identical to copy 0")
    table = apply_table_edits(bytearray(zlib.decompress(bytes(first))), chunk_edits)
    packed = zlib.compress(bytes(table), level=9)
    if len(packed) > PATCH_TABLE_STRIDE:
        raise ValueError(
            f"patched chunk table compresses to {len(packed)} bytes, exceeding "
            f"the fixed {PATCH_TABLE_STRIDE}-byte slot"
        )
    slot_bytes = packed + bytes(PATCH_TABLE_STRIDE - len(packed))
    for copy in range(PATCH_RECORD_COUNT):
        start = PATCH_TABLE_FIRST + copy * PATCH_TABLE_STRIDE
        payload[start : start + PATCH_TABLE_STRIDE] = slot_bytes
    print(
        f"global.patch: rewrote {len(chunk_edits)} chunk-table records in "
        f"{PATCH_RECORD_COUNT} copies; recompressed table = {len(packed)} of "
        f"0x{PATCH_TABLE_STRIDE:X} bytes"
    )
    return bytes(payload)


def apply_table_edits(table: bytearray, edits) -> bytearray:
    """Apply (kind, old_size, old_offset, new_size, new_offset) record edits to
    a chunk table; each old record must match exactly one 12-byte record."""
    for kind, old_size, old_offset, new_size, new_offset in edits:
        needle = struct.pack("<II", old_size, old_offset)
        hits = []
        position = 0
        while True:
            position = table.find(needle, position)
            if position < 0:
                break
            if position % 4 == 0 and struct.unpack_from("<H", table, position - 4)[0] == kind:
                hits.append(position - 4)
            position += 1
        if len(hits) != 1:
            raise ValueError(
                f"expected exactly one {kind:04X} record for 0x{old_offset:X}/"
                f"0x{old_size:X} in the patch chunk table, found {len(hits)}"
            )
        struct.pack_into("<II", table, hits[0] + 4, new_size, new_offset)
    return table


def texture_records(table: bytes, file63: bytes):
    """Map texture hash -> [B501 header record, B502 image record]."""
    records = {}
    subentries = parse_subentries(table)
    for index, entry in enumerate(subentries):
        if entry.kind != 0xB501:
            continue
        if index + 1 >= len(subentries) or subentries[index + 1].kind != 0xB502:
            raise ValueError("B501 texture header is not followed by B502 image data")
        texture_hash = struct.unpack_from("<I", file63, entry.offset)[0]
        records[texture_hash] = [entry, subentries[index + 1]]
    return records


ASTC_FORMATS = {
    0x1D: (6, 6),
    0x1E: (8, 5),
}


def block_height(width, height, texture_format=None):
    result = 8 if width <= 256 or height <= 256 else 16
    result = 4 if width <= 128 or height <= 128 else result
    result = 2 if width <= 64 or height <= 64 else result
    if texture_format == 0x1D:
        result = 4 if width <= 64 or height <= 64 else result
        result = 2 if width <= 32 or height <= 32 else result
        result = 1 if width <= 32 and height <= 32 else result
    return result


def block_address(x, y, width_blocks, height):
    width_in_gobs = (width_blocks * 16 + 63) // 64
    gob = (
        (y // (8 * height)) * 512 * height * width_in_gobs
        + (x * 16 // 64) * 512 * height
        + ((y % (8 * height)) // 8) * 512
    )
    return (
        gob
        + ((x * 16 % 64) // 32) * 256
        + ((y % 8) // 2) * 64
        + ((x * 16 % 32) // 16) * 32
        + (y % 2) * 16
        + (x * 16 % 16)
    )


def tile_astc(linear, width, height, block_width, block_height_pixels, texture_format):
    width_blocks = (width + block_width - 1) // block_width
    height_blocks = (height + block_height_pixels - 1) // block_height_pixels
    gob_height = block_height(width, height, texture_format)
    width_gobs = (width_blocks * 16 + 63) // 64
    rows = (height_blocks + 8 * gob_height - 1) // (8 * gob_height)
    output = bytearray(rows * 512 * gob_height * width_gobs)
    for y in range(height_blocks):
        for x in range(width_blocks):
            source = (y * width_blocks + x) * 16
            target = block_address(x, y, width_blocks, gob_height)
            output[target : target + 16] = linear[source : source + 16]
    return bytes(output)


def encode_signed_bc4_block(values):
    """Encode sixteen PNG channel values as one BC4_SNORM block."""
    signed = [
        max(-127, min(127, round((value / 127.5 - 1.0) * 127.0))) for value in values
    ]
    high, low = max(signed), min(signed)
    if high == low:
        palette = [high] * 8
    else:
        palette = [high, low] + [
            round(((7 - step) * high + step * low) / 7) for step in range(1, 7)
        ]
    bits = 0
    for index, value in enumerate(signed):
        choice = min(range(8), key=lambda item: abs(palette[item] - value))
        bits |= choice << (index * 3)
    return bytes((high & 0xFF, low & 0xFF)) + bits.to_bytes(6, "little")


def encode_signed_bc5(image):
    width, height = image.size
    pixels = image.convert("RGB").load()
    width_blocks = (width + 3) // 4
    height_blocks = (height + 3) // 4
    linear = bytearray()
    for block_y in range(height_blocks):
        for block_x in range(width_blocks):
            block = [
                pixels[
                    min(block_x * 4 + x, width - 1), min(block_y * 4 + y, height - 1)
                ]
                for y in range(4)
                for x in range(4)
            ]
            linear.extend(encode_signed_bc4_block([pixel[0] for pixel in block]))
            linear.extend(encode_signed_bc4_block([pixel[1] for pixel in block]))
    gob_height = 1 if width <= 8 and height <= 8 else block_height(width, height)
    width_gobs = (width_blocks * 16 + 63) // 64
    rows = (height_blocks + 8 * gob_height - 1) // (8 * gob_height)
    output = bytearray(rows * 512 * gob_height * width_gobs)
    for y in range(height_blocks):
        for x in range(width_blocks):
            source = (y * width_blocks + x) * 16
            target = block_address(x, y, width_blocks, gob_height)
            output[target : target + 16] = linear[source : source + 16]
    return bytes(output)


def encode_texture(png: Path, texture_format: int, original_payload: bytes):
    ASTC_TEMP.mkdir(parents=True, exist_ok=True)
    source = Image.open(png).convert("RGBA")
    if texture_format == 0x16:
        # Encode the whole mip chain: a base-level-only replacement leaves the
        # vanilla normal map in mips 1+, which shades wrongly-placed fabric
        # detail onto the new UV layout at gameplay camera distances.
        levels = []
        for level in range(8):
            width = max(1, source.width >> level)
            height = max(1, source.height >> level)
            mip = source.resize((width, height), Image.Resampling.LANCZOS)
            encoded = encode_signed_bc5(mip)
            encoded_size = sum(len(item) for item in levels)
            if encoded_size + len(encoded) > len(original_payload):
                levels.append(original_payload[encoded_size:])
                break
            levels.append(encoded)
        encoded_size = sum(len(item) for item in levels)
        if encoded_size < len(original_payload):
            levels.append(original_payload[encoded_size:])
        return b"".join(levels)
    levels = []
    for level in range(8):
        width = max(1, source.width >> level)
        height = max(1, source.height >> level)
        mip = source.resize((width, height), Image.Resampling.LANCZOS)
        if texture_format in ASTC_FORMATS:
            block_width, block_height_pixels = ASTC_FORMATS[texture_format]
            block_size = f"{block_width}x{block_height_pixels}"
            mip_png = ASTC_TEMP / f"{png.stem}_{level}.png"
            mip_astc = ASTC_TEMP / f"{png.stem}_{level}.astc"
            mip.save(mip_png)
            subprocess.run(
                [str(ASTCENC), "-cl", str(mip_png), str(mip_astc), block_size, "-fastest"],
                check=True,
                stdout=subprocess.DEVNULL,
            )
            encoded = mip_astc.read_bytes()
            if encoded[:4] != bytes.fromhex("13ABA15C"):
                raise ValueError(f"astcenc produced an invalid file for {png.name}")
            tiled = tile_astc(
                encoded[16:], width, height, block_width,
                block_height_pixels, texture_format,
            )
            encoded_size = sum(len(item) for item in levels)
            if encoded_size + len(tiled) > len(original_payload):
                levels.append(original_payload[encoded_size:])
                break
            levels.append(tiled)
        else:
            raise ValueError(f"unsupported custom texture format 0x{texture_format:02X}")
    return b"".join(levels)


def replace_textures(files) -> int:
    """Encode every PNG in TEXTURES_DIR into its fixed section-65 allocation."""
    pngs = sorted(TEXTURES_DIR.glob("*.png")) if TEXTURES_DIR.is_dir() else []
    if not pngs:
        return 0
    records = texture_records(files[0], files[63])
    for png in pngs:
        texture_hash = int(png.stem, 16)
        if texture_hash not in records:
            raise ValueError(f"PNG texture {texture_hash:08X} has no Global allocation")
        header, image = records[texture_hash]
        header_bytes = files[63][header.offset : header.offset + header.size]
        original = bytes(files[65][image.offset : image.offset + image.size])
        payload = encode_texture(png, header_bytes[12], original)
        if len(payload) != image.size:
            raise ValueError(
                f"texture {png.stem} encoded size {len(payload)} != allocation {image.size}"
            )
        files[65][image.offset : image.offset + image.size] = payload
        print(
            f"replaced texture {png.stem} (format 0x{header_bytes[12]:02X}, "
            f"{len(payload)} bytes)"
        )
    return len(pngs)


def material_bindings(file52, model):
    """Return each mesh's material record range via the validated B007 scan."""
    b003 = find_chunk(model, 0xB003)
    b006 = find_chunk(model, 0xB006)
    b007 = find_chunk(model, 0xB007)
    mesh_count = b003.size // 0x40
    marker = bytes.fromhex(
        "FFFFFFFFFFFFFFFFFFFFFFFF00000000FFFFFFFFFFFFFFFF00000000"
    )
    raw = []
    position = b007.offset
    while len(raw) < mesh_count and position + len(marker) <= b007.offset + b007.size:
        if file52[position : position + len(marker)] == marker:
            stored_position = position - 4
            relative = struct.unpack_from("<I", file52, stored_position)[0] - 8
            raw.append((stored_position, relative))
        position += 4
    if len(raw) != mesh_count:
        raise ValueError("could not resolve every mesh material binding")
    boundaries = sorted({relative for _position, relative in raw} | {b006.size})
    return [
        {
            "binding": stored_position,
            "relative": relative,
            "offset": b006.offset + relative,
            "size": min(value for value in boundaries if value > relative) - relative,
        }
        for stored_position, relative in raw
    ]


def copy_materials(files, models) -> None:
    """Deep-copy source meshes' material records over target meshes' records."""
    for slot, pairs in sorted(MATERIAL_COPIES.items()):
        bindings = material_bindings(files[52], models[slot])
        for target_mesh, source_mesh in pairs:
            source = bindings[source_mesh]
            target = bindings[target_mesh]
            template = bytes(
                files[52][source["offset"] : source["offset"] + source["size"]]
            )
            if len(template) > target["size"]:
                discarded = template[target["size"]:]
                if any(discarded):
                    raise ValueError(
                        f"slot {slot} mesh {source_mesh} material has nonzero data "
                        f"beyond mesh {target_mesh}'s {target['size']:#x}-byte record"
                    )
            material_size = min(len(template), target["size"])
            start = target["offset"]
            files[52][start : start + material_size] = template[:material_size]
            if material_size < target["size"]:
                files[52][start + material_size : start + target["size"]] = bytes(
                    target["size"] - material_size
                )
            print(
                f"slot {slot}: copied mesh {source_mesh} material over mesh "
                f"{target_mesh} ({material_size:#x} of {target['size']:#x} bytes)"
            )


def collect_mesh_records(file52, model):
    """Return per-mesh (descriptor, b004_cursor, skinned) before any patching."""
    b003 = find_chunk(model, 0xB003)
    b004 = find_chunk(model, 0xB004)
    cursor = b004.offset
    records = []
    for index in range(b003.size // 0x40):
        descriptor = b003.offset + index * 0x40
        marker = struct.unpack_from("<I", file52, descriptor + 0x28)[0]
        records.append((descriptor, cursor, marker != 0xFFFFFFFF))
        cursor += 16 if marker != 0xFFFFFFFF else 12
    if cursor != b004.offset + b004.size:
        raise AssertionError("B004 walk does not consume the whole chunk")
    return records


def build_mesh(files, slot, model, b005, mesh_records, mesh_index, name,
               mesh_data, id_to_hash, hash_to_local, chunk, append_stream):
    """Build and append one replacement mesh's streams; patch section 52."""
    bone_parents = mesh_data.get("_bone_parents", {})
    descriptor, b004_cursor, skinned = mesh_records[mesh_index]
    if not skinned:
        raise ValueError(f"slot {slot} mesh {mesh_index} is unskinned")
    mesh_hash = struct.unpack_from("<I", files[52], descriptor)[0]
    old_index_offset, old_index_flags, old_vertex_count = struct.unpack_from(
        "<III", files[52], descriptor + 4
    )
    old_index_width = 1 if old_index_flags >> 24 == 0x80 else 2
    old_skin_off, old_vertex_off, old_aux_off, old_sent_off = struct.unpack_from(
        "<IIII", files[52], b004_cursor
    )
    aux_size = old_sent_off - old_aux_off
    old_sentinel = bytes(
        files[54][
            b005.offset + old_sent_off : b005.offset + old_sent_off + old_vertex_count * 8
        ]
    )
    sentinel_uniform = all(
        old_sentinel[i : i + 8] == old_sentinel[:8]
        for i in range(0, len(old_sentinel), 8)
    )

    source = mesh_data[name]
    positions = source["positions"]
    normals = source["normals"]
    uvs = source["uvs"]
    faces = source["faces"]
    weights = source["weights"]
    if Y_UP_TO_Z_UP:
        positions = [[p[0], -p[2], p[1]] for p in positions]
        normals = [[n[0], -n[2], n[1]] for n in normals]
    if any(TEST_POSITION_OFFSET):
        positions = [
            [p[0] + TEST_POSITION_OFFSET[0], p[1] + TEST_POSITION_OFFSET[1],
             p[2] + TEST_POSITION_OFFSET[2]]
            for p in positions
        ]
        print(f"    {name}: applied TEST_POSITION_OFFSET {TEST_POSITION_OFFSET}")
    count = len(positions)
    if not (count == len(normals) == len(uvs) == len(weights)):
        raise ValueError(f"{name} vertex attributes have inconsistent lengths")
    if any(len(face) != 3 for face in faces):
        raise ValueError(f"{name} contains a non-triangle face")
    if count > 0xFFFF:
        raise ValueError(f"{name} exceeds 16-bit index range")

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

    for label, points in (("vanilla", old_positions), ("replacement", positions)):
        los = [min(p[axis] for p in points) for axis in range(3)]
        his = [max(p[axis] for p in points) for axis in range(3)]
        print(
            f"    slot {slot} {name} {label} bbox: "
            + " ".join(f"{lo:+.3f}..{hi:+.3f}" for lo, hi in zip(los, his))
        )

    nearest = []
    for i in range(count):
        px, py, pz = positions[i]
        best = 0
        best_distance = None
        for j, (ox, oy, oz) in enumerate(old_positions):
            distance = (ox - px) ** 2 + (oy - py) ** 2 + (oz - pz) ** 2
            if best_distance is None or distance < best_distance:
                best_distance = distance
                best = j
        nearest.append(best)

    fallback_vertices = 0
    skins = []
    for i in range(count):
        resolved = resolve_vertex_bones(weights[i], id_to_hash, hash_to_local, bone_parents)
        if resolved is None:
            resolved = skin_from_original_record(
                files[54], b005.offset + old_skin_off, nearest[i]
            )
            fallback_vertices += 1
        skins.append(resolved)
    order = sorted(range(count), key=lambda i: skins[i][2])
    remap = [0] * count
    for new_index, old_index in enumerate(order):
        remap[old_index] = new_index
    group_counts = [0, 0, 0, 0]
    for _ids, _weights, influences in skins:
        group_counts[influences - 1] += 1
    if sum(group_counts) != count:
        raise AssertionError(f"{name} influence group counts do not sum to vertex count")

    vertex_payload = bytearray()
    skin_payload = bytearray()
    nearest_by_new = []
    for new_index in range(count):
        i = order[new_index]
        best = nearest[i]
        nearest_by_new.append(best)
        record = bytearray(old_vertices[best])
        struct.pack_into("<fff", record, 0, *positions[i])
        struct.pack_into("<f", record, 0x0C, uvs[i][0])
        struct.pack_into("<fff", record, 0x10, *normals[i])
        struct.pack_into("<f", record, 0x1C, 1.0 - uvs[i][1])
        vertex_payload.extend(record)
        ids, repaired, _influences = skins[i]
        skin_payload.extend(struct.pack("<BBBBffff", *ids, *repaired))

    flat_indices = [remap[index] for face in faces for index in face]
    if max(flat_indices) >= count:
        raise ValueError(f"{name} contains an invalid vertex index")
    index_payload = struct.pack(f"<{len(flat_indices)}H", *flat_indices)

    if sentinel_uniform:
        sentinel_payload = old_sentinel[:8] * count
        sentinel_mode = "uniform pattern"
    else:
        sentinel_payload = b"".join(
            old_sentinel[nearest_index * 8 : nearest_index * 8 + 8]
            for nearest_index in nearest_by_new
        )
        sentinel_mode = "nearest-original replication"

    aux_bytes = bytes(files[54][b005.offset + old_aux_off : b005.offset + old_sent_off])
    new_skin_off = append_stream(bytes(skin_payload))
    new_vertex_off = append_stream(bytes(vertex_payload))
    new_aux_off = append_stream(aux_bytes + sentinel_payload)
    new_sent_off = new_aux_off + aux_size
    new_index_off = append_stream(index_payload)

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
    morph_field = struct.unpack_from("<I", files[52], descriptor + 0x20)[0]
    morph_count, morph_base = morph_field & 0xFFFF, morph_field >> 16
    if DISABLE_MORPH_TARGETS and morph_count:
        struct.pack_into("<I", files[52], descriptor + 0x20, morph_base << 16)
        morph_note = f", disabled {morph_count} morph targets"
    else:
        morph_note = ""
    width_note = " (8->16-bit indices)" if old_index_width == 1 else ""
    fallback_note = (
        f", {fallback_vertices} weightless vertices used nearest-original weights"
        if fallback_vertices
        else ""
    )
    print(
        f"slot {slot} mesh {mesh_index} ({mesh_hash:08X}) <- {name}: "
        f"{count} vertices ({old_vertex_count} original), {len(faces)} triangles, "
        f"influence groups {tuple(group_counts)}, sentinel {sentinel_mode}"
        f"{width_note}{fallback_note}{morph_note}"
    )
    return dict(
        slot=slot,
        mesh_index=mesh_index,
        name=name,
        count=count,
        index_count=len(flat_indices),
        group_counts=list(group_counts),
        skin=new_skin_off,
        vertex=new_vertex_off,
        aux=new_aux_off,
        sentinel=new_sent_off,
        index=new_index_off,
        sentinel_payload=bytes(sentinel_payload),
    )


def main() -> None:
    dictionary, data, entries, table_offset, compressed = read_archive(CLEAN)
    if not compressed:
        raise ValueError("expected a compressed Global archive")
    files = {
        index: bytearray(decompress_entry(data, entries[index], compressed))
        for index in (0, 52, 53, 54, 63, 65)
    }
    clean_section_54_len = len(files[54])
    clean_section_54 = bytes(files[54])
    models = group_models(parse_subentries(files[0]))
    mesh_data = json.loads(MESH_JSON.read_text(encoding="utf-8"))

    built = []
    chunk_edits = []
    slot_expectations = []
    new_chunks = {}
    for slot in sorted(TARGETS):
        model = models[slot]
        b005 = find_chunk(model, 0xB005)
        mesh_records = collect_mesh_records(files[52], model)
        id_to_hash = skeleton_id_to_hash(
            files[53], skeleton_groups(files[0])[SKELETON_GROUP_FOR_SLOT[slot]]
        )
        b103 = find_chunk(model, 0xB103)
        hashes = [
            struct.unpack_from("<I", files[52], b103.offset + offset)[0]
            for offset in range(0, b103.size, 4)
        ]
        hash_to_local = {bone_hash: index for index, bone_hash in enumerate(hashes)}

        chunk = bytearray(files[54][b005.offset : b005.offset + b005.size])
        if len(chunk) != b005.size:
            raise AssertionError(f"failed to copy slot {slot}'s vanilla B005 chunk")

        def append_stream(payload: bytes) -> int:
            start = align_up(len(chunk), STREAM_ALIGN)
            chunk.extend(bytes(start - len(chunk)))
            chunk.extend(payload)
            return start

        for mesh_index in sorted(TARGETS[slot]):
            built.append(
                build_mesh(
                    files, slot, model, b005, mesh_records, mesh_index,
                    TARGETS[slot][mesh_index], mesh_data, id_to_hash,
                    hash_to_local, chunk, append_stream,
                )
            )

        new_chunks[slot] = bytes(chunk)

    # Freed-region placement. Once the TARGETS slots are relocated and the
    # MIRROR_SLOTS records are redirected, their vanilla B005 regions are dead
    # space -- nothing else references them (verified against section 0 AND
    # every patch chunk-table copy). The grown chunks are placed inside that
    # dead space so section 54 keeps its exact vanilla size; appending past the
    # vanilla end would overlay the 1.4 update's own relocated models at
    # 0xB1E1EE0..0xBA63260 and explode them in-game.
    freed = sorted(
        (find_chunk(models[slot], 0xB005).offset, find_chunk(models[slot], 0xB005).size)
        for slot in set(TARGETS) | set(MIRROR_SLOTS)
    )
    spans = []
    for offset, size in freed:
        if spans and spans[-1][0] + spans[-1][1] == offset:
            spans[-1][1] += size
        else:
            spans.append([offset, size])
    freed_spans = [(offset, size) for offset, size in spans]
    for slot in sorted(new_chunks):
        chunk = new_chunks[slot]
        b005 = find_chunk(models[slot], 0xB005)
        placed = None
        for span in spans:
            start = align_up(span[0], STREAM_ALIGN)
            end = start + len(chunk)
            if end <= span[0] + span[1]:
                placed = start
                span[1] = span[0] + span[1] - end
                span[0] = end
                break
        if placed is None:
            raise ValueError(
                f"slot {slot}'s new B005 chunk (0x{len(chunk):X} bytes) does not "
                "fit in the freed vanilla chunk regions, and growing section 54 "
                "is not an option (the 1.4 update occupies the address space past "
                "the vanilla end -- see EXPANSION_WORKFLOW.md)"
            )
        files[54][placed : placed + len(chunk)] = chunk
        struct.pack_into("<II", files[0], b005.table_offset + 4, len(chunk), placed)
        chunk_edits.append((0xB005, b005.size, b005.offset, len(chunk), placed))
        slot_expectations.append(
            dict(slot=slot, old_offset=b005.offset, old_size=b005.size,
                 new_offset=placed, new_size=len(chunk))
        )
        print(
            f"slot {slot}: B005 chunk relocated 0x{b005.offset:X} -> 0x{placed:X} "
            f"(freed-region reuse), size 0x{b005.size:X} -> 0x{len(chunk):X}"
        )

    if len(files[54]) != clean_section_54_len:
        raise AssertionError("section 54 changed size; freed-region placement failed")
    print(
        f"section 54: size unchanged ({clean_section_54_len} bytes); "
        f"{len(new_chunks)} chunks placed inside the freed regions"
    )

    # Mirror slots: redirect every chunk record of the mirror slot at the
    # source slot's chunks (using the source's CURRENT records, i.e. after the
    # B005 relocation above).
    current_models = group_models(parse_subentries(files[0]))
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
                # Model-name pointer: its offsets differ between section 0 and
                # the patch table (the patch's name blob is a superset), and the
                # name is an identity key, not renderable content. Keep each
                # mirror slot's own name.
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
                "<II", files[0], record.table_offset + 4,
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

    copy_materials(files, current_models)

    texture_count = replace_textures(files)
    changed_sections = (0, 52, 54, 65) if texture_count else (0, 52, 54)

    output_data = bytearray(data)
    for index in changed_sections:
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
            CLEAN.with_suffix(".patch"), clean_section_54_len, len(files[54]), chunk_edits
        )
    )

    verify(bytes(data), files, clean_section_54, freed_spans, new_chunks,
           slot_expectations, built, changed_sections, chunk_edits)
    print(f"wrote expanded Global archive ({len(built)} meshes, "
          f"{len(slot_expectations)} slots, {len(MIRROR_SLOTS)} mirrors, "
          f"{texture_count} textures) to {OUTPUT}")


def verify(clean_data, files, clean_section_54, freed_spans, new_chunks,
           slot_expectations, built, changed_sections, chunk_edits) -> None:
    emitted_dict, emitted_data, emitted_entries, emitted_table, emitted_compressed = (
        read_archive(OUTPUT / "global.dict")
    )
    if bytes(emitted_data[: len(clean_data)]) != clean_data:
        raise AssertionError("vanilla global.data bytes were modified")
    for index in changed_sections:
        actual = decompress_entry(emitted_data, emitted_entries[index], emitted_compressed)
        if actual != bytes(files[index]):
            raise AssertionError(f"entry {index} did not round-trip")
    for index, entry in enumerate(emitted_entries):
        if index in changed_sections:
            continue
        if entry.offset + entry.compressed_size > len(clean_data):
            raise AssertionError(f"entry {index} unexpectedly points beyond vanilla data")
    table = decompress_entry(emitted_data, emitted_entries[0], emitted_compressed)
    section52 = decompress_entry(emitted_data, emitted_entries[52], emitted_compressed)
    section54 = decompress_entry(emitted_data, emitted_entries[54], emitted_compressed)
    if len(section54) != len(clean_section_54):
        raise AssertionError("emitted section 54 changed size")
    cursor = 0
    for span_offset, span_size in sorted(freed_spans):
        if section54[cursor:span_offset] != clean_section_54[cursor:span_offset]:
            raise AssertionError("section 54 was modified outside the freed regions")
        cursor = span_offset + span_size
    if section54[cursor:] != clean_section_54[cursor:]:
        raise AssertionError("section 54 was modified outside the freed regions")
    models = group_models(parse_subentries(table))
    chunks = {}
    for expectation in slot_expectations:
        slot = expectation["slot"]
        b005 = find_chunk(models[slot], 0xB005)
        if (b005.offset, b005.size) != (expectation["new_offset"], expectation["new_size"]):
            raise AssertionError(f"slot {slot} B005 record does not reference its new chunk")
        start, end = b005.offset, b005.offset + b005.size
        if not any(
            start >= span_offset and end <= span_offset + span_size
            for span_offset, span_size in freed_spans
        ):
            raise AssertionError(f"slot {slot} chunk lies outside the freed regions")
        chunk = section54[start:end]
        if chunk != new_chunks[slot]:
            raise AssertionError(f"slot {slot} relocated chunk content mismatch")
        if chunk[: expectation["old_size"]] != clean_section_54[
            expectation["old_offset"] : expectation["old_offset"] + expectation["old_size"]
        ]:
            raise AssertionError(f"slot {slot} relocated chunk lost its vanilla prefix")
        chunks[slot] = chunk
    for item in built:
        slot = item["slot"]
        chunk = chunks[slot]
        records = collect_mesh_records(section52, models[slot])
        descriptor, b004_cursor, skinned = records[item["mesh_index"]]
        if not skinned:
            raise AssertionError(f"slot {slot} mesh {item['mesh_index']} lost skinned marker")
        index_offset, index_flags, vertex_count = struct.unpack_from(
            "<III", section52, descriptor + 4
        )
        counts_lo, counts_hi = struct.unpack_from("<II", section52, descriptor + 0x28)
        stored_counts = [
            counts_lo & 0xFFFF, counts_lo >> 16, counts_hi & 0xFFFF, counts_hi >> 16,
        ]
        if (index_offset, index_flags, vertex_count) != (
            item["index"], item["index_count"], item["count"]
        ):
            raise AssertionError(f"slot {slot} mesh {item['mesh_index']} descriptor mismatch")
        if stored_counts != item["group_counts"]:
            raise AssertionError(f"slot {slot} mesh {item['mesh_index']} counts mismatch")
        if DISABLE_MORPH_TARGETS:
            morph_field = struct.unpack_from("<I", section52, descriptor + 0x20)[0]
            if morph_field & 0xFFFF:
                raise AssertionError(
                    f"slot {slot} mesh {item['mesh_index']} morph targets not disabled"
                )
        if struct.unpack_from("<IIII", section52, b004_cursor) != (
            item["skin"], item["vertex"], item["aux"], item["sentinel"]
        ):
            raise AssertionError(f"slot {slot} mesh {item['mesh_index']} B004 mismatch")
        accumulator = 0
        for group_index, group_count in enumerate(stored_counts):
            for vertex in range(accumulator, accumulator + group_count):
                vertex_weights = struct.unpack_from(
                    "<4f", chunk, item["skin"] + vertex * 0x14 + 4
                )
                influences = sum(1 for weight in vertex_weights if weight > 0.0)
                if influences != group_index + 1:
                    raise AssertionError(
                        f"slot {slot} mesh {item['mesh_index']} vertex {vertex} has "
                        f"{influences} influences, expected {group_index + 1}"
                    )
            accumulator += group_count
        indices = struct.unpack_from(f"<{item['index_count']}H", chunk, item["index"])
        if max(indices) >= vertex_count:
            raise AssertionError(
                f"slot {slot} mesh {item['mesh_index']} index references a missing vertex"
            )
        if item["sentinel"] + vertex_count * 8 > len(chunk):
            raise AssertionError(
                f"slot {slot} mesh {item['mesh_index']} sentinel exceeds the chunk"
            )
        if chunk[item["sentinel"] : item["sentinel"] + vertex_count * 8] != item[
            "sentinel_payload"
        ]:
            raise AssertionError(f"slot {slot} mesh {item['mesh_index']} sentinel mismatch")
    stored_largest = struct.unpack_from("<I", emitted_dict, 8)[0]
    actual_largest = max(entry.compressed_size for entry in emitted_entries)
    if stored_largest != actual_largest:
        raise AssertionError("dictionary largest-compressed-size field is stale")
    # Mirror slots: the emitted table must show every mirror slot's chunk
    # records exactly matching the source slot's.
    for mirror_slot, source_slot in sorted(MIRROR_SLOTS.items()):
        mirror_records = [
            (record.kind, record.size, record.offset)
            for record in models[mirror_slot]
            if record.kind != 0xB100
        ]
        source_records = [
            (record.kind, record.size, record.offset)
            for record in models[source_slot]
            if record.kind != 0xB100
        ]
        if sorted(mirror_records) != sorted(source_records):
            raise AssertionError(
                f"slot {mirror_slot} does not exactly mirror slot {source_slot}"
            )
    # Emitted global.patch: each chunk-table copy must equal the vanilla table
    # with exactly our record edits applied.
    emitted_patch = (OUTPUT / "global.patch").read_bytes()
    vanilla_patch = CLEAN.with_suffix(".patch").read_bytes()
    if len(emitted_patch) != len(vanilla_patch):
        raise AssertionError("emitted global.patch changed length")
    if emitted_patch[:PATCH_TABLE_FIRST] != vanilla_patch[:PATCH_TABLE_FIRST]:
        raise AssertionError(
            "global.patch header changed despite unchanged section sizes"
        )
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
        emitted_table = zlib.decompress(emitted_patch[start : start + PATCH_TABLE_STRIDE])
        if emitted_table != expected_table:
            raise AssertionError(f"patch table copy {copy} does not match the edits")
    print(
        "verification passed: vanilla data intact, sections round-trip, "
        "structure valid, mirrors exact, patch tables updated"
    )


if __name__ == "__main__":
    main()
