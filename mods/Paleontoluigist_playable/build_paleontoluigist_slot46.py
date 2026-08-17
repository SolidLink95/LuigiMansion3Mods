"""Build the verified Paleontoluigist Story Mode mod dynamically from slot 46."""

from dynamic_installer import run


MAPPING = {
    0x1F8C70FC: 0x1B320551, 0x1F9E897D: 0x1A58DF45,
    0x1FE6EB81: 0x1AC09D9C, 0x5D71C222: 0x4D1B8E4D,
    0x690164CC: 0x198CFD0B, 0x90F86A32: 0x8FA37F4F,
    0x91D1903E: 0x6E5EBC43, 0x91E3A8BF: 0x330969C5,
    0x922C0AC3: 0x83DEAF97, 0x94FC54FB: 0xDE8C1FBF,
    0x95E79388: 0x1A662317, 0x962FF58C: 0x3406C0D3,
    0xAEB6EEE3: 0xD59D3C60, 0xAFA22D70: 0x4345DE1A,
    0xAFEA8F74: 0x84B7D5A3, 0xD810DF2C: 0x43E8BAA3,
    0xD87D7232: 0x556FB4E1, 0xD8EA0538: 0x6D3D3433,
    0xD8FC1DB9: 0x2310E466,
}


if __name__ == "__main__":
    raise SystemExit(run(
        "Paleontoluigist_playable",
        tuple((target, 46) for target in (27, 28, 29, 30)),
        (46,),
        texture_mapping=MAPPING,
    ))
