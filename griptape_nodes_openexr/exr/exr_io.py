"""OpenEXR-based EXR header scanning.

Two-phase design:
- scan_exr_header(): reads headers only, no pixel I/O - fast for UI path
- Pixel loading is intentionally out of scope; downstream nodes call OpenImageIO directly
"""

from __future__ import annotations

import logging
import pathlib
from typing import TYPE_CHECKING, Any

import OpenEXR

from griptape_nodes_openexr.exr.exr_types import (
    _ATTR_CAP_DATE,
    _ATTR_CHROMATICITIES,
    _ATTR_CHUNK_COUNT,
    _ATTR_COMMENTS,
    _ATTR_COMPRESSION,
    _ATTR_DATA_WINDOW,
    _ATTR_DISPLAY_WINDOW,
    _ATTR_LINE_ORDER,
    _ATTR_OWNER,
    _ATTR_PIXEL_ASPECT_RATIO,
    _ATTR_SCREEN_WINDOW_CENTER,
    _ATTR_SCREEN_WINDOW_WIDTH,
    _ATTR_SOFTWARE,
    _ATTR_STORAGE_TYPE,
    _ATTR_TILE_DESCRIPTION,
    _ATTR_TIME_CODE,
    _EXR_COMPRESSION_MAP,
    _EXR_LEVEL_MODE_MAP,
    _EXR_LEVEL_ROUNDING_MODE_MAP,
    _EXR_LINE_ORDER_MAP,
    _EXR_PIXEL_TYPE_MAP,
    _EXR_STORAGE_TYPE_MAP,
    _HEADER_SKIP_ATTRS,
    Chromaticities,
    CompressionType,
    EXRChannelInfo,
    EXRData,
    EXRHeader,
    EXRPart,
    LevelModeType,
    LevelRoundingModeType,
    LineOrderType,
    PixelType,
    StorageType,
    TileDescription,
    WindowCoordinates,
    _convert_attribute_value,
    _format_time_code,
)

if TYPE_CHECKING:
    from griptape_nodes_openexr.exr.strategies.base import ChannelGroupingStrategy

logger = logging.getLogger("griptape_nodes")


def scan_exr_header(file_path: str | pathlib.Path, strategy: ChannelGroupingStrategy) -> EXRData:
    """Scan an EXR file's headers without loading pixel data.

    Opens the file, iterates all parts, and builds the full EXRData structure
    (headers + channel/layer metadata). Pixel chunks are never touched, making
    this fast even for large multi-part files.

    Args:
        file_path: Path to the EXR file
        strategy: Channel grouping strategy controlling name parsing and layer grouping

    Returns:
        EXRData with metadata for all parts; no pixel data loaded

    Raises:
        ValueError: If file_path is empty
        RuntimeError: If the file cannot be opened or parsed
    """
    if not file_path:
        msg = "file_path must not be empty"
        raise ValueError(msg)

    path = pathlib.Path(file_path)
    parts: list[EXRPart] = []
    try:
        with OpenEXR.File(str(path)) as exr_file:
            for part in exr_file.parts:
                channels = _build_channel_list(exr_file.channels(part.part_index))
                header = _build_header(part, part.header)
                parts.append(
                    EXRPart(
                        name=part.name(),
                        width=part.width(),
                        height=part.height(),
                        layers=[],
                        header=header,
                        channels=channels,
                    )
                )
    except Exception as e:
        msg = f"Failed to open EXR file '{path}': {e}"
        raise RuntimeError(msg) from e

    if not parts:
        msg = f"EXR file has no parts: {path}"
        raise ValueError(msg)

    for part in parts:
        part.layers = strategy.group_into_layers(part.channels)
    strategy.postprocess_parts(parts)
    return EXRData(parts=parts)


def _build_channel_list(exr_channels: dict[str, OpenEXR.Channel]) -> list[EXRChannelInfo]:
    """Build channel metadata list from an OpenEXR's Part Channels."""
    result: list[EXRChannelInfo] = []
    for channel_name, exr_channel in exr_channels.items():
        result.append(
            EXRChannelInfo(
                name=channel_name,
                pixel_type=_EXR_PIXEL_TYPE_MAP.get(exr_channel.type(), PixelType.FLOAT),
                x_sampling=exr_channel.xSampling,
                y_sampling=exr_channel.ySampling,
            )
        )
    return result


