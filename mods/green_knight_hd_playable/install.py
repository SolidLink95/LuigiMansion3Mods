"""Build the verified Green Knight HD Story Mode mod dynamically."""

from dynamic_installer import run


if __name__ == "__main__":
    raise SystemExit(run(
        "green_knight_hd_playable",
        ((27, 38), (28, 39), (29, 38), (30, 38)),
        (38, 39),
        skeleton_pairs=((27, 30), (28, 30)),
        empty_kinds=set(),
        keep_target_kinds=set(),
    ))
