"""OIIO-based EXR header scanning.

Two-phase design:
- scan_exr_header(): reads headers only, no pixel I/O - fast for UI path
- Pixel loading is intentionally out of scope; downstream nodes call OIIO directly

All OIIO APIs used here (ImageInput, seek_subimage, spec, extra_attribs, tile_width)
are stable across OIIO 2.3+ and 3.x (VFX Reference Platform 2022 onwards).
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

import OpenImageIO as oiio  # type: ignore[import-not-found]

from griptape_nodes_openexr.exr.exr_types import (
    _ATTR_CAP_DATE,
    _ATTR_CHROMATICITIES,
    _ATTR_CHUNK_COUNT,
    _ATTR_COMMENTS,
    _ATTR_COMPRESSION,
    _ATTR_LINE_ORDER,
    _ATTR_NAME,
    _ATTR_OWNER,
    _ATTR_PIXEL_ASPECT_RATIO,
    _ATTR_SCREEN_WINDOW_CENTER,
    _ATTR_SCREEN_WINDOW_WIDTH,
    _ATTR_SOFTWARE,
    _ATTR_TIME_CODE,
    _HEADER_SKIP_ATTRS,
    _OIIO_COMPRESSION_MAP,
    _OIIO_LINE_ORDER_MAP,
    _OIIO_PIXEL_TYPE_MAP,
    Chromaticities,
    EXRChannelInfo,
    EXRData,
    EXRHeader,
    EXRPart,
    StorageType,
    TileDescription,
    WindowCoordinates,
    _convert_attribute_value,
    _format_time_code,
    _map_oiio_string,
)

if TYPE_CHECKING:
    from griptape_nodes_openexr.exr.strategies.base import ChannelGroupingStrategy

logger = logging.getLogger("griptape_nodes")


def scan_exr_header(file_path: str, strategy: ChannelGroupingStrategy) -> EXRData:
    """Scan an EXR file's headers without loading pixel data.

    Opens the file, iterates all parts via OIIO subimage seeking, and builds
    the full EXRData structure (headers + channel/layer metadata). Pixel chunks
    are never touched, making this fast even for large multi-part files.

    Args:
        file_path: Path to the EXR file
        strategy: Channel grouping strategy controlling name parsing and layer grouping

    Returns:
        EXRData with metadata for all parts; no pixel data loaded

    Raises:
        ValueError: If file_path is empty or the file has no readable parts
        RuntimeError: If OIIO cannot open the file
    """
    if not file_path:
        msg = "file_path must not be empty"
        raise ValueError(msg)

    inp = oiio.ImageInput.open(file_path)
    if not inp:
        msg = f"Failed to open EXR file '{file_path}': {oiio.geterror()}"
        raise RuntimeError(msg)

    try:
        parts: list[EXRPart] = []
        subimage_idx = 0

        while inp.seek_subimage(subimage_idx, 0):
            spec = inp.spec()
            channels = _build_channel_list(spec)

            if not channels:
                msg = f"EXR part {subimage_idx} has no channels: {file_path}"
                raise ValueError(msg)

            header = _build_header_from_spec(spec)
            data_win = header.data_window
            width = data_win.xmax - data_win.xmin + 1
            height = data_win.ymax - data_win.ymin + 1

            parts.append(
                EXRPart(
                    channels=channels,
                    layers=strategy.group_into_layers(channels),
                    header=header,
                    index=subimage_idx,
                    width=width,
                    height=height,
                )
            )
            subimage_idx += 1

        if not parts:
            msg = f"EXR file has no parts: {file_path}"
            raise ValueError(msg)

        strategy.postprocess_parts(parts)
        return EXRData(parts=parts)

    finally:
        inp.close()


def _build_channel_list(spec: Any) -> list[EXRChannelInfo]:
    """Build channel metadata list from an OIIO ImageSpec."""
    channels: list[EXRChannelInfo] = []
    for ch_idx in range(spec.nchannels):
        ch_name = spec.channelnames[ch_idx]
        ch_format = str(spec.channelformat(ch_idx))
        pixel_type = _map_oiio_string(_OIIO_PIXEL_TYPE_MAP, ch_format, "pixel type")
        channels.append(
            EXRChannelInfo(
                name=ch_name,
                pixel_type=pixel_type,
                channel_index=ch_idx,
                x_sampling=1,
                y_sampling=1,
            )
        )
    return channels


def _build_header_from_spec(spec: Any) -> EXRHeader:
    """Build an EXRHeader from an OIIO ImageSpec.

    Extracts all required and optional standard EXR attributes. Non-standard
    attributes not covered by typed fields land in EXRHeader.custom.
    """
    data_window = WindowCoordinates(
        xmin=spec.x,
        ymin=spec.y,
        xmax=spec.x + spec.width - 1,
        ymax=spec.y + spec.height - 1,
    )
    display_window = WindowCoordinates(
        xmin=spec.full_x,
        ymin=spec.full_y,
        xmax=spec.full_x + spec.full_width - 1,
        ymax=spec.full_y + spec.full_height - 1,
    )

    comp_str = _require_string_attribute(spec, _ATTR_COMPRESSION)
    compression = _map_oiio_string(_OIIO_COMPRESSION_MAP, comp_str, "compression")

    lo_str = _require_string_attribute(spec, _ATTR_LINE_ORDER)
    line_order = _map_oiio_string(_OIIO_LINE_ORDER_MAP, lo_str, "line order")

    storage_type = _detect_storage_type(spec)
    tile_description = _extract_tile_description(spec) if spec.tile_width > 0 else None

    screen_center = _extract_screen_window_center(spec)
    screen_width = spec.get_float_attribute(_ATTR_SCREEN_WINDOW_WIDTH, 1.0)
    pixel_aspect = spec.get_float_attribute(_ATTR_PIXEL_ASPECT_RATIO, 1.0)

    part_name = spec.get_string_attribute(_ATTR_NAME, "")
    chunk_count_val = spec.get_int_attribute(_ATTR_CHUNK_COUNT, -1)
    chunk_count = chunk_count_val if chunk_count_val >= 0 else None

    chromaticities = _extract_chromaticities(spec)
    time_code = _extract_time_code(spec)

    owner = _get_optional_string(spec, _ATTR_OWNER)
    comments = _get_optional_string(spec, _ATTR_COMMENTS)
    capture_date = _get_optional_string(spec, _ATTR_CAP_DATE)
    software = _get_optional_string(spec, _ATTR_SOFTWARE)

    custom: dict[str, Any] = {
        attr.name: _convert_attribute_value(attr.value)
        for attr in spec.extra_attribs
        if attr.name not in _HEADER_SKIP_ATTRS
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


def _detect_storage_type(spec: Any) -> StorageType:
    """Infer storage type from OIIO spec tile dimensions and deep flag."""
    is_deep = getattr(spec, "deep", False)
    is_tiled = spec.tile_width > 0
    if is_deep and is_tiled:
        return StorageType.DEEP_TILED
    if is_deep:
        return StorageType.DEEP_SCANLINE
    if is_tiled:
        return StorageType.TILED_IMAGE
    return StorageType.SCANLINE_IMAGE


def _extract_tile_description(spec: Any) -> TileDescription:
    """Build TileDescription from tiled OIIO spec."""
    # OIIO doesn't expose level_mode/rounding_mode directly as strings;
    # derive from presence of mip/rip levels via nativeattrib if available.
    level_mode = "ONE_LEVEL"
    rounding_mode = "ROUND_DOWN"

    for attr in spec.extra_attribs:
        if attr.name == "openexr:levelMode":
            level_map = {0: "ONE_LEVEL", 1: "MIPMAP_LEVELS", 2: "RIPMAP_LEVELS"}
            level_mode = level_map.get(int(attr.value), "ONE_LEVEL")
        elif attr.name == "openexr:roundingMode":
            rounding_map = {0: "ROUND_DOWN", 1: "ROUND_UP"}
            rounding_mode = rounding_map.get(int(attr.value), "ROUND_DOWN")

    return TileDescription(
        tile_width=spec.tile_width,
        tile_height=spec.tile_height,
        level_mode=level_mode,
        rounding_mode=rounding_mode,
    )


def _extract_screen_window_center(spec: Any) -> tuple[float, float]:
    """Extract screenWindowCenter from extra_attribs."""
    for attr in spec.extra_attribs:
        if attr.name == _ATTR_SCREEN_WINDOW_CENTER:
            v = attr.value
            return (float(v[0]), float(v[1]))
    return (0.0, 0.0)


def _extract_chromaticities(spec: Any) -> Chromaticities | None:
    """Extract chromaticities attribute if present (8 floats: rx ry gx gy bx by wx wy)."""
    for attr in spec.extra_attribs:
        if attr.name == _ATTR_CHROMATICITIES:
            v = attr.value
            try:
                floats = [float(x) for x in v]
                if len(floats) >= 8:  # noqa: PLR2004
                    return Chromaticities(
                        red_x=floats[0],
                        red_y=floats[1],
                        green_x=floats[2],
                        green_y=floats[3],
                        blue_x=floats[4],
                        blue_y=floats[5],
                        white_x=floats[6],
                        white_y=floats[7],
                    )
            except (TypeError, ValueError):
                logger.debug("Could not parse chromaticities attribute: %s", v)
    return None


def _extract_time_code(spec: Any) -> str | None:
    """Extract and format timeCode attribute."""
    for attr in spec.extra_attribs:
        if attr.name == _ATTR_TIME_CODE:
            return _format_time_code(attr.value)
    return None


def _require_string_attribute(spec: Any, name: str) -> str:
    """Get a required string attribute from an OIIO ImageSpec."""
    sentinel = "__MISSING__"
    value = spec.get_string_attribute(name, sentinel)
    if value == sentinel:
        msg = f"Required EXR header attribute '{name}' is missing"
        raise ValueError(msg)
    return value


def _get_optional_string(spec: Any, name: str) -> str | None:
    """Get an optional string attribute, returning None if absent."""
    sentinel = "__MISSING__"
    value = spec.get_string_attribute(name, sentinel)
    return value if value != sentinel else None
