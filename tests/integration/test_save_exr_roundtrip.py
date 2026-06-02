"""Integration tests: round-trip EXR write → load → verify."""

from __future__ import annotations

from pathlib import Path

import numpy as np

from griptape_nodes_openexr.exr.exr_io import load_exr_channels, scan_exr_header, write_exr_channels

DATA = Path(__file__).parents[1] / "data"
SINGLE_PART_RGBA = DATA / "single_part_rgba.exr"


class TestRoundTripAllChannels:
    """load_exr_channels → write_exr_channels → load_exr_channels."""

    def test_channel_names_preserved(self, tmp_path: Path) -> None:
        original = load_exr_channels(str(SINGLE_PART_RGBA), 0)
        out = str(tmp_path / "rt.exr")
        write_exr_channels(out, original)
        reloaded = load_exr_channels(out, 0)
        assert set(reloaded.keys()) == set(original.keys())

    def test_dimensions_preserved(self, tmp_path: Path) -> None:
        original = load_exr_channels(str(SINGLE_PART_RGBA), 0)
        out = str(tmp_path / "rt.exr")
        write_exr_channels(out, original)
        reloaded = load_exr_channels(out, 0)
        for name in original:
            assert reloaded[name].shape == original[name].shape

    def test_half_roundtrip_values_close(self, tmp_path: Path) -> None:
        original = load_exr_channels(str(SINGLE_PART_RGBA), 0, ["R", "G", "B"])
        out = str(tmp_path / "rt_half.exr")
        write_exr_channels(out, original, pixel_type="half")
        reloaded = load_exr_channels(out, 0, ["R", "G", "B"])
        for name in ("R", "G", "B"):
            # float16 has ~3 decimal digit precision
            np.testing.assert_allclose(reloaded[name], original[name], atol=1e-3)

    def test_float_roundtrip_values_exact(self, tmp_path: Path) -> None:
        original = load_exr_channels(str(SINGLE_PART_RGBA), 0, ["R", "G", "B"])
        out = str(tmp_path / "rt_float.exr")
        write_exr_channels(out, original, pixel_type="float")
        reloaded = load_exr_channels(out, 0, ["R", "G", "B"])
        for name in ("R", "G", "B"):
            np.testing.assert_allclose(reloaded[name], original[name], atol=1e-6)

    def test_header_part_count(self, tmp_path: Path) -> None:
        original = load_exr_channels(str(SINGLE_PART_RGBA), 0)
        out = str(tmp_path / "rt.exr")
        write_exr_channels(out, original)
        data = scan_exr_header(out, header_only=False)
        assert len(data.parts) == 1

    def test_header_dimensions_match(self, tmp_path: Path) -> None:
        src_data = scan_exr_header(str(SINGLE_PART_RGBA))
        src_part = src_data.parts[0]
        original = load_exr_channels(str(SINGLE_PART_RGBA), 0)
        out = str(tmp_path / "rt.exr")
        write_exr_channels(out, original)
        out_data = scan_exr_header(out)
        out_part = out_data.parts[0]
        assert out_part.width == src_part.width
        assert out_part.height == src_part.height
