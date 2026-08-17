"""Build the supplied Mario FBX/texture replacement for Story Luigi slots."""

from __future__ import annotations

import struct
import subprocess
import sys
import json
import math
import os
from pathlib import Path

from PIL import Image


HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path[:0] = [str(ROOT / "src-tauri" / "misc"), str(ROOT / "tmp" / "ml3")]
from lm3_import_mummigi import texture_records
from lm3_slot_swap import (
    decompress_entry, group_models, parse_subentries, read_archive, replace_entry,
)

CLEAN = ROOT / "tmp" / "ml3" / "clean" / "global.dict"
ASSET_ROOT = ROOT / "tmp" / "ml3" / "Mario LM3"
TEXTURES = ASSET_ROOT / "tex"
# TEXTURE_REDIRECTS = TEXTURES / "textures.json"
# REPLACEMENT_RULES = TEXTURES / "replacement_rules.json"
TEXTURE_REDIRECTS = HERE / "config/textures.json"
REPLACEMENT_RULES = HERE / "config/replacement_rules.json"
ASTCENC = "astcenc-avx2.exe"
TEMP = ROOT / "tmp" / "ml3" / "mario_astc_temp"
OUTPUT = ROOT / "tmp" / "ml3" / "_mods" / "mario_fbx_replacement" / "romfs"
TARGETS = (27, 28, 29, 30)
BLENDER_UP_OFFSET = 0.128029
MESH_DATA = ASSET_ROOT / "Mario_to_luigi3.meshes.json"
SKELETON_GROUP_FOR_SLOT = {27: 27, 28: 27, 29: 28, 30: 28}

MESH_HASHES = {
    "submesh_7": {0x40243E90, 0xE4882C37},
    "submesh_14": {0xA92A6B5E, 0xCE77ED18},
}


def mesh_record(file52, model, mesh_index):
    b003 = next(record for record in model if record.kind == 0xB003)
    b004 = next(record for record in model if record.kind == 0xB004)
    b005 = next(record for record in model if record.kind == 0xB005)
    cursor = b004.offset
    for index in range(mesh_index):
        descriptor = b003.offset + index * 0x40
        cursor += 16 if struct.unpack_from("<I", file52, descriptor + 0x28)[0] != 0xFFFFFFFF else 12
    descriptor = b003.offset + mesh_index * 0x40
    size = 16 if struct.unpack_from("<I", file52, descriptor + 0x28)[0] != 0xFFFFFFFF else 12
    return descriptor, cursor, size, b005


