"""Export clean LM3 slots with geometry, normals, UVs, skin weights, and skeletons."""

from __future__ import annotations

import argparse
import json
import struct
import subprocess
import sys
from pathlib import Path


HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path[:0] = [str(ROOT / "src-tauri" / "misc"), str(ROOT / "tmp" / "ml3")]
from lm3_slot_swap import decompress_entry, group_models, parse_subentries, read_archive

BLENDER = Path(r"C:\Program Files\Blender Foundation\Blender 4.0\blender.exe")
BRIDGE = HERE / "lm3_full_fbx_blender.py"
CLEAN = ROOT / "tmp" / "ml3" / "clean" / "global.dict"
OUTPUT = ROOT / "tmp" / "ml3" / "clean_tex"
SHARED_SKELETON = {25: 26, 26: 26, 27: 27, 28: 27, 29: 28, 30: 28}


def skeleton_groups(records):
    groups = []
    current = None
    for record in records:
        if record.kind == 0x7101:
            current = []
            groups.append(current)
        if current is not None and 0x7101 <= record.kind <= 0x7106:
            current.append(record)
    return groups


def primary_records(table):
    records = []
    position = 0
    while position + 24 <= len(table) and struct.unpack_from("<H", table, position)[0] == 0x1301:
        records.append(struct.unpack_from("<HHIIHHII", table, position))
        position += 24
    return records


