"""Generate EXR test fixtures into tests/data/."""

from pathlib import Path

import numpy as np
import OpenEXR

OUT = Path(__file__).parent / "data"
OUT.mkdir(parents=True, exist_ok=True)

W, H = 64, 64


def _gray(v=0.5):
    return np.full((H, W), v, dtype=np.float32)


def _ramp():
    x = np.linspace(0.0, 1.0, W, dtype=np.float32)
    y = np.linspace(0.0, 1.0, H, dtype=np.float32)
    return np.outer(y, x)


# ── 1. single_part_rgba.exr ──────────────────────────────────────────────────
def make_single_part_rgba():
    header = {"compression": OpenEXR.ZIP_COMPRESSION, "type": OpenEXR.scanlineimage}
    channels = {
        "R": _ramp(),
        "G": _ramp() * 0.5,
        "B": _gray(0.2),
        "A": np.ones((H, W), dtype=np.float32),
    }
    OpenEXR.File(header, channels).write(str(OUT / "single_part_rgba.exr"))


# ── 2. single_part_aovs.exr ──────────────────────────────────────────────────
def make_single_part_aovs():
    header = {"compression": OpenEXR.ZIP_COMPRESSION, "type": OpenEXR.scanlineimage}
    channels = {
        "beauty.R": _ramp(),
        "beauty.G": _ramp() * 0.7,
        "beauty.B": _gray(0.3),
        "beauty.A": np.ones((H, W), dtype=np.float32),
        "diffuse.R": _gray(0.8),
        "diffuse.G": _gray(0.6),
        "diffuse.B": _gray(0.4),
        "depth.Z": _ramp() * 10.0,
        "normal.X": _gray(0.0),
        "normal.Y": _gray(0.0),
        "normal.Z": np.ones((H, W), dtype=np.float32),
    }
    OpenEXR.File(header, channels).write(str(OUT / "single_part_aovs.exr"))


# ── 3. multi_part.exr ────────────────────────────────────────────────────────
def make_multi_part():
    rgba_part = OpenEXR.Part(
        {"compression": OpenEXR.ZIP_COMPRESSION},
        {"R": _ramp(), "G": _ramp() * 0.5, "B": _gray(0.2), "A": np.ones((H, W), dtype=np.float32)},
        "rgba",
    )
    depth_part = OpenEXR.Part(
        {"compression": OpenEXR.ZIP_COMPRESSION},
        {"Z": _ramp() * 10.0},
        "depth",
    )
    normal_part = OpenEXR.Part(
        {"compression": OpenEXR.ZIP_COMPRESSION},
        {"X": _gray(0.0), "Y": _gray(0.0), "Z": np.ones((H, W), dtype=np.float32)},
        "normal",
    )
    OpenEXR.File([rgba_part, depth_part, normal_part]).write(str(OUT / "multi_part.exr"))


# ── 4. tiled.exr ─────────────────────────────────────────────────────────────
def make_tiled():
    tw, th = 128, 128
    td = OpenEXR.TileDescription()
    td.xSize = 32
    td.ySize = 32
    td.mode = OpenEXR.ONE_LEVEL
    header = {
        "compression": OpenEXR.DWAB_COMPRESSION,
        "type": OpenEXR.tiledimage,
        "tiles": td,
    }
    big = np.linspace(0.0, 1.0, tw * th, dtype=np.float32).reshape(th, tw)
    channels = {"R": big, "G": big * 0.5, "B": big * 0.2, "A": np.ones((th, tw), dtype=np.float32)}
    OpenEXR.File(header, channels).write(str(OUT / "tiled.exr"))


# ── 5. overscan.exr ──────────────────────────────────────────────────────────
def make_overscan():
    ox, oy = -8, -8
    header = {
        "compression": OpenEXR.ZIP_COMPRESSION,
        "type": OpenEXR.scanlineimage,
        "displayWindow": (
            np.array([0, 0], dtype=np.int32),
            np.array([W - 1, H - 1], dtype=np.int32),
        ),
        "dataWindow": (
            np.array([ox, oy], dtype=np.int32),
            np.array([ox + W - 1, oy + H - 1], dtype=np.int32),
        ),
    }
    channels = {"R": _ramp(), "G": _ramp() * 0.5, "B": _gray(0.2), "A": np.ones((H, W), dtype=np.float32)}
    OpenEXR.File(header, channels).write(str(OUT / "overscan.exr"))


