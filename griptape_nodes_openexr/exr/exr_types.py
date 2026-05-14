"""EXR data structures for header parsing.

Core types ported from griptape-nodes-library-opencolorio (James C),
extended with TileDescription, Chromaticities, and richer EXRHeader fields.
Uses OpenImageIO for I/O - no pixel data is loaded here.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, NamedTuple

# --- Enums ---


class CompressionType(StrEnum):
    NO_COMPRESSION = "NO_COMPRESSION"
    RLE_COMPRESSION = "RLE_COMPRESSION"
    ZIPS_COMPRESSION = "ZIPS_COMPRESSION"
    ZIP_COMPRESSION = "ZIP_COMPRESSION"
    PIZ_COMPRESSION = "PIZ_COMPRESSION"
    PXR24_COMPRESSION = "PXR24_COMPRESSION"
    B44_COMPRESSION = "B44_COMPRESSION"
    B44A_COMPRESSION = "B44A_COMPRESSION"
    DWAA_COMPRESSION = "DWAA_COMPRESSION"
    DWAB_COMPRESSION = "DWAB_COMPRESSION"
    HTJ2K32_COMPRESSION = "HTJ2K32_COMPRESSION"
    HTJ2K256_COMPRESSION = "HTJ2K256_COMPRESSION"


class LineOrderType(StrEnum):
    INCREASING_Y = "INCREASING_Y"
    DECREASING_Y = "DECREASING_Y"
    RANDOM_Y = "RANDOM_Y"


class StorageType(StrEnum):
    SCANLINE_IMAGE = "scanlineimage"
    TILED_IMAGE = "tiledimage"
    DEEP_SCANLINE = "deepscanline"
    DEEP_TILED = "deeptiled"


class PixelType(StrEnum):
    HALF = "half"
    FLOAT = "float"
    UINT = "uint"


# --- OIIO string → StrEnum mappings ---

_OIIO_COMPRESSION_MAP: dict[str, CompressionType] = {
    "none": CompressionType.NO_COMPRESSION,
    "rle": CompressionType.RLE_COMPRESSION,
    "zips": CompressionType.ZIPS_COMPRESSION,
    "zip": CompressionType.ZIP_COMPRESSION,
    "piz": CompressionType.PIZ_COMPRESSION,
    "pxr24": CompressionType.PXR24_COMPRESSION,
    "b44": CompressionType.B44_COMPRESSION,
    "b44a": CompressionType.B44A_COMPRESSION,
    "dwaa": CompressionType.DWAA_COMPRESSION,
    "dwab": CompressionType.DWAB_COMPRESSION,
}

_OIIO_PIXEL_TYPE_MAP: dict[str, PixelType] = {
    "half": PixelType.HALF,
    "float": PixelType.FLOAT,
    "uint32": PixelType.UINT,
    "uint16": PixelType.UINT,
    "uint8": PixelType.UINT,
}

_OIIO_LINE_ORDER_MAP: dict[str, LineOrderType] = {
    "increasingY": LineOrderType.INCREASING_Y,
    "decreasingY": LineOrderType.DECREASING_Y,
    "randomY": LineOrderType.RANDOM_Y,
}

# OIIO attribute names
_ATTR_COMPRESSION = "compression"
_ATTR_LINE_ORDER = "openexr:lineOrder"
_ATTR_CHUNK_COUNT = "openexr:chunkCount"
_ATTR_NAME = "name"
_ATTR_PIXEL_ASPECT_RATIO = "PixelAspectRatio"
_ATTR_SCREEN_WINDOW_CENTER = "screenWindowCenter"
_ATTR_SCREEN_WINDOW_WIDTH = "screenWindowWidth"
_ATTR_OWNER = "owner"
_ATTR_COMMENTS = "comments"
_ATTR_CAP_DATE = "capDate"
_ATTR_SOFTWARE = "Software"
_ATTR_TIME_CODE = "timeCode"
_ATTR_CHROMATICITIES = "chromaticities"

# Attributes handled as dedicated fields - excluded from EXRHeader.custom
_HEADER_SKIP_ATTRS: set[str] = {
    _ATTR_COMPRESSION,
    _ATTR_LINE_ORDER,
    _ATTR_CHUNK_COUNT,
    _ATTR_NAME,
    "oiio:subimagename",
    "oiio:subimages",
    _ATTR_PIXEL_ASPECT_RATIO,
    _ATTR_SCREEN_WINDOW_CENTER,
    _ATTR_SCREEN_WINDOW_WIDTH,
    _ATTR_OWNER,
    _ATTR_COMMENTS,
    _ATTR_CAP_DATE,
    _ATTR_SOFTWARE,
    "software",  # OIIO case-insensitive get_string_attribute matches "Software" but raw attr may be lowercase
    _ATTR_TIME_CODE,
    _ATTR_CHROMATICITIES,
}


def _map_oiio_string(mapping: dict[str, Any], oiio_value: str, label: str) -> Any:
    result = mapping.get(oiio_value)
    if result is not None:
        return result
    valid = ", ".join(f"'{k}'" for k in mapping)
    msg = f"Unsupported {label}: '{oiio_value}'. Supported values: {valid}"
    raise ValueError(msg)


# --- Coordinate types ---


class WindowCoordinates(NamedTuple):
    xmin: int
    ymin: int
    xmax: int
    ymax: int


class ChannelNameParts(NamedTuple):
    layer_name: str
    channel_name: str


class NormalizedWindows(NamedTuple):
    data: WindowCoordinates
    display: WindowCoordinates


# --- EXR-specific metadata types ---


@dataclass
class TileDescription:
    """Tile layout for tiled EXR images.

    Attributes:
        tile_width: Width of each tile in pixels
        tile_height: Height of each tile in pixels
        level_mode: ONE_LEVEL | MIPMAP_LEVELS | RIPMAP_LEVELS
        rounding_mode: ROUND_DOWN | ROUND_UP
    """

    tile_width: int
    tile_height: int
    level_mode: str
    rounding_mode: str


@dataclass
class Chromaticities:
    """CIE xy chromaticity coordinates for primary colours and white point.

    Present in EXR files that declare their colour primaries explicitly.
    Absence does not imply sRGB/Rec.709 - check with your pipeline.
    """

    red_x: float
    red_y: float
    green_x: float
    green_y: float
    blue_x: float
    blue_y: float
    white_x: float
    white_y: float


# --- Channel / layer structures ---


@dataclass
class EXRChannelInfo:
    """Channel metadata (no pixel data).

    Attributes:
        name: Full channel name as stored in the EXR (e.g. "beauty.R")
        pixel_type: Data type for this channel
        channel_index: Index in the part's channel list (for OIIO reads)
        x_sampling: Horizontal subsampling factor (1 = full resolution)
        y_sampling: Vertical subsampling factor (1 = full resolution)
    """

    name: str
    pixel_type: PixelType
    channel_index: int
    x_sampling: int
    y_sampling: int


@dataclass
class EXRLayer:
    """Channels grouped by common name prefix.

    Attributes:
        name: Layer prefix (empty string = default/unnamed layer)
        channels: Channels belonging to this layer
    """

    name: str
    channels: list[EXRChannelInfo]


# --- Header ---


@dataclass
class EXRHeader:
    """Full header metadata for a single EXR part.

    Attributes:
        compression: Compression algorithm
        line_order: Scanline storage order
        data_window: Bounding box of actual pixel data
        display_window: Intended display region (may differ from data_window)
        pixel_aspect_ratio: Pixel width / height ratio (1.0 = square)
        screen_window_center: Camera projection centre in NDC
        screen_window_width: Camera projection width in NDC
        storage_type: scanlineimage / tiledimage / deepscanline / deeptiled
        name: Part name (empty for single-part or unnamed parts)
        chunk_count: Number of chunks (multi-part files); None for single-part
        tile_description: Tile layout; None for scanline images
        chromaticities: Colour primaries; None if absent from header
        time_code: Editorial timecode string HH:MM:SS:FF; None if absent
        owner: Asset owner string; None if absent
        comments: Free-text comments; None if absent
        capture_date: Capture/render date string; None if absent
        software: Authoring application name; None if absent
        custom: Non-standard attributes not covered by the fields above
    """

    compression: CompressionType
    line_order: LineOrderType
    data_window: WindowCoordinates
    display_window: WindowCoordinates
    pixel_aspect_ratio: float
    screen_window_center: tuple[float, float]
    screen_window_width: float
    storage_type: StorageType
    name: str
    chunk_count: int | None
    tile_description: TileDescription | None
    chromaticities: Chromaticities | None
    time_code: str | None
    owner: str | None
    comments: str | None
    capture_date: str | None
    software: str | None
    custom: dict[str, Any]


# --- Part / file ---


@dataclass
class EXRPart:
    """Single part from an OpenEXR file.

    Attributes:
        channels: All channels in this part
        layers: Channels grouped by layer prefix (strategy-dependent)
        header: Full header metadata
        index: Zero-based part index within the file
        width: Image width in pixels
        height: Image height in pixels
    """

    channels: list[EXRChannelInfo]
    layers: list[EXRLayer]
    header: EXRHeader
    index: int
    width: int
    height: int


@dataclass
class EXRData:
    """All header data extracted from an OpenEXR file.

    No pixel data is loaded. Use a downstream node to access channel pixels.

    Attributes:
        parts: One entry per file part (single-part files have exactly one)
    """

    parts: list[EXRPart]


# --- Channel name parsing (Nuke-compatible algorithm) ---


def _sanitize_name_part(part: str) -> str:
    """Strip leading digits and replace non-alphanumeric chars with underscores."""
    i = 0
    while i < len(part) and part[i].isdigit():
        i += 1
    part = part[i:]
    return "".join(c if c.isalnum() else "_" for c in part)


def parse_channel_name(full_name: str) -> ChannelNameParts:
    """Parse an EXR channel name into layer and channel components.

    Algorithm matches Nuke's ExrChannelNameToNuke.cpp:
    - Split on '.' with at most 2 splits (max 3 parts)
    - Strip leading digits and sanitize each part
    - All-but-last parts form the layer name (joined with '_')
    - Last part is the channel name
    - Layer name "Ci" maps to the default layer (RenderMan convention)

    Examples:
        "R" → ("", "R")
        "beauty.R" → ("beauty", "R")
        "View Layer.AO.R" → ("View_Layer_AO", "R")
        "Ci.R" → ("", "R")
    """
    parts = full_name.split(".", maxsplit=2)
    sanitized: list[str] = [s for p in parts if (s := _sanitize_name_part(p))]

    if len(sanitized) <= 1:
        return ChannelNameParts(layer_name="", channel_name=sanitized[0] if sanitized else "unnamed")

    channel_name = sanitized[-1]
    layer_name = "_".join(sanitized[:-1])
    if layer_name == "Ci":
        layer_name = ""

    return ChannelNameParts(layer_name=layer_name, channel_name=channel_name)


def group_channels_into_layers(channels: list[EXRChannelInfo]) -> list[EXRLayer]:
    """Group channels by layer prefix. Default layer (empty name) sorts first."""
    layers_dict: dict[str, list[EXRChannelInfo]] = {}
    for channel in channels:
        layer_name = parse_channel_name(channel.name).layer_name
        if layer_name not in layers_dict:
            layers_dict[layer_name] = []
        layers_dict[layer_name].append(channel)

    layers = [EXRLayer(name=name, channels=chs) for name, chs in layers_dict.items()]
    layers.sort(key=lambda layer: (layer.name != "", layer.name))
    return layers


# --- Window normalisation ---


def _normalize_windows(
    data_window: WindowCoordinates,
    display_window: WindowCoordinates,
) -> NormalizedWindows:
    """Shift both windows so the display origin lands at (0, 0).

    Matches Nuke's offset_negative_display_window behavior. Preserves the
    relative offset between data and display windows.
    """
    x_offset = display_window.xmin
    y_offset = display_window.ymin

    if x_offset == 0 and y_offset == 0:
        return NormalizedWindows(data=data_window, display=display_window)

    return NormalizedWindows(
        data=WindowCoordinates(
            xmin=data_window.xmin - x_offset,
            ymin=data_window.ymin - y_offset,
            xmax=data_window.xmax - x_offset,
            ymax=data_window.ymax - y_offset,
        ),
        display=WindowCoordinates(
            xmin=0,
            ymin=0,
            xmax=display_window.xmax - x_offset,
            ymax=display_window.ymax - y_offset,
        ),
    )


# --- Legacy multi-part handling ---


def _apply_legacy_part_name_prefix(parts: list[EXRPart]) -> None:
    """Prefix part name as layer name for legacy multi-part files.

    Legacy files store the layer name in the part header rather than using
    dot-notation in channel names. Detected when no channel in any part
    contains a '.' and no 'fullLayerNames' attribute is set.

    Mutates parts in place, re-grouping layers after renaming channels.
    """
    if any(part.header.custom.get("fullLayerNames") for part in parts):
        return
    if any("." in ch.name for part in parts for ch in part.channels):
        return

    for part in parts:
        part_name = part.header.name
        if not part_name:
            continue
        for ch in part.channels:
            ch.name = f"{part_name}.{ch.name}"
        part.layers = group_channels_into_layers(part.channels)


# --- Attribute value conversion ---


def _convert_attribute_value(value: Any) -> Any:
    """Normalise an OIIO attribute value to a plain Python type."""
    if isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    if isinstance(value, (tuple, list)):
        return [_convert_attribute_value(v) for v in value]
    if hasattr(value, "item"):
        return value.item()
    if hasattr(value, "tolist"):
        return value.tolist()
    return str(value)


# --- Time code formatting ---

_TIMECODE_RE = re.compile(r"(\d+):(\d+):(\d+):(\d+)")


def _format_time_code(raw: Any) -> str | None:
    """Convert an OIIO timeCode attribute to HH:MM:SS:FF string."""
    if raw is None:
        return None
    s = str(raw)
    m = _TIMECODE_RE.search(s)
    if m:
        return ":".join(m.groups())
    converted = _convert_attribute_value(raw)
    return str(converted) if converted is not None else None
