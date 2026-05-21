"""Integration tests for scan_exr_header() against real EXR fixtures.

Fixtures produced with generate_fixtures.py (see tests/data/).
All assertions are against values confirmed by direct header inspection.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from griptape_nodes_openexr.exr.exr_io import scan_exr_header
from griptape_nodes_openexr.exr.exr_types import CompressionType, StorageType

DATA = Path(__file__).parents[1] / "data"


# ---------------------------------------------------------------------------
# single_part_rgba.exr - baseline
# ---------------------------------------------------------------------------


class TestSinglePartRgba:
    FILE = DATA / "single_part_rgba.exr"

    def test_one_part(self) -> None:
        data = scan_exr_header(str(self.FILE))
        assert len(data.parts) == 1

    def test_dimensions(self) -> None:
        part = scan_exr_header(str(self.FILE)).parts[0]
        assert part.width == 64
        assert part.height == 64

    def test_compression(self) -> None:
        part = scan_exr_header(str(self.FILE)).parts[0]
        assert part.header.compression == CompressionType.ZIP_COMPRESSION

    def test_storage_type(self) -> None:
        part = scan_exr_header(str(self.FILE)).parts[0]
        assert part.header.storage_type == StorageType.SCANLINE_IMAGE

    def test_channels(self) -> None:
        part = scan_exr_header(str(self.FILE)).parts[0]
        # EXR header stores channels alphabetically
        assert [ch.name for ch in part.channels] == ["A", "B", "G", "R"]

    def test_no_tile_description(self) -> None:
        part = scan_exr_header(str(self.FILE)).parts[0]
        assert part.header.tile_description is None

    def test_pixel_aspect_ratio(self) -> None:
        part = scan_exr_header(str(self.FILE)).parts[0]
        assert part.header.pixel_aspect_ratio == pytest.approx(1.0)

    def test_windows_at_origin(self) -> None:
        header = scan_exr_header(str(self.FILE)).parts[0].header
        assert header.data_window.xmin == 0
        assert header.data_window.ymin == 0
        assert header.data_window.xmax == 63
        assert header.data_window.ymax == 63
        assert header.display_window == header.data_window


# ---------------------------------------------------------------------------
# single_part_aovs.exr - multiple named channels
# ---------------------------------------------------------------------------


class TestSinglePartAovs:
    FILE = DATA / "single_part_aovs.exr"

    def test_channel_count(self) -> None:
        part = scan_exr_header(str(self.FILE)).parts[0]
        assert len(part.channels) == 11

    def test_channel_names(self) -> None:
        part = scan_exr_header(str(self.FILE)).parts[0]
        names = [ch.name for ch in part.channels]
        assert "beauty.R" in names
        assert "depth.Z" in names
        assert "diffuse.R" in names
        assert "normal.X" in names


# ---------------------------------------------------------------------------
# multi_part.exr - named parts
# ---------------------------------------------------------------------------


class TestMultiPart:
    FILE = DATA / "multi_part.exr"

    def test_three_parts(self) -> None:
        data = scan_exr_header(str(self.FILE))
        assert len(data.parts) == 3

    def test_part_names(self) -> None:
        data = scan_exr_header(str(self.FILE))
        assert [p.header.name for p in data.parts] == ["rgba", "depth", "normal"]

    def test_channel_names_raw(self) -> None:
        data = scan_exr_header(str(self.FILE))
        # Channels stored as-is, alphabetically
        rgba_channels = [ch.name for ch in data.parts[0].channels]
        assert rgba_channels == ["A", "B", "G", "R"]
        assert [ch.name for ch in data.parts[1].channels] == ["Z"]


# ---------------------------------------------------------------------------
# tiled.exr - tile description
# ---------------------------------------------------------------------------


class TestTiled:
    FILE = DATA / "tiled.exr"

    def test_dimensions(self) -> None:
        part = scan_exr_header(str(self.FILE)).parts[0]
        assert part.width == 128
        assert part.height == 128

    def test_storage_type_tiled(self) -> None:
        part = scan_exr_header(str(self.FILE)).parts[0]
        assert part.header.storage_type == StorageType.TILED_IMAGE

    def test_compression_dwab(self) -> None:
        part = scan_exr_header(str(self.FILE)).parts[0]
        assert part.header.compression == CompressionType.DWAB_COMPRESSION

    def test_tile_description_present(self) -> None:
        td = scan_exr_header(str(self.FILE)).parts[0].header.tile_description
        assert td is not None

    def test_tile_dimensions(self) -> None:
        td = scan_exr_header(str(self.FILE)).parts[0].header.tile_description
        assert td is not None
        assert td.tile_width == 32
        assert td.tile_height == 32

    def test_tile_level_mode(self) -> None:
        td = scan_exr_header(str(self.FILE)).parts[0].header.tile_description
        assert td is not None
        assert td.level_mode == "ONE_LEVEL"


# ---------------------------------------------------------------------------
# overscan.exr - data window extends outside display window (overscan pattern)
# Display is at origin; data window is -8,-8 to 55,55 (8px overscan on all sides)
# ---------------------------------------------------------------------------


class TestOverscan:
    FILE = DATA / "overscan.exr"

    def test_display_window_at_origin(self) -> None:
        header = scan_exr_header(str(self.FILE)).parts[0].header
        assert header.display_window.xmin == 0
        assert header.display_window.ymin == 0
        assert header.display_window.xmax == 63
        assert header.display_window.ymax == 63

    def test_data_window_extends_beyond_display(self) -> None:
        # 8px overscan on all sides: data starts at -8,-8 and ends at 55,55
        header = scan_exr_header(str(self.FILE)).parts[0].header
        assert header.data_window.xmin == -8
        assert header.data_window.ymin == -8
        assert header.data_window.xmax == 55
        assert header.data_window.ymax == 55

    def test_part_dimensions_from_data_window(self) -> None:
        # Width/height are derived from data window extents
        part = scan_exr_header(str(self.FILE)).parts[0]
        assert part.width == 64  # 55 - (-8) + 1
        assert part.height == 64  # 55 - (-8) + 1


# ---------------------------------------------------------------------------
# custom_attributes.exr - chromaticities and custom metadata
# ---------------------------------------------------------------------------


class TestCustomAttributes:
    FILE = DATA / "custom_attributes.exr"

    def test_chromaticities_extracted(self) -> None:
        header = scan_exr_header(str(self.FILE)).parts[0].header
        assert header.chromaticities is not None

    def test_chromaticities_values_rec709(self) -> None:
        # Fixture uses Rec.709/sRGB primaries
        c = scan_exr_header(str(self.FILE)).parts[0].header.chromaticities
        assert c is not None
        assert c.red_x == pytest.approx(0.64, abs=0.01)
        assert c.red_y == pytest.approx(0.33, abs=0.01)
        assert c.green_x == pytest.approx(0.30, abs=0.01)
        assert c.green_y == pytest.approx(0.60, abs=0.01)
        assert c.blue_x == pytest.approx(0.15, abs=0.01)
        assert c.blue_y == pytest.approx(0.06, abs=0.01)
        assert c.white_x == pytest.approx(0.3127, abs=0.001)
        assert c.white_y == pytest.approx(0.3290, abs=0.001)

    def test_software_extracted(self) -> None:
        header = scan_exr_header(str(self.FILE)).parts[0].header
        assert header.software == "generate_fixtures.py v1.0"

    def test_software_not_in_custom(self) -> None:
        custom = scan_exr_header(str(self.FILE)).parts[0].header.custom
        assert "software" not in custom
        assert "Software" not in custom

    def test_standard_attrs_not_duplicated_in_custom(self) -> None:
        custom = scan_exr_header(str(self.FILE)).parts[0].header.custom
        assert "compression" not in custom
        assert "chromaticities" not in custom
        assert "channels" not in custom


# ---------------------------------------------------------------------------
# nuke_metadata.exr - EXR from Nuke Write (or equivalent)
# ---------------------------------------------------------------------------


class TestNukeMetadata:
    FILE = DATA / "nuke_metadata.exr"

    def test_loads_successfully(self) -> None:
        data = scan_exr_header(str(self.FILE))
        assert len(data.parts) == 1

    def test_basic_structure(self) -> None:
        part = scan_exr_header(str(self.FILE)).parts[0]
        assert part.width == 64
        assert part.height == 64
        # EXR header stores channels alphabetically
        assert [ch.name for ch in part.channels] == ["A", "B", "G", "R"]

    def test_compression_zip(self) -> None:
        part = scan_exr_header(str(self.FILE)).parts[0]
        assert part.header.compression == CompressionType.ZIP_COMPRESSION

    def test_nuke_attrs_in_custom(self) -> None:
        custom = scan_exr_header(str(self.FILE)).parts[0].header.custom
        assert "nuke/version" in custom
        assert "nuke/node_hash" in custom
        assert "nuke/full_layer_names" in custom


# ---------------------------------------------------------------------------
# legacy_multipart.exr - part names as layer names, bare channels
# ---------------------------------------------------------------------------


class TestLegacyMultipart:
    FILE = DATA / "legacy_multipart.exr"

    def test_three_parts(self) -> None:
        data = scan_exr_header(str(self.FILE))
        assert len(data.parts) == 3

    def test_part_names_preserved_in_header(self) -> None:
        data = scan_exr_header(str(self.FILE))
        assert [p.header.name for p in data.parts] == ["beauty", "diffuse", "depth"]

    def test_channel_names_raw(self) -> None:
        data = scan_exr_header(str(self.FILE))
        # Channels stored without prefix; EXR header sorts alphabetically
        beauty_channels = [ch.name for ch in data.parts[0].channels]
        assert beauty_channels == ["B", "G", "R"]
        depth_channels = [ch.name for ch in data.parts[2].channels]
        assert depth_channels == ["Z"]


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------


def test_empty_path_raises() -> None:
    with pytest.raises(ValueError, match="file_path must not be empty"):
        scan_exr_header("")


def test_missing_file_raises() -> None:
    with pytest.raises(RuntimeError, match="Failed to open EXR file"):
        scan_exr_header("/nonexistent/file.exr")
