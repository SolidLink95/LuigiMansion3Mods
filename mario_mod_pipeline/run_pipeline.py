"""Run the complete Luigi's Mansion 3 Mario replacement pipeline.

The defaults describe the validated Mario build in this repository.  Every
external input, tool, output, target slot, and packaging choice can be
overridden from the command line; run with ``--help`` to see them all.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

CWD = Path(__file__).resolve().parent
JSON_INPUT = CWD / "green_knight.json"
# JSON_INPUT = CWD / "params_mario.json"


def load_locals():
    local_json = CWD.parent / "local.json"
    result = json.loads(local_json.read_text())
    return {k: Path(v) for k, v in result.items()}


def load_input():
    return json.loads(JSON_INPUT.read_text())


LOCAL_DATA = load_locals()
INPUT_DATA = load_input()

ROOT = CWD.parent
RES = CWD / "res"
TMP = CWD / INPUT_DATA["TMP"]
HELPERS = CWD / "helpers"
CONFIG = CWD / INPUT_DATA["CONFIG"]

DEFAULT_ASSETS = TMP / INPUT_DATA["DEFAULT_ASSETS"]
MOD_NAME = INPUT_DATA["MOD_NAME"]
YUZU_MOD_PATH = Path(
    os.path.expandvars(
        f"%USERPROFILE%/AppData/Roaming/yuzu/load/0100DCA0064A6000/{MOD_NAME}/romfs"
    )
)
MOD_DEST_PATH = TMP / INPUT_DATA["MOD_DEST_PATH"]
SKIP_PACKAGE = INPUT_DATA.get("SKIP_PACKAGE", True)
VERIFY = INPUT_DATA.get("VERIFY", True)
FBX = DEFAULT_ASSETS / INPUT_DATA["FBX"]

BLENDER_PATH = LOCAL_DATA["blender"]
ROMFS = LOCAL_DATA["romfs"]


def path(value: str) -> Path:
    return Path(value).expanduser().resolve()


def copy_to_yuzu():
    if YUZU_MOD_PATH.is_dir():
        shutil.rmtree(YUZU_MOD_PATH)
    YUZU_MOD_PATH.parent.mkdir(exist_ok=True, parents=True)
    shutil.copytree(MOD_DEST_PATH, YUZU_MOD_PATH)
    print(f"[COPY] Copied mod to yuzu as name {YUZU_MOD_PATH.parent.name}")


def load_module(name: str, source: Path):
    spec = importlib.util.spec_from_file_location(name, source)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {source}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def sha256(source: Path) -> str:
    digest = hashlib.sha256()
    with source.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def require_file(source: Path, label: str) -> None:
    if not source.is_file():
        raise FileNotFoundError(f"{label} does not exist: {source}")


def parse_skeleton_groups(values: list[str]) -> dict[int, int]:
    result: dict[int, int] = {}
    for value in values:
        try:
            slot, group = value.split(":", 1)
            result[int(slot)] = int(group)
        except ValueError as error:
            raise argparse.ArgumentTypeError(
                f"invalid skeleton mapping {value!r}; expected SLOT:GROUP"
            ) from error
    return result


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--clean-archive", type=path, default=ROMFS / "global.dict")
    result.add_argument("--source-fbx", type=path, default=FBX)
    result.add_argument(
        "--mesh-data", type=path, default=DEFAULT_ASSETS / "Mario_to_luigi3.meshes.json"
    )
    result.add_argument("--texture-dir", type=path, default=DEFAULT_ASSETS / "tex")
    result.add_argument(
        "--replacement-rules", type=path, default=CONFIG / "replacement_rules.json"
    )
    result.add_argument(
        "--texture-redirects", type=path, default=CONFIG / "textures.json"
    )
    result.add_argument("--blender", type=path, default=BLENDER_PATH)
    result.add_argument(
        "--astcenc", default="astcenc-avx2.exe", help="ASTC encoder executable or path"
    )
    result.add_argument(
        "--temp-dir", type=path, default=ROOT / "tmp/ml3/mario_astc_temp"
    )
    result.add_argument("--build-output", type=path, default=MOD_DEST_PATH)
    result.add_argument(
        "--package-output",
        type=path,
        default=ROOT / f"tmp/ml3/clean_tex/_gb_result/{MOD_NAME}",
    )
    result.add_argument(
        "--installer-template",
        type=path,
        default=CWD / "installer/install_mario_playable.py",
    )
    result.add_argument("--mod-name", default=MOD_NAME)
    result.add_argument("--sections", nargs="+", type=int, default=[0, 52, 54, 65])
    result.add_argument("--targets", nargs="+", type=int, default=[27, 28, 29, 30])
    result.add_argument(
        "--skeleton-groups",
        nargs="+",
        default=["27:27", "28:27", "29:28", "30:28"],
        metavar="SLOT:GROUP",
    )
    result.add_argument("--blender-up-offset", type=float, default=0.128029)
    result.add_argument(
        "--skip-extract", type=bool, default=False, help="reuse --mesh-data"
    )
    result.add_argument(
        "--skip-build", type=bool, default=False, help="reuse --build-output"
    )
    result.add_argument(
        "--skip-package",
        action="store_true",
        help="do not create compact installer files",
    )
    result.add_argument(
        "--verify",
        action="store_true",
        help="install into a temporary clean archive and compare hashes",
    )
    result.add_argument(
        "--skip-deploy",
        action="store_true",
        help="build without copying the result into Yuzu's mod directory",
    )
    return result


def main() -> int:
    args = parser().parse_args()
    skeleton_groups = parse_skeleton_groups(args.skeleton_groups)
    missing_groups = set(args.targets) - skeleton_groups.keys()
    if missing_groups:
        raise ValueError(
            f"missing skeleton groups for target slots: {sorted(missing_groups)}"
        )

    require_file(args.clean_archive, "clean Global dictionary")
    require_file(args.clean_archive.with_suffix(".data"), "clean Global data")
    require_file(args.clean_archive.with_suffix(".patch"), "clean Global patch")

    if not args.skip_extract:
        print(f"[BLENDER] Running Blender")
        require_file(args.source_fbx, "source FBX")
        require_file(args.blender, "Blender")
        require_file(args.replacement_rules, "replacement rules")
        configured_meshes = list(
            json.loads(args.replacement_rules.read_text(encoding="utf-8"))[
                "mesh_targets"
            ]
        )
        args.mesh_data.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(
            [
                str(args.blender),
                "--background",
                "--factory-startup",
                "--python",
                str(RES / "extract_fbx_replacement.py"),
                "--",
                str(args.source_fbx),
                str(args.mesh_data),
                *configured_meshes,
            ],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

    if not args.skip_build:
        for source, label in (
            (args.mesh_data, "extracted mesh data"),
            (args.replacement_rules, "replacement rules"),
            (args.texture_redirects, "texture redirects"),
        ):
            require_file(source, label)
        sys.path.insert(0, str(HELPERS))
        builder = load_module("mario_pipeline_builder", RES / "build_mario_fbx_mod.py")
        builder.CLEAN = args.clean_archive
        builder.ASSET_ROOT = args.texture_dir.parent
        builder.TEXTURES = args.texture_dir
        builder.TEXTURE_REDIRECTS = args.texture_redirects
        builder.REPLACEMENT_RULES = args.replacement_rules
        builder.ASTCENC = args.astcenc
        builder.TEMP = args.temp_dir
        builder.OUTPUT = args.build_output
        builder.TARGETS = tuple(args.targets)
        builder.BLENDER_UP_OFFSET = args.blender_up_offset
        builder.MESH_DATA = args.mesh_data
        builder.SKELETON_GROUP_FOR_SLOT = skeleton_groups
        builder.main()

    built_dictionary = args.build_output / "global.dict"
    require_file(built_dictionary, "built Global dictionary")
    require_file(args.build_output / "global.data", "built Global data")

    if not args.skip_package:
        require_file(args.installer_template, "installer template")
        sys.path.insert(0, str(HELPERS))
        packager = load_module(
            "mario_pipeline_packager", RES / "package_mario_global_sections.py"
        )
        packager.ORIGINAL = args.clean_archive
        packager.BUILT = built_dictionary
        packager.OUTPUT = args.package_output
        packager.INSTALLER_TEMPLATE = args.installer_template
        old_argv = sys.argv
        try:
            sys.argv = [
                str(RES / "package_mario_global_sections.py"),
                "--built",
                str(built_dictionary),
                "--output",
                str(args.package_output),
                "--mod-name",
                args.mod_name,
                "--sections",
                *(str(section) for section in args.sections),
            ]
            packager.main()
        finally:
            sys.argv = old_argv
    if not args.verify:
        print(f"[SKIP] Verification skipped")
    else:
        installer = args.package_output / f"install_{args.mod_name}.py"
        require_file(installer, "packaged installer")
        args.package_output.mkdir(parents=True, exist_ok=True)
        verify_root = ROOT / "tmp" / "ml3"
        verify_root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(
            prefix="mario_pipeline_verify_", dir=verify_root
        ) as temporary:
            romfs = Path(temporary)
            for suffix in (".dict", ".data"):
                shutil.copy2(
                    args.clean_archive.with_suffix(suffix), romfs / f"global{suffix}"
                )
            subprocess.run([sys.executable, str(installer), str(romfs)], check=True)
            for name in ("global.dict", "global.data"):
                actual = sha256(romfs / name)
                expected = sha256(args.build_output / name)
                if actual != expected:
                    raise RuntimeError(
                        f"installer verification failed for {name}: {actual} != {expected}"
                    )
        print(
            "installer verification passed: global.dict and global.data match the build"
        )

    print(f"Mario pipeline complete: {args.build_output}")
    if not args.skip_package:
        print(f"compact installer: {args.package_output}")
    if args.skip_deploy:
        print("[SKIP] Yuzu deployment skipped")
    else:
        copy_to_yuzu()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
