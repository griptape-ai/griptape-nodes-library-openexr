"""Integration tests for load_exr_channels() against real EXR fixtures."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np
import OpenEXR
import pytest

from griptape_nodes_openexr.exr.exr_io import load_exr_channels, load_slot_channels

DATA = Path(__file__).parents[1] / "data"


# ---------------------------------------------------------------------------
# Single-part file — basic channel loading
# ---------------------------------------------------------------------------


class TestSinglePart:
    FILE = DATA / "single_part_rgba.exr"

    def test_returns_all_channels(self) -> None:
        channels = load_exr_channels(str(self.FILE), part_index=0)
        assert set(channels.keys()) == {"R", "G", "B", "A"}

    def test_shape(self) -> None:
        channels = load_exr_channels(str(self.FILE), part_index=0)
        for arr in channels.values():
            assert arr.shape == (64, 64)

    def test_dtype_float32(self) -> None:
        channels = load_exr_channels(str(self.FILE), part_index=0)
        for arr in channels.values():
            assert arr.dtype == np.float32

    def test_pixel_values_in_range(self) -> None:
        channels = load_exr_channels(str(self.FILE), part_index=0)
        assert channels["A"].min() >= 0.0
        assert channels["A"].max() <= 1.0


# ---------------------------------------------------------------------------
# Multi-part file — part_index selects the correct part
# ---------------------------------------------------------------------------


class TestMultiPart:
    FILE = DATA / "multi_part.exr"

    def test_part0_rgba_channels(self) -> None:
        channels = load_exr_channels(str(self.FILE), part_index=0)
        assert set(channels.keys()) == {"R", "G", "B", "A"}

    def test_part1_depth_channel(self) -> None:
        channels = load_exr_channels(str(self.FILE), part_index=1)
        assert set(channels.keys()) == {"Z"}

    def test_part2_normal_channels(self) -> None:
        channels = load_exr_channels(str(self.FILE), part_index=2)
        assert set(channels.keys()) == {"X", "Y", "Z"}

    def test_parts_are_independent(self) -> None:
        depth = load_exr_channels(str(self.FILE), part_index=1)
        normal = load_exr_channels(str(self.FILE), part_index=2)
        assert "Z" in depth
        assert "Z" in normal
        # depth Z is a depth ramp; normal Z is all ones — values differ
        assert not np.allclose(depth["Z"], normal["Z"])


# ---------------------------------------------------------------------------
# Pixel type conversion — HALF, FLOAT, UINT all become float32
# ---------------------------------------------------------------------------


class TestPixelTypes:
    FILE = DATA / "pixel_types.exr"

    def test_half_channel_is_float32(self) -> None:
        channels = load_exr_channels(str(self.FILE), part_index=0)
        assert channels["half_ch"].dtype == np.float32

    def test_float_channel_is_float32(self) -> None:
        channels = load_exr_channels(str(self.FILE), part_index=0)
        assert channels["float_ch"].dtype == np.float32

    def test_uint_channel_is_float32(self) -> None:
        channels = load_exr_channels(str(self.FILE), part_index=0)
        assert channels["uint_ch"].dtype == np.float32

    def test_half_values_preserved(self) -> None:
        channels = load_exr_channels(str(self.FILE), part_index=0)
        assert channels["half_ch"] == pytest.approx(0.5, abs=1e-3)

    def test_float_values_preserved(self) -> None:
        channels = load_exr_channels(str(self.FILE), part_index=0)
        assert channels["float_ch"] == pytest.approx(1.0)

    def test_uint_values_preserved(self) -> None:
        channels = load_exr_channels(str(self.FILE), part_index=0)
        assert channels["uint_ch"] == pytest.approx(1000.0)


# ---------------------------------------------------------------------------
# Channel name filtering
# ---------------------------------------------------------------------------


class TestChannelFiltering:
    FILE = DATA / "single_part_aovs.exr"

    def test_filter_single_channel(self) -> None:
        channels = load_exr_channels(str(self.FILE), part_index=0, channel_names=["depth.Z"])
        assert set(channels.keys()) == {"depth.Z"}

    def test_filter_multiple_channels(self) -> None:
        channels = load_exr_channels(str(self.FILE), part_index=0, channel_names=["beauty.R", "beauty.G", "beauty.B"])
        assert set(channels.keys()) == {"beauty.R", "beauty.G", "beauty.B"}

    def test_filtered_channels_are_float32(self) -> None:
        channels = load_exr_channels(str(self.FILE), part_index=0, channel_names=["depth.Z"])
        assert channels["depth.Z"].dtype == np.float32

    def test_unknown_channel_raises(self) -> None:
        with pytest.raises(ValueError, match="Unknown channel"):
            load_exr_channels(str(self.FILE), part_index=0, channel_names=["nonexistent"])

    def test_none_loads_all(self) -> None:
        channels = load_exr_channels(str(self.FILE), part_index=0, channel_names=None)
        assert len(channels) == 11  # noqa: PLR2004


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------


def test_empty_path_raises() -> None:
    with pytest.raises(ValueError, match="file_path must not be empty"):
        load_exr_channels("", part_index=0)


def test_missing_file_raises() -> None:
    with pytest.raises(RuntimeError, match="Failed to read EXR file"):
        load_exr_channels("/nonexistent/file.exr", part_index=0)


def test_invalid_part_index_raises() -> None:
    f = str(DATA / "single_part_rgba.exr")
    with pytest.raises(ValueError, match="out of range"):
        load_exr_channels(f, part_index=5)


def test_negative_part_index_raises() -> None:
    f = str(DATA / "single_part_rgba.exr")
    with pytest.raises(ValueError, match="out of range"):
        load_exr_channels(f, part_index=-1)


# ---------------------------------------------------------------------------
# load_slot_channels — slots from one file decode it once
# ---------------------------------------------------------------------------


class TestLoadSlotChannels:
    FILE = DATA / "single_part_rgba.exr"

    MULTI_PART_FILE = DATA / "multi_part.exr"

    def _slot(self, name: str, file: Path = FILE, part_index: int = 0) -> MagicMock:
        artifact = MagicMock()
        artifact.file_path = str(file)
        artifact.part_index = part_index
        artifact.channel.name = name
        return artifact

    def test_matches_per_channel_loads_with_one_file_open(self) -> None:
        slots = {slot: self._slot(slot) for slot in "RGBA"}
        with patch("griptape_nodes_openexr.exr.exr_io.OpenEXR.File", side_effect=OpenEXR.File) as exr_file:
            pixels = load_slot_channels(slots)
        assert exr_file.call_count == 1
        for slot in "RGBA":
            np.testing.assert_array_equal(pixels[slot], load_exr_channels(str(self.FILE), 0, [slot])[slot])

    def test_slots_across_parts_of_one_file_share_one_file_open(self) -> None:
        file = self.MULTI_PART_FILE
        slots = {"R": self._slot("R", file, 0), "A": self._slot("Z", file, 1)}
        with patch("griptape_nodes_openexr.exr.exr_io.OpenEXR.File", side_effect=OpenEXR.File) as exr_file:
            pixels = load_slot_channels(slots)
        assert exr_file.call_count == 1
        np.testing.assert_array_equal(pixels["R"], load_exr_channels(str(file), 0, ["R"])["R"])
        np.testing.assert_array_equal(pixels["A"], load_exr_channels(str(file), 1, ["Z"])["Z"])
