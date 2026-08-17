"""Extract every texture referenced by an LM3 model slot to hash-named PNGs."""

from __future__ import annotations

import argparse
import struct
import sys
from pathlib import Path

ROOT = Path(__file__).parent
sys.path[:0] = [
    str(ROOT / "texture_decoder_vendor"),
    str(ROOT),
    str(ROOT.parents[1] / "src-tauri" / "misc"),
]

from PIL import Image, ImageDraw, ImageOps
import texture2ddecoder

from lm3_import_mummigi import decode_archive, texture_records
from lm3_slot_swap import group_models, parse_subentries


ARCHIVE = Path(r"E:\Yuzu\dumps\LM3\romfs\Scarescraper\Persistent.dict")
SLOT = 50
OUTPUT = ROOT / "slot50_textures_png"

ASTC_FORMATS = {
    0x19: (4, 4),
    0x1A: (5, 4),
    0x1B: (5, 5),
    0x1C: (6, 5),
    0x1D: (6, 6),
    0x1E: (8, 5),
    0x1F: (8, 6),
    0x20: (8, 8),
}


def block_height(width: int, height: int, texture_format: int) -> int:
    result = 8 if width <= 256 or height <= 256 else 16
    result = 4 if width <= 128 or height <= 128 else result
    result = 2 if width <= 64 or height <= 64 else result
    if texture_format == 0x1D:
        result = 4 if width <= 64 or height <= 64 else result
        result = 2 if width <= 32 or height <= 32 else result
        result = 1 if width <= 32 and height <= 32 else result
    return result


def block_linear_address(x: int, y: int, width_blocks: int, block_size: int, height: int) -> int:
    width_in_gobs = (width_blocks * block_size + 63) // 64
    gob = (
        (y // (8 * height)) * 512 * height * width_in_gobs
        + (x * block_size // 64) * 512 * height
        + ((y % (8 * height)) // 8) * 512
    )
    return (
        gob
        + ((x * block_size % 64) // 32) * 256
        + ((y % 8) // 2) * 64
        + ((x * block_size % 32) // 16) * 32
        + (y % 2) * 16
        + (x * block_size % 16)
    )


def untile(data: bytes, width_blocks: int, height_blocks: int, block_size: int, gob_height: int) -> bytes:
    output = bytearray(width_blocks * height_blocks * block_size)
    for y in range(height_blocks):
        for x in range(width_blocks):
            source = block_linear_address(x, y, width_blocks, block_size, gob_height)
            target = (y * width_blocks + x) * block_size
            if source + block_size > len(data):
                raise ValueError(
                    f"tiled texture is truncated at {source:#x} (length {len(data):#x})"
                )
            output[target : target + block_size] = data[source : source + block_size]
    return bytes(output)


def decode_texture(header: bytes, data: bytes) -> Image.Image:
    width, height = struct.unpack_from("<HH", header, 4)
    # Current LM3 headers store the GPU format at byte 12. Byte 8 is not the
    # format despite the older fmt_lm3 plug-in treating it as one.
    texture_format = header[12]
    raw_mode = "BGRA"
    if texture_format == 0x05:
        # Uncompressed RGBA8. Layered/cubemap allocations keep their first
        # surface at the start, so decoding the base surface is sufficient for
        # the hash-named inspection PNG.
        decoded = untile(
            data, width, height, 4, block_height(width, height, texture_format)
        )
        raw_mode = "RGBA"
    elif texture_format in ASTC_FORMATS:
        block_width, block_height_pixels = ASTC_FORMATS[texture_format]
        width_blocks = (width + block_width - 1) // block_width
        height_blocks = (height + block_height_pixels - 1) // block_height_pixels
        linear = untile(
            data, width_blocks, height_blocks, 16,
            block_height(width, height, texture_format),
        )
        decoded = texture2ddecoder.decode_astc(
            linear, width, height, block_width, block_height_pixels
        )
    elif texture_format == 0x11:
        width_blocks = (width + 3) // 4
        height_blocks = (height + 3) // 4
        linear = untile(data, width_blocks, height_blocks, 8, block_height(width, height, texture_format))
        decoded = texture2ddecoder.decode_bc1(linear, width, height)
    elif texture_format in (0x15, 0x16):
        # Some Global records retain the full-resolution header while only the
        # resident lower mip is stored in this archive. Select the largest
        # power-of-two mip whose complete BC5 surface fits the allocation.
        while ((width + 3) // 4) * ((height + 3) // 4) * 16 > len(data):
            width = max(1, width // 2)
            height = max(1, height // 2)
        width_blocks = (width + 3) // 4
        height_blocks = (height + 3) // 4
        linear = untile(data, width_blocks, height_blocks, 16, block_height(width, height, texture_format))
        decoded = texture2ddecoder.decode_bc5(linear, width, height)
    else:
        raise ValueError(f"unsupported LM3 texture format 0x{texture_format:02X}")
    return Image.frombytes("RGBA", (width, height), decoded, "raw", raw_mode)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", type=Path, default=ARCHIVE)
    parser.add_argument("--slot", type=int, default=SLOT)
    parser.add_argument("--output", type=Path, default=OUTPUT)
    args = parser.parse_args()

    _, _, _, _, _, files = decode_archive(args.archive)
    models = group_models(parse_subentries(files[0]))
    textures = texture_records(files[0], files[63])
    material = next(record for record in models[args.slot] if record.kind == 0xB006)
    payload = files[52][material.offset : material.offset + material.size]
    referenced = {
        struct.unpack_from("<I", payload, offset)[0]
        for offset in range(0, len(payload) - 3, 4)
        if struct.unpack_from("<I", payload, offset)[0] in textures
    }

    args.output.mkdir(parents=True, exist_ok=True)
    manifest = ["hash,width,height,format,data_size"]
    previews = []
    for texture_hash in sorted(referenced):
        header_record, image_record = textures[texture_hash]
        header = files[63][header_record.offset : header_record.offset + header_record.size]
        image_data = files[65][image_record.offset : image_record.offset + image_record.size]
        image = decode_texture(header, image_data)
        output_path = args.output / f"{texture_hash:08X}.png"
        image.save(output_path)
        preview = ImageOps.contain(image.convert("RGB"), (220, 220))
        previews.append((f"{texture_hash:08X}", preview))
        width, height = image.size
        manifest.append(
            f"{texture_hash:08X},{width},{height},0x{header[12]:02X},{image_record.size}"
        )
        print(output_path.name)
    (args.output / "manifest.csv").write_text("\n".join(manifest) + "\n", encoding="utf-8")
    columns = 4
    tile_width, tile_height = 240, 260
    rows = (len(previews) + columns - 1) // columns
    sheet = Image.new("RGB", (columns * tile_width, rows * tile_height), "#202020")
    draw = ImageDraw.Draw(sheet)
    for index, (label, preview) in enumerate(previews):
        x = (index % columns) * tile_width
        y = (index // columns) * tile_height
        sheet.paste(preview, (x + (tile_width - preview.width) // 2, y + 4))
        draw.text((x + 8, y + 232), label, fill="white")
    sheet.save(args.output / "contact_sheet.png")
    print(f"extracted {len(referenced)} textures to {args.output}")


if __name__ == "__main__":
    main()