def find_mesh(file52, model, hashes):
    b003 = next(record for record in model if record.kind == 0xB003)
    for index in range(b003.size // 0x40):
        value = struct.unpack_from("<I", file52, b003.offset + index * 0x40)[0]
        if value in hashes:
            return index
    raise ValueError(f"model lacks expected mesh hashes {[f'{h:08X}' for h in hashes]}")


def align_up(value, alignment=16):
    return (value + alignment - 1) & -alignment


def free_mesh_ranges(file52, model, replaced_indices):
    """Return unused B005-relative ranges after releasing replaced meshes."""
    b003 = next(record for record in model if record.kind == 0xB003)
    b005 = next(record for record in model if record.kind == 0xB005)
    occupied = []
    for mesh_index in range(b003.size // 0x40):
        descriptor, b004, b004_size, _ = mesh_record(file52, model, mesh_index)
        index_offset, index_flags, vertex_count = struct.unpack_from(
            "<III", file52, descriptor + 4
        )
        index_width = 1 if index_flags >> 24 == 0x80 else 2
        index_count = index_flags & 0xFFFFFF
        if mesh_index not in replaced_indices:
            occupied.append((index_offset, index_offset + index_count * index_width))
            if b004_size == 16:
                skin_offset, vertex_offset = struct.unpack_from("<II", file52, b004)
                occupied.append((skin_offset, skin_offset + vertex_count * 0x14))
            else:
                vertex_offset = struct.unpack_from("<I", file52, b004)[0]
            occupied.append((vertex_offset, vertex_offset + vertex_count * 0x30))
    occupied.sort()
    merged = []
    for start, end in occupied:
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    free = []
    cursor = 0
    for start, end in merged:
        if cursor < start:
            free.append([cursor, start])
        cursor = max(cursor, end)
    if cursor < b005.size:
        free.append([cursor, b005.size])
    return free


def allocate_range(free, size, alignment=16):
    for region in free:
        start = align_up(region[0], alignment)
        if start + size <= region[1]:
            region[0] = start + size
            return start
    raise ValueError(f"mesh buffer has no free range for {size} bytes")


def reserve_range(free, start, size):
    end = start + size
    for index, (region_start, region_end) in enumerate(free):
        if region_start <= start and end <= region_end:
            replacement = []
            if region_start < start:
                replacement.append([region_start, start])
            if end < region_end:
                replacement.append([end, region_end])
            free[index:index + 1] = replacement
            return
    raise ValueError(f"mesh buffer range {start}:{end} is unavailable")


def move_source_mesh_up(files, model, mesh_index):
    descriptor, b004, b004_size, b005 = mesh_record(files[52], model, mesh_index)
    if b004_size != 16:
        raise ValueError(f"slot 34 mesh {mesh_index} is unexpectedly unskinned")
    vertex_count = struct.unpack_from("<I", files[52], descriptor + 0x0C)[0]
    vertex_offset = struct.unpack_from("<I", files[52], b004 + 4)[0]
    vertex_base = b005.offset + vertex_offset
    for index in range(vertex_count):
        position = vertex_base + index * 0x30
        z = struct.unpack_from("<f", files[54], position + 8)[0]
        struct.pack_into("<f", files[54], position + 8, z + BLENDER_UP_OFFSET)
    print(
        f"moved slot 34 mesh {mesh_index} ({vertex_count} vertices) "
        f"up +{BLENDER_UP_OFFSET} m on Blender Z"
    )


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


def skeleton_id_to_hash(files, group):
    record = next(item for item in group if item.kind == 0x7105)
    result = {}
    for offset in range(record.offset, record.offset + record.size, 8):
        bone_hash, bone_id = struct.unpack_from("<II", files[53], offset)
        result[bone_id] = bone_hash
    return result


def skin_from_fbx(vertex_weights, model, id_to_hash, file52, preserve_weights=False):
    b103 = next(record for record in model if record.kind == 0xB103)
    hashes = [struct.unpack_from("<I", file52, b103.offset + offset)[0]
              for offset in range(0, b103.size, 4)]
    hash_to_b103 = {bone_hash: index for index, bone_hash in enumerate(hashes)}
    output = bytearray()
    for vertex_index, influences in enumerate(vertex_weights):
        resolved = []
        for name, weight in influences:
            if not name.startswith("bone_"):
                continue
            bone_id = int(name[5:])
            if bone_id not in id_to_hash:
                raise ValueError(f"FBX bone {name} is missing from target skeleton")
            bone_hash = id_to_hash[bone_id]
            if bone_hash not in hash_to_b103:
                raise ValueError(
                    f"FBX bone {name}/{bone_hash:08X} is missing from target B103"
                )
            resolved.append((hash_to_b103[bone_hash], float(weight)))
        resolved = [(bone, weight) for bone, weight in resolved
                    if math.isfinite(weight) and weight > 1e-8]
        resolved.sort(key=lambda item: item[1], reverse=True)
        if preserve_weights and len(resolved) > 2:
            raise ValueError(
                f"FBX vertex {vertex_index} has {len(resolved)} usable weights; "
                "preserved-weight meshes permit at most two"
            )
        if not preserve_weights:
            resolved = resolved[:2]
        total = sum(weight for _index, weight in resolved)
        if total <= 1e-8:
            raise ValueError(f"FBX vertex {vertex_index} has no usable skin weight")
        ids = [item[0] for item in resolved] + [0] * (4 - len(resolved))
        # Keep at most the two strongest bones. Redistribute every discarded
        # influence between them in their existing proportion (equivalent to
        # normalizing the retained set), then repair float32 rounding on the
        # dominant influence only.
        if preserve_weights:
            if abs(total - 1.0) > 1e-4:
                raise ValueError(
                    f"FBX vertex {vertex_index} preserved weights sum to {total}, not 1.0"
                )
            repaired = [weight for _index, weight in resolved]
        elif len(resolved) == 1:
            repaired = [1.0]
        else:
            dominant = struct.unpack(
                "<f", struct.pack("<f", resolved[0][1] / total)
            )[0]
            # Store the second float as the exact complement of the dominant
            # float. This guarantees their decoded sum is 1.0 while retaining
            # the original ratio as closely as float32 permits.
            secondary = struct.unpack("<f", struct.pack("<f", 1.0 - dominant))[0]
            repaired = [dominant, secondary]
        repaired += [0.0] * (4 - len(repaired))
        output.extend(struct.pack("<BBBBffff", *ids, *repaired))
    return bytes(output)


def rigid_skin_from_original(payload, original_vertex_count, new_vertex_count):
    totals = {}
    for vertex in range(original_vertex_count):
        position = vertex * 0x14
        ids = payload[position : position + 4]
        weights = struct.unpack_from("<ffff", payload, position + 4)
        for bone_id, weight in zip(ids, weights):
            if weight > 0.0:
                totals[bone_id] = totals.get(bone_id, 0.0) + weight
    if not totals:
        raise ValueError("original target mesh has no usable skin weights")
    dominant = max(totals, key=totals.get)
    record = struct.pack("<BBBBffff", dominant, 0, 0, 0, 1.0, 0.0, 0.0, 0.0)
    return record * new_vertex_count, dominant


def skin_from_nearest_original(
    file54, absolute_skin_offset, nearest_vertices, fallback_record=None
):
    output = bytearray()
    for source_vertex in nearest_vertices:
        offset = absolute_skin_offset + source_vertex * 0x14
        ids = struct.unpack_from("<BBBB", file54, offset)
        weights = struct.unpack_from("<ffff", file54, offset + 4)
        influences = sorted(
            ((bone_id, weight) for bone_id, weight in zip(ids, weights)
             if math.isfinite(weight) and weight > 1e-8),
            key=lambda item: item[1], reverse=True,
        )[:2]
        total = sum(weight for _bone_id, weight in influences)
        if total <= 1e-8:
            if fallback_record is None:
                raise ValueError(f"original vertex {source_vertex} has no usable skin weight")
            output.extend(fallback_record)
            continue
        if len(influences) == 1:
            repaired = [1.0]
        else:
            dominant = struct.unpack(
                "<f", struct.pack("<f", influences[0][1] / total)
            )[0]
            repaired = [
                dominant,
                struct.unpack("<f", struct.pack("<f", 1.0 - dominant))[0],
            ]
        packed_ids = [item[0] for item in influences] + [0] * (4 - len(influences))
        repaired += [0.0] * (4 - len(repaired))
        output.extend(struct.pack("<BBBBffff", *packed_ids, *repaired))
    return bytes(output)


def rigid_skin_for_skeleton_bone(model, id_to_hash, file52, bone_id, vertex_count):
    if bone_id not in id_to_hash:
        raise ValueError(f"bone_{bone_id} is missing from the target skeleton")
    b103 = next(record for record in model if record.kind == 0xB103)
    hashes = [
        struct.unpack_from("<I", file52, b103.offset + offset)[0]
        for offset in range(0, b103.size, 4)
    ]
    bone_hash = id_to_hash[bone_id]
    if bone_hash not in hashes:
        raise ValueError(f"bone_{bone_id}/{bone_hash:08X} is missing from target B103")
    local_id = hashes.index(bone_hash)
    record = struct.pack("<BBBBffff", local_id, 0, 0, 0, 1.0, 0.0, 0.0, 0.0)
    return record * vertex_count, local_id


def replace_u32_in_place(payload, replacements):
    changed = 0
    for offset in range(0, len(payload) - 3, 4):
        value = struct.unpack_from("<I", payload, offset)[0]
        if value in replacements:
            struct.pack_into("<I", payload, offset, replacements[value])
            changed += 1
    return changed


def block_height(width, height):
    result = 8 if width <= 256 or height <= 256 else 16
    result = 4 if width <= 128 or height <= 128 else result
    return 2 if width <= 64 or height <= 64 else result


def block_address(x, y, width_blocks, height):
    width_in_gobs = (width_blocks * 16 + 63) // 64
    gob = ((y // (8 * height)) * 512 * height * width_in_gobs
           + (x * 16 // 64) * 512 * height
           + ((y % (8 * height)) // 8) * 512)
    return (gob + ((x * 16 % 64) // 32) * 256 + ((y % 8) // 2) * 64
            + ((x * 16 % 32) // 16) * 32 + (y % 2) * 16 + (x * 16 % 16))


def tile_astc(linear, width, height):
    width_blocks = (width + 7) // 8
    height_blocks = (height + 4) // 5
    gob_height = block_height(width, height)
    width_gobs = (width_blocks * 16 + 63) // 64
    rows = (height_blocks + 8 * gob_height - 1) // (8 * gob_height)
    output = bytearray(rows * 512 * gob_height * width_gobs)
    for y in range(height_blocks):
        for x in range(width_blocks):
            source = (y * width_blocks + x) * 16
            target = block_address(x, y, width_blocks, gob_height)
            output[target : target + 16] = linear[source : source + 16]
    return bytes(output)


def encode_bc4_block(values):
    high, low = max(values), min(values)
    if high == low:
        palette = [high] * 8
    else:
        palette = [high, low] + [
            round(((7 - step) * high + step * low) / 7) for step in range(1, 7)
        ]
    bits = 0
    for index, value in enumerate(values):
        choice = min(range(8), key=lambda item: abs(palette[item] - value))
        bits |= choice << (index * 3)
    return bytes((high, low)) + bits.to_bytes(6, "little")


def encode_bc5(image):
    width, height = image.size
    pixels = image.convert("RGB").load()
    width_blocks = (width + 3) // 4
    height_blocks = (height + 3) // 4
    linear = bytearray()
    for block_y in range(height_blocks):
        for block_x in range(width_blocks):
            block = [
                pixels[min(block_x * 4 + x, width - 1), min(block_y * 4 + y, height - 1)]
                for y in range(4) for x in range(4)
            ]
            linear.extend(encode_bc4_block([pixel[0] for pixel in block]))
            linear.extend(encode_bc4_block([pixel[1] for pixel in block]))
    gob_height = 1 if width <= 8 and height <= 8 else block_height(width, height)
    width_gobs = (width_blocks * 16 + 63) // 64
    rows = (height_blocks + 8 * gob_height - 1) // (8 * gob_height)
    output = bytearray(rows * 512 * gob_height * width_gobs)
    for y in range(height_blocks):
        for x in range(width_blocks):
            source = (y * width_blocks + x) * 16
            target = block_address(x, y, width_blocks, gob_height)
            output[target:target + 16] = linear[source:source + 16]
    return bytes(output)


def encode_texture(png: Path, texture_format: int, original_payload: bytes):
    TEMP.mkdir(parents=True, exist_ok=True)
    source = Image.open(png).convert("RGBA")
    if texture_format == 0x16:
        base = encode_bc5(source)
        if len(base) > len(original_payload):
            raise ValueError(f"BC5 base level exceeds the allocation for {png.name}")
        # LM3 uses a proprietary packed BC5 mip tail. Preserve that tail and
        # replace only the independently tiled full-resolution level.
        return base + original_payload[len(base):]
    levels = []
    for level in range(8):
        width = max(1, source.width >> level)
        height = max(1, source.height >> level)
        mip = source.resize((width, height), Image.Resampling.LANCZOS)
        if texture_format == 0x1E:
            mip_png = TEMP / f"{png.stem}_{level}.png"
            mip_astc = TEMP / f"{png.stem}_{level}.astc"
            mip.save(mip_png)
            subprocess.run(
                [str(ASTCENC), "-cl", str(mip_png), str(mip_astc), "8x5", "-fastest"],
                check=True, stdout=subprocess.DEVNULL,
            )
            encoded = mip_astc.read_bytes()
            if encoded[:4] != bytes.fromhex("13ABA15C"):
                raise ValueError(f"astcenc produced an invalid file for {png.name}")
            levels.append(tile_astc(encoded[16:], width, height))
        else:
            raise ValueError(f"unsupported custom texture format 0x{texture_format:02X}")
    return b"".join(levels)


def main():
    dictionary, data, entries, table_offset, compressed = read_archive(CLEAN)
    files = {index: bytearray(decompress_entry(data, entries[index], compressed))
             for index in (0, 52, 53, 54, 63, 65)}
    models = group_models(parse_subentries(files[0]))
    mesh_data = json.loads(MESH_DATA.read_text(encoding="utf-8"))
    print(REPLACEMENT_RULES)
    replacement_rules = json.loads(
        REPLACEMENT_RULES.read_text(encoding="utf-8")
    )
    configured_meshes = replacement_rules["mesh_targets"]
    skeletons = skeleton_groups(files[0])
    changed_meshes = 0
    for slot in TARGETS:
        target_indices = {}
        for name, mapping in configured_meshes.items():
            if isinstance(mapping, dict):
                target_indices[name] = int(mapping[str(slot)])
            elif name == "submesh_14" and mapping == "keep_existing_replacement":
                target_indices[name] = find_mesh(
                    files[52], models[slot], MESH_HASHES["submesh_14"]
                )
            else:
                raise ValueError(f"unsupported mesh rule for {name}: {mapping!r}")
        if "submesh_7" in target_indices:
            expected_index = find_mesh(
                files[52], models[slot], MESH_HASHES["submesh_7"]
            )
            if target_indices["submesh_7"] != expected_index:
                raise ValueError(
                    f"replacement_rules.json maps slot {slot} submesh_7 to mesh "
                    f"{target_indices['submesh_7']}, expected mesh {expected_index}"
                )
        free = free_mesh_ranges(files[52], models[slot], set(target_indices.values()))
        id_to_hash = skeleton_id_to_hash(
            files, skeletons[SKELETON_GROUP_FOR_SLOT[slot]]
        )
        for name in configured_meshes:
            if name not in mesh_data:
                raise ValueError(f"replacement FBX extraction lacks {name}")
            source = mesh_data[name]
            positions = source["positions"]
            normals = source["normals"]
            uvs = source["uvs"]
            faces = source["faces"]
            weights = source["weights"]
            if not (len(positions) == len(normals) == len(uvs) == len(weights)):
                raise ValueError(f"{name} vertex attributes have inconsistent lengths")
            if any(len(face) != 3 for face in faces):
                raise ValueError(f"{name} contains a non-triangle face")
            flat_indices = [index for face in faces for index in face]
            if flat_indices and max(flat_indices) >= len(positions):
                raise ValueError(f"{name} contains an invalid vertex index")
            index_payload = struct.pack("<" + "H" * len(flat_indices), *flat_indices)
            target_index = target_indices[name]
            target_descriptor, target_b004, target_size, target_b005 = mesh_record(
                files[52], models[slot], target_index
            )
            if target_size != 16:
                raise ValueError(f"Mario {name} replacement expects a skinned target mesh")
            target_hash = struct.unpack_from("<I", files[52], target_descriptor)[0]
            old_index_offset, old_index_flags, old_vertex_count = struct.unpack_from(
                "<III", files[52], target_descriptor + 4
            )
            old_index_count = old_index_flags & 0xFFFFFF
            index_width = 1 if old_index_flags >> 24 == 0x80 else 2
            old_skin_offset, old_vertex_offset = struct.unpack_from(
                "<II", files[52], target_b004
            )
            if len(positions) > old_vertex_count:
                raise ValueError(
                    f"slot {slot} {name} has {len(positions)} replacement vertices, "
                    f"exceeding the original mesh's {old_vertex_count}; aborting import"
                )
            old_vertices = [
                bytes(files[54][
                    target_b005.offset + old_vertex_offset + i * 0x30:
                    target_b005.offset + old_vertex_offset + (i + 1) * 0x30
                ]) for i in range(old_vertex_count)
            ]
            old_positions = [struct.unpack_from("<fff", record) for record in old_vertices]
            original_transform = replacement_rules.get(
                "original_mesh_transforms", {}
            ).get(name)
            if original_transform is not None:
                if not original_transform.get("use_original_mesh_instead_of_fbx", False):
                    raise ValueError(f"{name} original-mesh transform is not enabled")
                position_offset = original_transform["vertex_position_offset_m"]
                uv_offsets = original_transform[
                    "uv_u_offset_by_original_global_vertex_x_sign"
                ]
                dx = float(position_offset["x"])
                dy = float(position_offset["y"])
                dz = float(position_offset["z"])
                transformed = bytearray()
                for original in old_vertices:
                    record = bytearray(original)
                    x, y, z = struct.unpack_from("<fff", record, 0)
                    u = struct.unpack_from("<f", record, 0x0C)[0]
                    if x > 0.0:
                        du = float(uv_offsets["positive"])
                    elif x < 0.0:
                        du = float(uv_offsets["negative"])
                    else:
                        du = float(uv_offsets.get("zero", 0.0))
                    struct.pack_into("<fff", record, 0, x + dx, y + dy, z + dz)
                    struct.pack_into("<f", record, 0x0C, u + du)
                    transformed.extend(record)
                base = target_b005.offset + old_vertex_offset
                files[54][base:base + len(transformed)] = transformed
                if replacement_rules.get("neutralize_auxiliary_vertex_data", False):
                    auxiliary_offset, sentinel_offset = struct.unpack_from(
                        "<II", files[52], target_b004 + 8
                    )
                    if not auxiliary_offset <= sentinel_offset <= target_b005.size:
                        raise ValueError(
                            f"slot {slot} {name} has invalid auxiliary buffer offsets"
                        )
                    buffer_base = target_b005.offset
                    files[54][
                        buffer_base + auxiliary_offset:buffer_base + sentinel_offset
                    ] = bytes(sentinel_offset - auxiliary_offset)
                    sentinel = bytes.fromhex("FFFFFFFFFF7FFF7F")
                    files[54][
                        buffer_base + sentinel_offset:
                        buffer_base + sentinel_offset + old_vertex_count * len(sentinel)
                    ] = sentinel * old_vertex_count
                changed_meshes += 1
                print(
                    f"slot {slot} {name} (mesh {target_index}, {target_hash:08X}): "
                    f"kept original {old_vertex_count} vertices/topology/weights; "
                    f"position offset ({dx}, {dy}, {dz}); sign-based U offset; "
                    "neutralized auxiliary vertex streams"
                )
                continue
            vertex_payload = bytearray()
            nearest_vertices = []
            for position, normal, uv in zip(positions, normals, uvs):
                nearest = min(
                    range(old_vertex_count),
                    key=lambda i: sum((old_positions[i][axis] - position[axis]) ** 2 for axis in range(3)),
                )
                nearest_vertices.append(nearest)
                record = bytearray(old_vertices[nearest])
                struct.pack_into("<fff", record, 0, *position)
                struct.pack_into("<f", record, 0x0C, uv[0])
                struct.pack_into("<fff", record, 0x10, *normal)
                struct.pack_into("<f", record, 0x1C, 1.0 - uv[1])
                vertex_payload.extend(record)
            rigid_mesh_bones = replacement_rules.get("rigid_mesh_bones", {})
            rigid_bone = rigid_mesh_bones.get(
                name, replacement_rules.get("temporary_rigid_bone")
            )
            preserve_fbx_weights = name in replacement_rules.get(
                "preserve_fbx_weights", []
            )
            if preserve_fbx_weights and rigid_bone is not None:
                raise ValueError(
                    f"{name} cannot preserve FBX weights and use rigid_mesh_bones"
                )
            if preserve_fbx_weights:
                skin_payload = skin_from_fbx(
                    weights, models[slot], id_to_hash, files[52], preserve_weights=True
                )
                weight_mode = "preserved FBX weights (validated, no recalculation)"
            elif rigid_bone is None:
                fallback_record = None
                fallback_bone = replacement_rules.get("fallback_rigid_bone")
                if fallback_bone is not None:
                    fallback_payload, fallback_local = rigid_skin_for_skeleton_bone(
                        models[slot], id_to_hash, files[52], int(fallback_bone), 1
                    )
                    fallback_record = fallback_payload
                skin_payload = skin_from_nearest_original(
                    files[54], target_b005.offset + old_skin_offset,
                    nearest_vertices, fallback_record
                )
                weight_mode = (
                    f"nearest-original weights; bone_{int(fallback_bone)} fallback"
                    if fallback_bone is not None else "nearest-original weights"
                )
            else:
                skin_payload, local_bone = rigid_skin_for_skeleton_bone(
                    models[slot], id_to_hash, files[52], int(rigid_bone), len(positions)
                )
                weight_mode = f"rigid bone_{int(rigid_bone)} (B103 {local_bone})"
            stored_vertex_count = len(positions)
            if (
                replacement_rules.get("pad_to_original_vertex_count", False)
                and name != "submesh_14"
                and len(positions) < old_vertex_count
            ):
                padding = old_vertex_count - len(positions)
                vertex_payload.extend(vertex_payload[:0x30] * padding)
                skin_payload += skin_payload[:0x14] * padding
                stored_vertex_count = old_vertex_count
                weight_mode += f"; padded {padding} inactive vertices"
            # Prefer the mesh's original buffers whenever all new payloads
            # fit. Some LM3 facial meshes have additional runtime/deformation
            # relationships that assume these owned buffer locations even
            # though B003/B004 expose offsets. Relocate only as a fallback.
            index_fits = index_width == 2 and len(index_payload) <= old_index_count * index_width
            skin_fits = len(skin_payload) <= old_vertex_count * 0x14
            vertex_fits = len(vertex_payload) <= old_vertex_count * 0x30
            if index_fits:
                reserve_range(free, old_index_offset, len(index_payload))
            if skin_fits:
                reserve_range(free, old_skin_offset, len(skin_payload))
            if vertex_fits:
                reserve_range(free, old_vertex_offset, len(vertex_payload))
            index_offset = (
                old_index_offset if index_fits else allocate_range(free, len(index_payload))
            )
            skin_offset = (
                old_skin_offset if skin_fits else allocate_range(free, len(skin_payload))
            )
            vertex_offset = (
                old_vertex_offset if vertex_fits else allocate_range(free, len(vertex_payload))
            )
            placement = (
                "original buffers" if index_fits and skin_fits and vertex_fits
                else "relocated " + "/".join(
                    name for name, fits in (
                        ("index", index_fits), ("skin", skin_fits), ("vertex", vertex_fits)
                    ) if not fits
                )
            )
            base = target_b005.offset
            files[54][base + index_offset:base + index_offset + len(index_payload)] = index_payload
            files[54][base + skin_offset:base + skin_offset + len(skin_payload)] = skin_payload
            files[54][base + vertex_offset:base + vertex_offset + len(vertex_payload)] = vertex_payload
            if (
                replacement_rules.get("neutralize_auxiliary_vertex_data", False)
                and name != "submesh_14"
            ):
                auxiliary_offset, sentinel_offset = struct.unpack_from(
                    "<II", files[52], target_b004 + 8
                )
                if not auxiliary_offset <= sentinel_offset <= target_b005.size:
                    raise ValueError(
                        f"slot {slot} {name} has invalid auxiliary buffer offsets"
                    )
                files[54][
                    base + auxiliary_offset:base + sentinel_offset
                ] = bytes(sentinel_offset - auxiliary_offset)
                sentinel = bytes.fromhex("FFFFFFFFFF7FFF7F")
                files[54][
                    base + sentinel_offset:
                    base + sentinel_offset + old_vertex_count * len(sentinel)
                ] = sentinel * old_vertex_count
            struct.pack_into("<III", files[52], target_descriptor + 4,
                             index_offset, len(flat_indices), stored_vertex_count)
            struct.pack_into("<II", files[52], target_b004, skin_offset, vertex_offset)
            changed_meshes += 1
            print(
                f"slot {slot} {name} (mesh {target_index}, {target_hash:08X}): "
                f"{len(positions)} vertices, {len(faces)} triangles, {weight_mode}, "
                f"{placement}"
            )
            if (
                replacement_rules.get("neutralize_auxiliary_vertex_data", False)
                and name != "submesh_14"
            ):
                print(f"slot {slot} {name}: neutralized auxiliary vertex streams")

        deformation_rule = replacement_rules.get("disable_face_deformation", False)
        disable_deformation = (
            bool(deformation_rule.get(str(slot), False))
            if isinstance(deformation_rule, dict) else bool(deformation_rule)
        )
        if disable_deformation:
            purge_all = replacement_rules.get(
                "purge_face_morphs_and_transforms", False
            )
            kinds = (0xB00A, 0xB00B, 0xB00C) if purge_all else (0xB00A, 0xB00B)
            for kind in kinds:
                deformation = next(record for record in models[slot] if record.kind == kind)
                struct.pack_into("<I", files[0], deformation.table_offset + 4, 0)
            label = "B00A/B00B/B00C" if purge_all else "B00A/B00B"
            print(f"slot {slot}: disabled original {label} facial deformation/transforms")

    textures = texture_records(files[0], files[63])
    configured = json.loads(TEXTURE_REDIRECTS.read_text(encoding="utf-8"))
    if not isinstance(configured, dict):
        raise ValueError("textures.json must contain a hash-to-hash object")
    requested_redirects = {
        int(source, 16): int(target, 16) for source, target in configured.items()
    }
    redirects = {
        source: target for source, target in requested_redirects.items()
        if source in textures and target in textures
    }
    redirected_references = 0
    for slot in TARGETS:
        material = next(record for record in models[slot] if record.kind == 0xB006)
        payload = memoryview(files[52])[material.offset:material.offset + material.size]
        redirected_references += replace_u32_in_place(payload, redirects)
    for source, target in sorted(redirects.items()):
        print(f"redirected material texture {source:08X} -> existing Global {target:08X}")
    for source, target in sorted(requested_redirects.items()):
        if source not in redirects:
            missing = []
            if source not in textures:
                missing.append("key")
            if target not in textures:
                missing.append("value")
            print(
                f"texture redirect {source:08X}->{target:08X} missing Global "
                f"{'/'.join(missing)}; using PNG replacement"
            )
    for png in sorted(TEXTURES.glob("*.png")):
        texture_hash = int(png.stem, 16)
        if texture_hash in redirects:
            print(f"skipped PNG {png.name}; materials use {redirects[texture_hash]:08X}")
            continue
        if texture_hash not in textures:
            raise ValueError(f"PNG texture {texture_hash:08X} has no Global allocation")
        header, image = textures[texture_hash]
        header_bytes = files[63][header.offset:header.offset + header.size]
        original_payload = bytes(files[65][image.offset:image.offset + image.size])
        payload = encode_texture(png, header_bytes[12], original_payload)
        if len(payload) != image.size:
            raise ValueError(
                f"texture {png.stem} encoded size {len(payload)} != allocation {image.size}"
            )
        files[65][image.offset : image.offset + image.size] = payload
        print(f"replaced texture {png.stem} ({len(payload)} bytes, 8 ASTC mip levels)")

    for index in (0, 52, 54, 65):
        replace_entry(dictionary, data, entries, table_offset, index,
                      bytes(files[index]), compressed)
    OUTPUT.mkdir(parents=True, exist_ok=True)
    (OUTPUT / "global.dict").write_bytes(dictionary)
    (OUTPUT / "global.data").write_bytes(data)
    (OUTPUT / "global.patch").write_bytes(CLEAN.with_suffix(".patch").read_bytes())
    _, emitted_data, emitted_entries, _, emitted_compressed = read_archive(OUTPUT / "global.dict")
    for index in (0, 52, 54, 65):
        actual = decompress_entry(emitted_data, emitted_entries[index], emitted_compressed)
        if actual != bytes(files[index]):
            raise AssertionError(f"entry {index} did not round-trip")
    print(f"wrote Mario replacement ({changed_meshes} mesh redirects) to {OUTPUT}")


if __name__ == "__main__":
    main()
