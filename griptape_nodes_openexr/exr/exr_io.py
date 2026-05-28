"""OpenEXR-based EXR file scanning.

Two-phase design:
- scan_exr_header(): reads metadata for all parts; pixel loading is controlled by the
  `header_only` flag (configurable via the `openexr.header_only` engine setting).
- When header_only=True (default): headers are read without touching pixel data — fast
  even on large multi-part files, but channel pixel types default to HALF when the
  OpenEXR binding cannot inspect the pixel arrays.
- When header_only=False: the full file is opened, pixel types are read accurately from
  the file, at the cost of loading pixel data into memory.
"""

from __future__ import annotations

import logging
from typing import Any

import numpy as np
import OpenEXR
from griptape_nodes.files.file import File

from griptape_nodes_openexr.exr.exr_types import (
    _ATTR_CAP_DATE,
    _ATTR_CHANNELS,
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

logger = logging.getLogger("griptape_nodes")


def scan_exr_header(file_path: str, *, header_only: bool = True) -> EXRData:
    """Scan an EXR file and return metadata for all parts.

    Opens the file, iterates all parts, and builds the full EXRData structure
    (headers + channel metadata).

    Args:
        file_path: Path to the EXR file
        header_only: When True (default), only header data is read — pixel chunks are
            never loaded, making this fast even for large multi-part files.  Channel
            pixel types default to HALF when the OpenEXR binding cannot inspect the
            pixel arrays in this mode.  When False, the full file is opened so pixel
            types are read accurately from the file at the cost of loading pixel data.

    Returns:
        EXRData with metadata for all parts

    Raises:
        ValueError: If file_path is empty
        RuntimeError: If the file cannot be opened or parsed
    """
    if not file_path:
        msg = "file_path must not be empty"
        raise ValueError(msg)

    resolved_path = File(str(file_path)).resolve()
    parts: list[EXRPart] = []
    try:
        # TODO: revisit direct file I/O once artifact manager is pluggable https://github.com/griptape-ai/griptape-nodes-library-openexr/issues/9
        with OpenEXR.File(resolved_path, header_only=header_only) as exr_file:
            for part in exr_file.parts:
                # In header_only mode part.width()/height() return 0 and
                # exr_file.channels() returns {}; read both from the header.
                raw_header = part.header
                channels = _build_channel_list_from_header(raw_header.get(_ATTR_CHANNELS, []))
                header = _build_header(part, raw_header)
                dw = header.data_window
                width = int(dw.xmax - dw.xmin + 1)
                height = int(dw.ymax - dw.ymin + 1)
                parts.append(
                    EXRPart(
                        name=part.name(),
                        width=width,
                        height=height,
                        header=header,
                        channels=channels,
                    )
                )
    except Exception as e:
        msg = f"Failed to open EXR file '{resolved_path}': {e}"
        raise RuntimeError(msg) from e

    if not parts:
        msg = f"EXR file has no parts: {resolved_path}"
        raise ValueError(msg)

    return EXRData(parts=parts)


def load_exr_channels(
    file_path: str,
    part_index: int,
    channel_names: list[str] | None = None,
) -> dict[str, np.ndarray]:
    """Read pixel arrays for one part of an EXR file.

    Args:
        file_path: Engine path string (may contain macros like ``{{workspace}}/shot.exr``).
            Resolved via ``File(...).resolve()`` before opening.
        part_index: Zero-based index of the part to read.
        channel_names: Channels to load. ``None`` loads all channels in the part.

    Returns:
        Mapping of channel name to float32 array of shape ``(height, width)``.

    Raises:
        ValueError: For empty ``file_path``, out-of-range ``part_index``, or
            unknown ``channel_names``.
        RuntimeError: If the file cannot be opened or read.
    """
    if not file_path:
        msg = "file_path must not be empty"
        raise ValueError(msg)

    resolved_path = File(str(file_path)).resolve()
    try:
        # TODO: revisit direct file I/O once artifact manager is pluggable https://github.com/griptape-ai/griptape-nodes-library-openexr/issues/9
        exr = OpenEXR.File(resolved_path, separate_channels=True)
    except Exception as e:
        msg = f"Failed to read EXR file '{resolved_path}': {e}"
        raise RuntimeError(msg) from e

    with exr as exr_file:
        # Validate the parts and channels before doing any pixel conversion.
        parts = exr_file.parts
        if part_index < 0 or part_index >= len(parts):
            msg = f"part_index {part_index} is out of range (file has {len(parts)} part(s))"
            raise ValueError(msg)

        part = parts[part_index]
        if channel_names is not None:
            unknown = set(channel_names) - part.channels.keys()
            if unknown:
                msg = f"Unknown channel(s) {sorted(unknown)!r} in part {part_index}"
                raise ValueError(msg)

        names_to_load = channel_names if channel_names is not None else list(part.channels)
        try:
            return {name: part.channels[name].pixels.astype(np.float32, copy=False) for name in names_to_load}
        except Exception as e:
            msg = f"Failed to read EXR file '{resolved_path}': {e}"
            raise RuntimeError(msg) from e


def write_exr_channels(
    output_path: str,
    channels: dict[str, np.ndarray],
    compression: OpenEXR.Compression | None = None,
    pixel_type: str = "half",
) -> None:
    """Write a dict of channel arrays to a single-part scanline EXR file.

    Args:
        output_path: Filesystem path for the output file.
        channels: Mapping of channel name to float32 (or any numeric) array of
            shape ``(height, width)``.  Arrays are converted to float16 or float32
            depending on ``pixel_type`` before writing.
        compression: OpenEXR compression constant (e.g. ``OpenEXR.ZIP_COMPRESSION``).
            Defaults to ``OpenEXR.ZIP_COMPRESSION`` when ``None``.
        pixel_type: ``"half"`` (float16, default) or ``"float"`` (float32).

    Raises:
        ValueError: If ``output_path`` is empty.
        RuntimeError: If the file cannot be written.
    """
    if not output_path:
        msg = "output_path must not be empty"
        raise ValueError(msg)

    if compression is None:
        compression = OpenEXR.ZIP_COMPRESSION

    dtype = np.float16 if pixel_type == "half" else np.float32
    converted = {name: arr.astype(dtype, copy=False) for name, arr in channels.items()}

    header = {
        "compression": compression,
        "type": OpenEXR.scanlineimage,
    }

    try:
        # TODO: revisit direct file I/O once artifact manager is pluggable https://github.com/griptape-ai/griptape-nodes-library-openexr/issues/9
        OpenEXR.File(header, converted).write(output_path)
    except Exception as e:
        msg = f"Failed to write EXR file '{output_path}': {e}"
        raise RuntimeError(msg) from e


def _build_channel_list_from_header(exr_channels: list[OpenEXR.Channel]) -> list[EXRChannelInfo]:
    """Build channel metadata list from the header 'channels' attribute.

    In header_only mode, exr_file.channels() returns an empty dict, so channels
    are read from part.header['channels'] — a list of Channel objects with
    .name, .xSampling, .ySampling. Pixel type is not accessible via .type() in
    this mode (the binding inspects the pixels array, which is empty), so it
    defaults to HALF, which covers the vast majority of VFX EXR channels.
    """
    result: list[EXRChannelInfo] = []
    for exr_channel in exr_channels:
        try:
            pixel_type = _EXR_PIXEL_TYPE_MAP.get(exr_channel.type(), PixelType.HALF)
        except Exception:
            pixel_type = PixelType.HALF
        result.append(
            EXRChannelInfo(
                name=exr_channel.name,
                pixel_type=pixel_type,
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
    software = _optional_exr_attribute(header, _ATTR_SOFTWARE, "") or _optional_exr_attribute(header, "Software", "")

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
        rounding_mode=_EXR_LEVEL_ROUNDING_MODE_MAP.get(
            exr_tile_description.roundingMode, LevelRoundingModeType.ROUND_UP
        ),
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