def _build_header(part: OpenEXR.Part, header: dict) -> EXRHeader:
    """Build an EXRHeader from OpenEXR header dictionary.

    Extracts all required and optional standard EXR attributes. Non-standard
    attributes not covered by typed fields land in EXRHeader.custom.
    """
    # DataWindow
    exr_data_window = _require_exr_attribute(header, _ATTR_DATA_WINDOW)
    data_window = _extract_window_coordinates(exr_data_window)

    # DisplayWindow
    exr_display_window = _require_exr_attribute(header, _ATTR_DISPLAY_WINDOW)
    display_window = _extract_window_coordinates(exr_display_window)

    # Compression
    exr_compression = _require_exr_attribute(header, _ATTR_COMPRESSION)
    compression: CompressionType = _EXR_COMPRESSION_MAP.get(exr_compression, CompressionType.NO_COMPRESSION)

    # Line Order
    exr_line_order = _require_exr_attribute(header, _ATTR_LINE_ORDER)
    line_order: LineOrderType = _EXR_LINE_ORDER_MAP.get(exr_line_order, LineOrderType.INCREASING_Y)

    # Storage Type (scanline/tiled)
    exr_storage_type = _require_exr_attribute(header, _ATTR_STORAGE_TYPE)
    storage_type = _EXR_STORAGE_TYPE_MAP.get(exr_storage_type, StorageType.SCANLINE_IMAGE)
    tile_description = (
        _extract_tile_description(header) if storage_type in (StorageType.TILED_IMAGE, StorageType.DEEP_TILED) else None
    )

    screen_center = _extract_screen_window_center(header)
    screen_width = _require_exr_attribute(header, _ATTR_SCREEN_WINDOW_WIDTH)
    pixel_aspect = _require_exr_attribute(header, _ATTR_PIXEL_ASPECT_RATIO)

    part_name = part.name()
    # The spec requires chunkCount for multipart/deep EXRs; be lenient for single-part files
    chunk_count_val = _optional_exr_attribute(header, _ATTR_CHUNK_COUNT, -1)
    chunk_count = chunk_count_val if chunk_count_val >= 0 else None

    chromaticities = _extract_chromaticities(header)
    time_code = _extract_time_code(header)

    owner = _optional_exr_attribute(header, _ATTR_OWNER, "")
    comments = _optional_exr_attribute(header, _ATTR_COMMENTS, "")
    capture_date = _optional_exr_attribute(header, _ATTR_CAP_DATE, "")
    software = _optional_exr_attribute(header, _ATTR_SOFTWARE, "")

    custom: dict[str, Any] = {
        attr_name: _convert_attribute_value(header[attr_name])
        for attr_name in header.keys()
        if attr_name not in _HEADER_SKIP_ATTRS
    }

    return EXRHeader(
        compression=compression,
        line_order=line_order,
        data_window=data_window,
        display_window=display_window,
        pixel_aspect_ratio=pixel_aspect,
        screen_window_center=screen_center,
        screen_window_width=screen_width,
        storage_type=storage_type,
        name=part_name,
        chunk_count=chunk_count,
        tile_description=tile_description,
        chromaticities=chromaticities,
        time_code=time_code,
        owner=owner,
        comments=comments,
        capture_date=capture_date,
        software=software,
        custom=custom,
    )


def _extract_window_coordinates(exr_coordinates: Any) -> WindowCoordinates:
    return WindowCoordinates(
        xmin=exr_coordinates[0][0],
        ymin=exr_coordinates[0][1],
        xmax=exr_coordinates[1][0],
        ymax=exr_coordinates[1][1],
    )


def _extract_tile_description(header: dict) -> TileDescription:
    """Build TileDescription from OpenEXR TileDescription."""
    exr_tile_description: OpenEXR.TileDescription = _require_exr_attribute(header, _ATTR_TILE_DESCRIPTION)

    return TileDescription(
        tile_width=exr_tile_description.xSize,
        tile_height=exr_tile_description.ySize,
        level_mode=_EXR_LEVEL_MODE_MAP.get(exr_tile_description.mode, LevelModeType.ONE_LEVEL),
        rounding_mode=_EXR_LEVEL_ROUNDING_MODE_MAP.get(exr_tile_description.roundingMode, LevelRoundingModeType.ROUND_UP),
    )


def _extract_screen_window_center(header: dict[str, Any]) -> tuple[float, float]:
    """Extract screenWindowCenter from header attributes."""
    exr_screen_window_center = _optional_exr_attribute(header, _ATTR_SCREEN_WINDOW_CENTER, (0.0, 0.0))
    return exr_screen_window_center[0], exr_screen_window_center[1]


def _extract_chromaticities(header: dict[str, Any]) -> Chromaticities | None:
    """Extract chromaticities attribute if present (8 floats: rx ry gx gy bx by wx wy)."""
    exr_chromaticities = _optional_exr_attribute(header, _ATTR_CHROMATICITIES, None)
    if exr_chromaticities is None:
        return None

    try:
        if len(exr_chromaticities) >= 8:  # noqa: PLR2004
            return Chromaticities(
                red_x=exr_chromaticities[0],
                red_y=exr_chromaticities[1],
                green_x=exr_chromaticities[2],
                green_y=exr_chromaticities[3],
                blue_x=exr_chromaticities[4],
                blue_y=exr_chromaticities[5],
                white_x=exr_chromaticities[6],
                white_y=exr_chromaticities[7],
            )
        return None
    except Exception:
        logger.exception("Could not parse chromaticities attribute: %s", exr_chromaticities)
        return None


def _extract_time_code(header: dict[str, Any]) -> str | None:
    """Extract and format timeCode attribute."""
    time_code = _optional_exr_attribute(header, _ATTR_TIME_CODE, None)
    if time_code is None:
        return None
    return _format_time_code(time_code)


def _require_exr_attribute(header: dict, name: str) -> Any:
    """Get a required attribute from an OpenEXR Part Header."""
    sentinel = "__MISSING__"
    value = header.get(name, sentinel)
    if value == sentinel:
        msg = f"Required EXR header attribute '{name}' is missing"
        raise ValueError(msg)
    return value


def _optional_exr_attribute(header: dict[str, Any], name: str, default: Any) -> Any:
    """Get an optional attribute from an OpenEXR Part Header, returning default if absent."""
    return header.get(name, default)
