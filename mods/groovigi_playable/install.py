"""Build the verified Groovigi Story Mode mod dynamically from slot 50."""

from dynamic_installer import run


MAPPING = {
    0x20197348: 0x84B7D5A3, 0x4FE3BA54: 0x198CFD0B,
    0x5D71C222: 0x4D1B8E4D, 0x90F86A32: 0xD59D3C60,
    0x91D1903E: 0x6E5EBC43, 0x91E3A8BF: 0x330969C5,
    0x922C0AC3: 0x83DEAF97, 0x94FC54FB: 0xF6831E9A,
    0x95E79388: 0x1A662317, 0x962FF58C: 0x3406C0D3,
    0xAEB6EEE3: 0xDE8C1FBF, 0xAFA22D70: 0x4345DE1A,
    0xAFEA8F74: 0x8FA37F4F, 0xD810DF2C: 0x43E8BAA3,
    0xD87D7232: 0x556FB4E1, 0xD8EA0538: 0x6D3D3433,
    0xD8FC1DB9: 0x2310E466, 0xDD6B7384: 0x1AC09D9C,
    0xDD7D8C05: 0x1A58DF45,
}


if __name__ == "__main__":
    raise SystemExit(run(
        "groovigi_playable",
        tuple((target, 50) for target in (27, 28, 29, 30)),
        (50,),
        texture_mapping=MAPPING,
    ))