# ── 6. custom_attributes.exr ─────────────────────────────────────────────────
def make_custom_attributes():
    header = {
        "compression": OpenEXR.ZIP_COMPRESSION,
        "type": OpenEXR.scanlineimage,
        "owner": "VFX Studio",
        "comments": "Test fixture with custom EXR attributes.",
        "capDate": "2024:01:15 10:30:00",
        "software": "generate_fixtures.py v1.0",
        # Rec. 709 primaries + D65 white point
        "chromaticities": (0.64, 0.33, 0.30, 0.60, 0.15, 0.06, 0.3127, 0.3290),
    }
    channels = {"R": _ramp(), "G": _ramp() * 0.5, "B": _gray(0.2), "A": np.ones((H, W), dtype=np.float32)}
    OpenEXR.File(header, channels).write(str(OUT / "custom_attributes.exr"))


# ── 7. nuke_metadata.exr ─────────────────────────────────────────────────────
def make_nuke_metadata():
    header = {
        "compression": OpenEXR.ZIP_COMPRESSION,
        "type": OpenEXR.scanlineimage,
        "nuke/version": "15.0v1",
        "nuke/node_hash": "abc123def456",
        "nuke/full_layer_names": "0",
    }
    channels = {"R": _ramp(), "G": _ramp() * 0.5, "B": _gray(0.2), "A": np.ones((H, W), dtype=np.float32)}
    OpenEXR.File(header, channels).write(str(OUT / "nuke_metadata.exr"))


# ── 8. pixel_types.exr ───────────────────────────────────────────────────────
def make_pixel_types():
    """Single-part file with HALF, FLOAT, and UINT channels for pixel-type conversion tests."""
    channels = {
        "half_ch": np.full((H, W), 0.5, dtype=np.float16),
        "float_ch": np.full((H, W), 1.0, dtype=np.float32),
        "uint_ch": np.full((H, W), 1000, dtype=np.uint32),
    }
    header = {"compression": OpenEXR.ZIP_COMPRESSION, "type": OpenEXR.scanlineimage}
    OpenEXR.File(header, channels).write(str(OUT / "pixel_types.exr"))


# ── 9. legacy_multipart.exr ──────────────────────────────────────────────────
def make_legacy_multipart():
    rgba_part = OpenEXR.Part(
        {"compression": OpenEXR.ZIP_COMPRESSION},
        {"R": _ramp(), "G": _ramp() * 0.5, "B": _gray(0.2)},
        "beauty",
    )
    diffuse_part = OpenEXR.Part(
        {"compression": OpenEXR.ZIP_COMPRESSION},
        {"R": _gray(0.8), "G": _gray(0.6), "B": _gray(0.4)},
        "diffuse",
    )
    depth_part = OpenEXR.Part(
        {"compression": OpenEXR.ZIP_COMPRESSION},
        {"Z": _ramp() * 10.0},
        "depth",
    )
    OpenEXR.File([rgba_part, diffuse_part, depth_part]).write(str(OUT / "legacy_multipart.exr"))


# ── 10. infinity_attributes.exr ──────────────────────────────────────────────
def make_infinity_attributes():
    """Single-part EXR with custom float attributes set to non-finite values.

    Reproduces the bug where json.dumps serialises float('inf') as the bare
    token 'Infinity', which is not valid JSON and crashes JSON.parse.
    """
    header = {
        "compression": OpenEXR.ZIP_COMPRESSION,
        "type": OpenEXR.scanlineimage,
        "focus": float("inf"),     # positive infinity — triggers the bug
        "near_clip": float("-inf"),  # negative infinity
    }
    channels = {"R": _gray(0.0)}
    OpenEXR.File(header, channels).write(str(OUT / "infinity_attributes.exr"))


if __name__ == "__main__":
    fixtures = [
        ("single_part_rgba.exr", make_single_part_rgba),
        ("single_part_aovs.exr", make_single_part_aovs),
        ("multi_part.exr", make_multi_part),
        ("tiled.exr", make_tiled),
        ("overscan.exr", make_overscan),
        ("custom_attributes.exr", make_custom_attributes),
        ("nuke_metadata.exr", make_nuke_metadata),
        ("pixel_types.exr", make_pixel_types),
        ("legacy_multipart.exr", make_legacy_multipart),
        ("infinity_attributes.exr", make_infinity_attributes),
    ]
    for name, fn in fixtures:
        fn()
        print(f"  wrote {OUT / name}")
    print("Done.")
