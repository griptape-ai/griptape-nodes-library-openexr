"""Integration tests for scan_exr_header() against real EXR fixtures.

Fixtures produced with generate_fixtures.py (see tests/data/).
All assertions are against values confirmed by direct OIIO inspection.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from griptape_nodes_openexr.exr.exr_io import scan_exr_header
from griptape_nodes_openexr.exr.exr_types import CompressionType, StorageType
from griptape_nodes_openexr.exr.strategies.nuke_strategy import NukeChannelGrouping
from griptape_nodes_openexr.exr.strategies.raw_strategy import RawEXRChannelGrouping

DATA = Path(__file__).parents[1] / "data"
NUKE = NukeChannelGrouping()
RAW = RawEXRChannelGrouping()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def layer_map(data, part_index: int = 0) -> dict[str, list[str]]:
    """Return {layer_name: [channel_names]} for a part."""
    return {layer.name: [ch.name for ch in layer.channels] for layer in data.parts[part_index].layers}


# ---------------------------------------------------------------------------
# single_part_rgba.exr - baseline
# ---------------------------------------------------------------------------


class TestSinglePartRgba:
    FILE = DATA / "single_part_rgba.exr"

    def test_one_part(self) -> None:
        data = scan_exr_header(str(self.FILE), NUKE)
        assert len(data.parts) == 1

    def test_dimensions(self) -> None:
        part = scan_exr_header(str(self.FILE), NUKE).parts[0]
        assert part.width == 64
        assert part.height == 64

    def test_compression(self) -> None:
        part = scan_exr_header(str(self.FILE), NUKE).parts[0]
        assert part.header.compression == CompressionType.ZIP_COMPRESSION

    def test_storage_type(self) -> None:
        part = scan_exr_header(str(self.FILE), NUKE).parts[0]
        assert part.header.storage_type == StorageType.SCANLINE_IMAGE

    def test_channels(self) -> None:
        part = scan_exr_header(str(self.FILE), NUKE).parts[0]
        assert [ch.name for ch in part.channels] == ["R", "G", "B", "A"]

    def test_single_default_layer(self) -> None:
        part = scan_exr_header(str(self.FILE), NUKE).parts[0]
        assert len(part.layers) == 1
        assert part.layers[0].name == ""

    def test_no_tile_description(self) -> None:
        part = scan_exr_header(str(self.FILE), NUKE).parts[0]
        assert part.header.tile_description is None

    def test_pixel_aspect_ratio(self) -> None:
        part = scan_exr_header(str(self.FILE), NUKE).parts[0]
        assert part.header.pixel_aspect_ratio == pytest.approx(1.0)

    def test_windows_at_origin(self) -> None:
        header = scan_exr_header(str(self.FILE), NUKE).parts[0].header
        assert header.data_window.xmin == 0
        assert header.data_window.ymin == 0
        assert header.data_window.xmax == 63
        assert header.data_window.ymax == 63
        assert header.display_window == header.data_window


# ---------------------------------------------------------------------------
# single_part_aovs.exr - Nuke layer grouping
# ---------------------------------------------------------------------------


class TestSinglePartAovs:
    FILE = DATA / "single_part_aovs.exr"

    def test_channel_count(self) -> None:
        part = scan_exr_header(str(self.FILE), NUKE).parts[0]
        assert len(part.channels) == 11

    def test_nuke_layer_count(self) -> None:
        part = scan_exr_header(str(self.FILE), NUKE).parts[0]
        assert len(part.layers) == 4

    def test_nuke_layer_names_sorted(self) -> None:
        part = scan_exr_header(str(self.FILE), NUKE).parts[0]
        names = [layer.name for layer in part.layers]
        assert names == ["beauty", "depth", "diffuse", "normal"]

    def test_nuke_layer_channels(self) -> None:
        lmap = layer_map(scan_exr_header(str(self.FILE), NUKE))
        assert lmap["beauty"] == ["beauty.R", "beauty.G", "beauty.B", "beauty.A"]
        assert lmap["depth"] == ["depth.Z"]
        assert lmap["diffuse"] == ["diffuse.R", "diffuse.G", "diffuse.B"]
        assert lmap["normal"] == ["normal.X", "normal.Y", "normal.Z"]

    def test_raw_flat_layers(self) -> None:
        # Raw strategy: one layer per channel, all with empty name
        part = scan_exr_header(str(self.FILE), RAW).parts[0]
        assert len(part.layers) == 11
        assert all(layer.name == "" for layer in part.layers)

    def test_raw_channel_names_preserved(self) -> None:
        part = scan_exr_header(str(self.FILE), RAW).parts[0]
        channel_names = [layer.channels[0].name for layer in part.layers]
        assert "beauty.R" in channel_names
        assert "depth.Z" in channel_names


# ---------------------------------------------------------------------------
# multi_part.exr - named parts, Nuke legacy prefix
# ---------------------------------------------------------------------------


class TestMultiPart:
    FILE = DATA / "multi_part.exr"

    def test_three_parts(self) -> None:
        data = scan_exr_header(str(self.FILE), NUKE)
        assert len(data.parts) == 3

    def test_part_names(self) -> None:
        data = scan_exr_header(str(self.FILE), NUKE)
        assert [p.header.name for p in data.parts] == ["rgba", "depth", "normal"]

    def test_legacy_prefix_applied(self) -> None:
        # No dots in original channels → legacy prefix fires
        data = scan_exr_header(str(self.FILE), NUKE)
        assert data.parts[0].layers[0].name == "rgba"
        assert data.parts[1].layers[0].name == "depth"
        assert data.parts[2].layers[0].name == "normal"

    def test_legacy_channel_names_prefixed(self) -> None:
        data = scan_exr_header(str(self.FILE), NUKE)
        rgba_channels = [ch.name for ch in data.parts[0].channels]
        assert rgba_channels == ["rgba.R", "rgba.G", "rgba.B", "rgba.A"]
        assert [ch.name for ch in data.parts[1].channels] == ["depth.Z"]

    def test_raw_no_prefix(self) -> None:
        # Raw strategy skips legacy prefix
        data = scan_exr_header(str(self.FILE), RAW)
        assert data.parts[0].channels[0].name == "R"


# ---------------------------------------------------------------------------
# tiled.exr - tile description
# ---------------------------------------------------------------------------


class TestTiled:
    FILE = DATA / "tiled.exr"

    def test_dimensions(self) -> None:
        part = scan_exr_header(str(self.FILE), NUKE).parts[0]
        assert part.width == 128
        assert part.height == 128

    def test_storage_type_tiled(self) -> None:
        part = scan_exr_header(str(self.FILE), NUKE).parts[0]
        assert part.header.storage_type == StorageType.TILED_IMAGE

    def test_compression_dwab(self) -> None:
        part = scan_exr_header(str(self.FILE), NUKE).parts[0]
        assert part.header.compression == CompressionType.DWAB_COMPRESSION

    def test_tile_description_present(self) -> None:
        td = scan_exr_header(str(self.FILE), NUKE).parts[0].header.tile_description
        assert td is not None

    def test_tile_dimensions(self) -> None:
        td = scan_exr_header(str(self.FILE), NUKE).parts[0].header.tile_description
        assert td is not None
        assert td.tile_width == 32
        assert td.tile_height == 32

    def test_tile_level_mode(self) -> None:
        td = scan_exr_header(str(self.FILE), NUKE).parts[0].header.tile_description
        assert td is not None
        assert td.level_mode == "ONE_LEVEL"


# ---------------------------------------------------------------------------
# overscan.exr - data window extends outside display window (overscan pattern)
# Display is at origin; data window is -8,-8 to 55,55 (8px overscan on all sides)
# ---------------------------------------------------------------------------


class TestOverscan:
    FILE = DATA / "overscan.exr"

    def test_display_window_at_origin(self) -> None:
        header = scan_exr_header(str(self.FILE), NUKE).parts[0].header
        assert header.display_window.xmin == 0
        assert header.display_window.ymin == 0
        assert header.display_window.xmax == 63
        assert header.display_window.ymax == 63

    def test_data_window_extends_beyond_display(self) -> None:
        # 8px overscan on all sides: data starts at -8,-8 and ends at 55,55
        header = scan_exr_header(str(self.FILE), NUKE).parts[0].header
        assert header.data_window.xmin == -8
        assert header.data_window.ymin == -8
        assert header.data_window.xmax == 55
        assert header.data_window.ymax == 55

    def test_part_dimensions_from_data_window(self) -> None:
        # Width/height derived from data window extents, not display window
        part = scan_exr_header(str(self.FILE), NUKE).parts[0]
        assert part.width == 64
        assert part.height == 64

    def test_nuke_normalisation_no_op_when_display_at_origin(self) -> None:
        # Nuke normalisation only shifts when display origin is non-zero;
        # display is already at (0,0) so coordinates are preserved as-is
        header = scan_exr_header(str(self.FILE), NUKE).parts[0].header
        assert header.data_window.xmin == -8
        assert header.display_window.xmin == 0

    def test_raw_windows_unchanged(self) -> None:
        header = scan_exr_header(str(self.FILE), RAW).parts[0].header
        assert header.data_window.xmin == -8
        assert header.display_window.xmin == 0


# ---------------------------------------------------------------------------
# custom_attributes.exr - chromaticities and custom metadata
# ---------------------------------------------------------------------------


class TestCustomAttributes:
    FILE = DATA / "custom_attributes.exr"

    def test_chromaticities_extracted(self) -> None:
        header = scan_exr_header(str(self.FILE), NUKE).parts[0].header
        assert header.chromaticities is not None

    def test_chromaticities_values_rec709(self) -> None:
        # Fixture uses Rec.709/sRGB primaries
        c = scan_exr_header(str(self.FILE), NUKE).parts[0].header.chromaticities
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
        header = scan_exr_header(str(self.FILE), NUKE).parts[0].header
        assert header.software == "generate_fixtures.py v1.0"

    def test_software_not_in_custom(self) -> None:
        header = scan_exr_header(str(self.FILE), NUKE).parts[0].header
        assert "software" not in header.custom
        assert "Software" not in header.custom

    def test_non_standard_attrs_in_custom(self) -> None:
        custom = scan_exr_header(str(self.FILE), NUKE).parts[0].header.custom
        assert "DateTime" in custom
        assert "ImageDescription" in custom
        assert "Copyright" in custom

    def test_standard_attrs_not_duplicated_in_custom(self) -> None:
        custom = scan_exr_header(str(self.FILE), NUKE).parts[0].header.custom
        assert "compression" not in custom
        assert "PixelAspectRatio" not in custom
        assert "chromaticities" not in custom


# ---------------------------------------------------------------------------
# nuke_metadata.exr - EXR from Nuke Write (or equivalent)
# ---------------------------------------------------------------------------


class TestNukeMetadata:
    FILE = DATA / "nuke_metadata.exr"

    def test_loads_successfully(self) -> None:
        data = scan_exr_header(str(self.FILE), NUKE)
        assert len(data.parts) == 1

    def test_basic_structure(self) -> None:
        part = scan_exr_header(str(self.FILE), NUKE).parts[0]
        assert part.width == 256
        assert part.height == 256
        assert [ch.name for ch in part.channels] == ["R", "G", "B"]

    def test_compression_zips(self) -> None:
        part = scan_exr_header(str(self.FILE), NUKE).parts[0]
        assert part.header.compression == CompressionType.ZIPS_COMPRESSION

    def test_nuke_attrs_in_custom(self) -> None:
        custom = scan_exr_header(str(self.FILE), NUKE).parts[0].header.custom
        assert "nuke/full_layer_names" in custom
        assert "nuke/node_hash" in custom
        assert "nuke/version" in custom

    def test_nuke_version(self) -> None:
        custom = scan_exr_header(str(self.FILE), NUKE).parts[0].header.custom
        assert custom["nuke/version"] == "17.0v1"

    def test_single_default_layer(self) -> None:
        part = scan_exr_header(str(self.FILE), NUKE).parts[0]
        assert len(part.layers) == 1
        assert part.layers[0].name == ""


# ---------------------------------------------------------------------------
# legacy_multipart.exr - part names as layer names, bare channels
# ---------------------------------------------------------------------------


class TestLegacyMultipart:
    FILE = DATA / "legacy_multipart.exr"

    def test_three_parts(self) -> None:
        data = scan_exr_header(str(self.FILE), NUKE)
        assert len(data.parts) == 3

    def test_legacy_layer_names(self) -> None:
        data = scan_exr_header(str(self.FILE), NUKE)
        assert data.parts[0].layers[0].name == "beauty"
        assert data.parts[1].layers[0].name == "diffuse"
        assert data.parts[2].layers[0].name == "depth"

    def test_legacy_channels_prefixed(self) -> None:
        data = scan_exr_header(str(self.FILE), NUKE)
        beauty_channels = [ch.name for ch in data.parts[0].channels]
        assert beauty_channels == ["beauty.R", "beauty.G", "beauty.B"]
        depth_channels = [ch.name for ch in data.parts[2].channels]
        assert depth_channels == ["depth.Z"]

    def test_raw_strategy_no_prefix(self) -> None:
        data = scan_exr_header(str(self.FILE), RAW)
        assert data.parts[0].channels[0].name == "R"

    def test_part_names_preserved_in_header(self) -> None:
        data = scan_exr_header(str(self.FILE), NUKE)
        assert [p.header.name for p in data.parts] == ["beauty", "diffuse", "depth"]


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------


def test_empty_path_raises() -> None:
    with pytest.raises(ValueError, match="file_path must not be empty"):
        scan_exr_header("", NUKE)


def test_missing_file_raises() -> None:
    with pytest.raises(RuntimeError, match="Failed to open EXR file"):
        scan_exr_header("/nonexistent/file.exr", NUKE)
