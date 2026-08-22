"""One-command pipeline for the 90s Luigi expanded mod.

Runs the whole chain: FBX mesh extraction (Blender headless) -> expanded
Global archive build (freed-region placement, never grows section 54) ->
distributable package under mods/90s_luigi_expanded -> end-to-end installer
verification against the clean ROMFS -> Yuzu LayeredFS deploy.

Inputs (paths relative to the repo root):
    tmp/expand/expand2.fbx          replacement meshes (objects named submesh_<n>)
    tmp/expand/tex/<hash>.png       replacement textures, named by Global texture hash
    local.json                      "blender" and "romfs" paths (gitignored)

Usage (from anywhere):
    python expand_archive/run_90s_luigi_pipeline.py
    python expand_archive/run_90s_luigi_pipeline.py --skip-extract          # reuse mesh JSON
    python expand_archive/run_90s_luigi_pipeline.py --skip-extract --skip-build
                                                                            # repackage only

Each stage only runs if the previous ones succeeded; any failure aborts with
that stage's output. The verify stage installs the packaged mod into
tmp/_verify_90s_luigi and requires the result to match the canonical build
SHA-256 for SHA-256 before anything is deployed.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent

MOD_NAME = "90s_luigi_expanded"
YUZU_MOD_NAME = "000_global_expanded"

FBX = ROOT / "tmp" / "expand" / "expand3.fbx"
MESH_JSON = ROOT / "tmp" / "expand_work" / "expand2.meshes.json"
EXTRACTOR = ROOT / "mario_mod_pipeline" / "res" / "extract_fbx_replacement.py"
BUILDER = ROOT / "mario_mod_pipeline" / "res" / "build_expanded_global.py"
PACKAGER = ROOT / "mario_mod_pipeline" / "res" / "package_expanded_global.py"
BUILT_ROMFS = ROOT / "tmp" / "_mods" / YUZU_MOD_NAME / "romfs"
MOD_DIR = ROOT / "mods" / MOD_NAME
VERIFY_DIR = ROOT / "tmp" / "_verify_90s_luigi"
ROMFS_FILES = ("global.dict", "global.data", "global.patch")


def announce(stage: str) -> None:
    print(f"\n=== {stage} " + "=" * max(0, 66 - len(stage)))


def run(command: list[str], stage: str) -> None:
    result = subprocess.run(command, cwd=ROOT)
    if result.returncode != 0:
        raise SystemExit(f"{stage} failed with exit code {result.returncode}")


def sha256_of(path: Path) -> str:
    output = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 22), b""):
            output.update(block)
    return output.hexdigest()


def jingle() -> None:
    try:
        import winsound

        for frequency, duration in (
            (659, 150), (659, 150), (659, 150), (523, 150), (659, 150), (784, 300)
        ):
            winsound.Beep(frequency, duration)
    except Exception:
        pass  # audio is a courtesy, never a failure


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--skip-extract", action="store_true",
                        help="reuse the existing mesh JSON instead of running Blender")
    parser.add_argument("--skip-build", action="store_true",
                        help="reuse the existing build under tmp/_mods/")
    parser.add_argument("--skip-package", action="store_true",
                        help="do not regenerate mods/" + MOD_NAME)
    parser.add_argument("--skip-verify", action="store_true",
                        help="skip the end-to-end installer verification (not recommended)")
    parser.add_argument("--skip-deploy", action="store_true",
                        help="do not copy the build into the Yuzu load directory")
    args = parser.parse_args()

    local = json.loads((ROOT / "local.json").read_text())
    romfs = Path(local["romfs"])
    for name in ROMFS_FILES:
        if not (romfs / name).is_file():
            raise SystemExit(f"clean ROMFS is missing {name}: {romfs}")

    if not args.skip_extract:
        announce("extract meshes (Blender headless)")
        blender = Path(local["blender"])
        if not blender.is_file():
            raise SystemExit(f"Blender not found: {blender} (local.json 'blender')")
        if not FBX.is_file():
            raise SystemExit(f"source FBX not found: {FBX}")
        MESH_JSON.parent.mkdir(parents=True, exist_ok=True)
        run(
            [str(blender), "--background", "--factory-startup",
             "--python", str(EXTRACTOR), "--", str(FBX), str(MESH_JSON)],
            "mesh extraction",
        )

    if not args.skip_build:
        announce("build expanded Global archive")
        if not MESH_JSON.is_file():
            raise SystemExit(f"mesh JSON not found: {MESH_JSON} (run without --skip-extract)")
        run([sys.executable, str(BUILDER)], "archive build")

    for name in ROMFS_FILES:
        if not (BUILT_ROMFS / name).is_file():
            raise SystemExit(f"canonical build is missing {name}: {BUILT_ROMFS}")

    if not args.skip_package:
        announce("package " + MOD_NAME)
        run(
            [sys.executable, str(PACKAGER),
             "--original", str(romfs / "global.dict"),
             "--built", str(BUILT_ROMFS / "global.dict"),
             "--output", str(MOD_DIR),
             "--mod-name", MOD_NAME],
            "packaging",
        )

    if not args.skip_verify:
        announce("verify packaged installer end-to-end")
        if VERIFY_DIR.exists():
            shutil.rmtree(VERIFY_DIR)
        VERIFY_DIR.mkdir(parents=True)
        for item in MOD_DIR.iterdir():
            if item.is_file():
                shutil.copyfile(item, VERIFY_DIR / item.name)
        run([sys.executable, str(VERIFY_DIR / "install.py"), str(romfs)],
            "installer verification run")
        for name in ROMFS_FILES:
            installed = sha256_of(VERIFY_DIR / "romfs" / name)
            canonical = sha256_of(BUILT_ROMFS / name)
            if installed != canonical:
                raise SystemExit(
                    f"verification FAILED: installed {name} ({installed}) does not "
                    f"match the canonical build ({canonical})"
                )
            print(f"{name}: installed == canonical ({canonical})")
        shutil.rmtree(VERIFY_DIR)
        print("end-to-end verification passed")

    if not args.skip_deploy:
        announce("deploy to Yuzu")
        appdata = Path.home() / "AppData" / "Roaming"
        target = appdata / "yuzu" / "load" / "0100DCA0064A6000" / YUZU_MOD_NAME / "romfs"
        target.mkdir(parents=True, exist_ok=True)
        try:
            for name in ROMFS_FILES:
                shutil.copyfile(BUILT_ROMFS / name, target / name)
        except PermissionError as error:
            raise SystemExit(
                f"deploy failed ({error}); close Yuzu (it locks global.data) and re-run "
                "with --skip-extract --skip-build --skip-package --skip-verify"
            )
        print(f"deployed {', '.join(ROMFS_FILES)} to {target}")

    announce("done")
    print(f"mod package: {MOD_DIR}")
    jingle()


if __name__ == "__main__":
    main()