def material_textures(files, model, texture_formats):
    b003 = next(record for record in model if record.kind == 0xB003)
    b006 = next(record for record in model if record.kind == 0xB006)
    b007 = next(record for record in model if record.kind == 0xB007)
    mesh_hashes = [
        struct.unpack_from("<I", files[52], b003.offset + index * 0x40)[0]
        for index in range(b003.size // 0x40)
    ]
    marker = bytes.fromhex("FFFFFFFFFFFFFFFFFFFFFFFF00000000FFFFFFFFFFFFFFFF00000000")
    bindings = []
    position = b007.offset
    while len(bindings) < len(mesh_hashes) and position + 28 <= b007.offset + b007.size:
        if files[52][position:position + 28] == marker:
            bindings.append(struct.unpack_from("<I", files[52], position - 4)[0] - 8)
        position += 4
    if len(bindings) != len(mesh_hashes):
        raise ValueError("could not resolve every mesh material binding")
    boundaries = sorted(set(bindings) | {b006.size})
    result = {}
    for mesh_hash, relative in zip(mesh_hashes, bindings):
        size = min(boundary for boundary in boundaries if boundary > relative) - relative
        payload = files[52][b006.offset + relative:b006.offset + relative + size]
        references = []
        for offset in range(0, len(payload) - 3, 4):
            value = struct.unpack_from("<I", payload, offset)[0]
            if value in texture_formats and value not in references:
                references.append(value)
        diffuse = next((value for value in references if texture_formats[value] not in (0x15, 0x16)), None)
        result[mesh_hash] = diffuse
    return result


def mesh_manifest(files, model, skeleton_hash_to_id, slot, texture_formats):
    b003 = next(record for record in model if record.kind == 0xB003)
    b004 = next(record for record in model if record.kind == 0xB004)
    b005 = next(record for record in model if record.kind == 0xB005)
    b103 = next(record for record in model if record.kind == 0xB103)
    b103_hashes = [
        struct.unpack_from("<I", files[52], b103.offset + offset)[0]
        for offset in range(0, b103.size, 4)
    ]
    result = []
    diffuse_by_mesh = material_textures(files, model, texture_formats)
    b004_cursor = b004.offset
    for mesh_index in range(b003.size // 0x40):
        descriptor = b003.offset + mesh_index * 0x40
        mesh_hash, index_offset, index_flags, vertex_count = struct.unpack_from(
            "<IIII", files[52], descriptor
        )
        skinned = struct.unpack_from("<I", files[52], descriptor + 0x28)[0] != 0xFFFFFFFF
        if skinned:
            skin_offset, vertex_offset = struct.unpack_from("<II", files[52], b004_cursor)
            b004_cursor += 16
        else:
            skin_offset = None
            vertex_offset = struct.unpack_from("<I", files[52], b004_cursor)[0]
            b004_cursor += 12
        index_count = index_flags & 0xFFFFFF
        index_width = 1 if index_flags >> 24 == 0x80 else 2
        index_format = "<B" if index_width == 1 else "<H"
        indices = [
            struct.unpack_from(index_format, files[54], b005.offset + index_offset + i * index_width)[0]
            for i in range(index_count)
        ]
        vertices = []
        for vertex in range(vertex_count):
            offset = b005.offset + vertex_offset + vertex * 0x30
            position = struct.unpack_from("<fff", files[54], offset)
            u = struct.unpack_from("<f", files[54], offset + 0x0C)[0]
            normal = struct.unpack_from("<fff", files[54], offset + 0x10)
            v = struct.unpack_from("<f", files[54], offset + 0x1C)[0]
            weights = []
            if skin_offset is not None:
                skin = b005.offset + skin_offset + vertex * 0x14
                ids = files[54][skin:skin + 4]
                values = struct.unpack_from("<ffff", files[54], skin + 4)
                for local_id, weight in zip(ids, values):
                    if weight <= 0.0 or local_id >= len(b103_hashes):
                        continue
                    bone_hash = b103_hashes[local_id]
                    if bone_hash in skeleton_hash_to_id:
                        weights.append([skeleton_hash_to_id[bone_hash], weight])
            vertices.append({
                "position": position, "normal": normal, "uv": [u, 1.0 - v],
                "weights": weights,
            })
        result.append({
            "name": f"slot_{slot}_mesh_{mesh_index:02d}_{mesh_hash:08X}",
            "diffuse_texture": (
                f"{diffuse_by_mesh[mesh_hash]:08X}.png"
                if diffuse_by_mesh.get(mesh_hash) is not None else None
            ),
            "vertices": vertices,
            "faces": [indices[i:i + 3] for i in range(0, len(indices), 3)],
        })
    return result


def build_manifest(files, slot):
    table = files[0]
    models = group_models(parse_subentries(table))
    primary = primary_records(table)
    model_headers = [record for record in primary if record[4] == 0xB000]
    skeleton_headers = [record for record in primary if record[4] == 0x7100]
    model_hash = struct.unpack_from("<I", files[52], model_headers[slot][3] + 4)[0]
    paired = {
        struct.unpack_from("<I", files[52], record[3] + 4)[0]: index
        for index, record in enumerate(skeleton_headers)
    }
    groups = skeleton_groups(parse_subentries(table))
    if model_hash in paired:
        skeleton_index = paired[model_hash]
    elif slot in SHARED_SKELETON:
        skeleton_index = SHARED_SKELETON[slot]
    else:
        model = models[slot]
        b103 = next(record for record in model if record.kind == 0xB103)
        required_hashes = {
            struct.unpack_from("<I", files[52], offset)[0]
            for offset in range(b103.offset, b103.offset + b103.size, 4)
        }
        scored = []
        for index, candidate in enumerate(groups):
            mapping = next((record for record in candidate if record.kind == 0x7105), None)
            if mapping is None:
                continue
            available_hashes = {
                struct.unpack_from("<I", files[53], offset)[0]
                for offset in range(mapping.offset, mapping.offset + mapping.size, 8)
            }
            scored.append((len(required_hashes & available_hashes), index))
        if not scored or max(scored)[0] == 0:
            raise ValueError(f"could not find a compatible skeleton for slot {slot}")
        skeleton_index = max(scored)[1]
        print(
            f"slot {slot}: selected shared skeleton {skeleton_index} "
            f"({max(scored)[0]}/{len(required_hashes)} model bone hashes matched)"
        )
    group = groups[skeleton_index]
    by_kind = {record.kind: record for record in group}
    map_record = by_kind[0x7105]
    hash_to_id = {
        struct.unpack_from("<II", files[53], offset)[0]: struct.unpack_from("<II", files[53], offset)[1]
        for offset in range(map_record.offset, map_record.offset + map_record.size, 8)
    }
    transform = by_kind[0x7103]
    parent = by_kind[0x7106]
    bone_count = transform.size // 0x1C
    parents = struct.unpack_from("<" + "H" * bone_count, files[53], parent.offset)
    bones = []
    for index in range(bone_count):
        values = struct.unpack_from("<fffffff", files[53], transform.offset + index * 0x1C)
        bones.append({
            "name": f"bone_{index}", "parent": parents[index],
            "quaternion_xyzw": values[:4], "position": values[4:],
        })
    texture_formats = {}
    for record in parse_subentries(table):
        if record.kind == 0xB501 and record.offset + 13 <= len(files[63]):
            texture_hash = struct.unpack_from("<I", files[63], record.offset)[0]
            texture_formats[texture_hash] = files[63][record.offset + 12]
    return {"slot": slot, "bones": bones, "meshes": mesh_manifest(files, models[slot], hash_to_id, slot, texture_formats)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--slots", nargs="+", type=int, default=[27, 28, 29, 30])
    parser.add_argument("--archive", type=Path, default=CLEAN)
    parser.add_argument("--output", type=Path, default=OUTPUT)
    parser.add_argument("--name")
    args = parser.parse_args()
    _dictionary, data, entries, _table_offset, compressed = read_archive(args.archive)
    files = {index: decompress_entry(data, entries[index], compressed) for index in (0, 52, 53, 54, 63)}
    for slot in args.slots:
        base_name = args.name or f"slot_{slot}"
        folder = args.output / base_name if args.name else args.output / f"slot_{slot}"
        folder = folder.resolve()
        folder.mkdir(parents=True, exist_ok=True)
        manifest = folder / f"{base_name}.lm3-fbx.json"
        fbx = folder / f"{base_name}.fbx"
        report = folder / f"{base_name}.fbx-validation.json"
        manifest.write_text(json.dumps(build_manifest(files, slot), separators=(",", ":")), encoding="utf-8")
        subprocess.run([str(BLENDER), "--background", "--factory-startup", "--python", str(BRIDGE), "--", "export", str(manifest), str(fbx)], check=True)
        if not fbx.is_file():
            raise RuntimeError(f"Blender did not create the expected FBX: {fbx}")
        subprocess.run([str(BLENDER), "--background", "--factory-startup", "--python", str(BRIDGE), "--", "validate", str(fbx), str(report)], check=True)
        if not report.is_file():
            raise RuntimeError(f"Blender did not create the validation report: {report}")
        print(f"exported and validated slot {slot}: {fbx}")


if __name__ == "__main__":
    main()
