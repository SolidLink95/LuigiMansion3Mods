"""Package exact global.dict/global.data byte deltas for the compact installer."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
from pathlib import Path

from package_mario_global_sections import encode_patch


def digest(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--original", type=Path, required=True)
    parser.add_argument("--built", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--installer-template", type=Path, required=True)
    parser.add_argument("--mod-name", required=True)
    args = parser.parse_args()

    args.output.mkdir(parents=True, exist_ok=True)
    for pattern in ("global_section_*.bin", "global_*.delta.bin"):
        for stale in args.output.glob(pattern):
            stale.unlink()

    files = []
    for name, filename in (
        ("global.dict", "global_dict.delta.bin"),
        ("global.data", "global_data.delta.bin"),
    ):
        original = args.original.with_name(name).read_bytes()
        replacement = args.built.with_name(name).read_bytes()
        if len(original) != len(replacement):
            raise ValueError(f"{name} size changed; physical delta requires equal sizes")
        patch, range_count, changed_bytes = encode_patch(original, replacement)
        (args.output / filename).write_bytes(patch)
        files.append(
            {
                "target": name,
                "file": filename,
                "file_size": len(replacement),
                "patch_size": len(patch),
                "range_count": range_count,
                "covered_changed_bytes": changed_bytes,
                "original_sha256": digest(original),
                "replacement_sha256": digest(replacement),
            }
        )

    manifest = {
        "format": "LM3 compact physical Global delta v3",
        "mod_name": args.mod_name,
        "files": files,
    }
    (args.output / "sections.json").write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
    )
    shutil.copyfile(args.installer_template, args.output / "install.py")
    print(
        f"packaged {args.mod_name}: "
        + ", ".join(f'{item["target"]}={item["patch_size"]} bytes' for item in files)
    )


if __name__ == "__main__":
    main()
