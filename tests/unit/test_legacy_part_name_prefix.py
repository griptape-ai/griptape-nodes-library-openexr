"""Unit tests for _apply_legacy_part_name_prefix()."""

from griptape_nodes_openexr.exr.exr_types import (
    CompressionType,
    EXRChannelInfo,
    EXRHeader,
    EXRLayer,
    EXRPart,
    LineOrderType,
    PixelType,
    StorageType,
    WindowCoordinates,
    _apply_legacy_part_name_prefix,
)


def _make_header(name: str = "", custom: dict | None = None) -> EXRHeader:
    return EXRHeader(
        compression=CompressionType.ZIP_COMPRESSION,
        line_order=LineOrderType.INCREASING_Y,
        data_window=WindowCoordinates(0, 0, 63, 63),
        display_window=WindowCoordinates(0, 0, 63, 63),
        pixel_aspect_ratio=1.0,
        screen_window_center=(0.0, 0.0),
        screen_window_width=1.0,
        storage_type=StorageType.SCANLINE_IMAGE,
        name=name,
        chunk_count=None,
        tile_description=None,
        chromaticities=None,
        time_code=None,
        owner=None,
        comments=None,
        capture_date=None,
        software=None,
        custom=custom or {},
    )


def _make_part(name: str, channels: list[EXRChannelInfo]) -> EXRPart:
    return EXRPart(
        name=name,
        channels=channels,
        layers=[EXRLayer(name="", channels=channels)],
        header=_make_header(name=name),
        width=64,
        height=64,
    )


def _ch(name: str) -> EXRChannelInfo:
    return EXRChannelInfo(name=name, pixel_type=PixelType.HALF, x_sampling=1, y_sampling=1)


def test_legacy_multi_part_prefixed() -> None:
    parts = [
        _make_part("beauty", [_ch("R"), _ch("G"), _ch("B")]),
        _make_part("depth", [_ch("Z")]),
    ]
    _apply_legacy_part_name_prefix(parts)

    # Channels should now have part name prefix
    assert parts[0].channels[0].name == "beauty.R"
    assert parts[1].channels[0].name == "depth.Z"
    # Layers should be regrouped
    assert parts[0].layers[0].name == "beauty"
    assert parts[1].layers[0].name == "depth"


def test_dotted_channels_not_modified() -> None:
    # If any channel already has a dot, skip legacy treatment
    parts = [
        _make_part("beauty", [_ch("beauty.R"), _ch("beauty.G")]),
        _make_part("depth", [_ch("depth.Z")]),
    ]
    original_names = [ch.name for part in parts for ch in part.channels]
    _apply_legacy_part_name_prefix(parts)
    assert [ch.name for part in parts for ch in part.channels] == original_names


def test_full_layer_names_attribute_skips_prefix() -> None:
    parts = [
        _make_part("beauty", [_ch("R")]),
    ]
    parts[0].header.custom["fullLayerNames"] = True
    _apply_legacy_part_name_prefix(parts)
    # Should not be modified
    assert parts[0].channels[0].name == "R"


def test_unnamed_part_skipped() -> None:
    parts = [
        _make_part("", [_ch("R")]),
        _make_part("", [_ch("Z")]),
    ]
    _apply_legacy_part_name_prefix(parts)
    # Empty part names - no prefix applied
    assert parts[0].channels[0].name == "R"
    assert parts[1].channels[0].name == "Z"
