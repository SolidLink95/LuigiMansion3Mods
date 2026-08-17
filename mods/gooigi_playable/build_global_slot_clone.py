"""Clone one clean Global model slot into Story Luigi slots 27-30."""

from __future__ import annotations

import struct
from pathlib import Path

from lm3_slot_swap import (
    decompress_entry,
    group_models,
    parse_subentries,
    read_archive,
    replace_entry,
)


FILE_FOR_KIND = {
    0xB006: 52, 0xB005: 54, 0xB00C: 52, 0xB004: 52,
    0xB00A: 52, 0xB00B: 52, 0xB003: 52, 0xB007: 52,
    0xB001: 52, 0xB002: 52, 0xB100: 53, 0xB008: 53,
    0xB009: 53, 0xB101: 52, 0xB102: 52, 0xB103: 52,
}


def build(global_dict: Path, output: Path, source_slot: int = 24) -> None:
    dictionary, data, entries, table_offset, compressed = read_archive(global_dict)
    indexes = (0, 52, 53, 54)
    changed = {
        index: bytearray(decompress_entry(data, entries[index], compressed))
        for index in indexes
    }
    models = group_models(parse_subentries(changed[0]))
    source_by_kind = {record.kind: record for record in models[source_slot]}

    for target in (27, 28, 29, 30):
        for target_record in models[target]:
            source_record = source_by_kind[target_record.kind]
            if source_record.size > target_record.size:
                raise ValueError(
                    f"slot {target} allocation for {target_record.kind:04X} is too small"
                )
            file_index = FILE_FOR_KIND[target_record.kind]
            payload = bytes(changed[file_index][
                source_record.offset:source_record.offset + source_record.size
            ])
            start = target_record.offset
            changed[file_index][start:start + len(payload)] = payload
            changed[file_index][start + len(payload):start + target_record.size] = bytes(
                target_record.size - len(payload)
            )
            struct.pack_into(
                "<I", changed[0], target_record.table_offset + 4, len(payload)
            )

    for index in (52, 53, 54, 0):
        replace_entry(
            dictionary, data, entries, table_offset, index,
            bytes(changed[index]), compressed,
        )

    output.mkdir(parents=True, exist_ok=True)
    (output / "global.dict").write_bytes(dictionary)
    (output / "global.data").write_bytes(data)
    (output / "global.patch").write_bytes(global_dict.with_suffix(".patch").read_bytes())


if __name__ == "__main__":
    raise SystemExit("Import build() from an installer")
