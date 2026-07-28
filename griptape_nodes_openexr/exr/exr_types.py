"""EXR data structures for header parsing.

Core types ported from griptape-nodes-library-opencolorio (James C),
extended with TileDescription, Chromaticities, and richer EXRHeader fields.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, NamedTuple

import OpenEXR

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


class LevelModeType(StrEnum):
    ONE_LEVEL = "ONE_LEVEL"
    MIPMAP_LEVELS = "MIPMAP_LEVELS"
    RIPMAP_LEVELS = "RIPMAP_LEVELS"


class LevelRoundingModeType(StrEnum):
    ROUND_DOWN = "ROUND_DOWN"
    ROUND_UP = "ROUND_UP"


# --- OpenEXR types -> StrEnum mappings ---
_EXR_COMPRESSION_MAP: dict[OpenEXR.Compression, CompressionType] = {
    OpenEXR.NO_COMPRESSION: CompressionType.NO_COMPRESSION,
    OpenEXR.RLE_COMPRESSION: CompressionType.RLE_COMPRESSION,
    OpenEXR.ZIPS_COMPRESSION: CompressionType.ZIPS_COMPRESSION,
    OpenEXR.ZIP_COMPRESSION: CompressionType.ZIP_COMPRESSION,
    OpenEXR.PIZ_COMPRESSION: CompressionType.PIZ_COMPRESSION,
    OpenEXR.PXR24_COMPRESSION: CompressionType.PXR24_COMPRESSION,
    OpenEXR.B44_COMPRESSION: CompressionType.B44_COMPRESSION,
    OpenEXR.B44A_COMPRESSION: CompressionType.B44A_COMPRESSION,
    OpenEXR.DWAA_COMPRESSION: CompressionType.DWAA_COMPRESSION,
    OpenEXR.DWAB_COMPRESSION: CompressionType.DWAB_COMPRESSION,
}

_EXR_PIXEL_TYPE_MAP: dict[OpenEXR.PixelType, PixelType] = {
    OpenEXR.PixelType.HALF: PixelType.HALF,
    OpenEXR.PixelType.FLOAT: PixelType.FLOAT,
    OpenEXR.PixelType.UINT: PixelType.UINT,
}

_EXR_LINE_ORDER_MAP: dict[OpenEXR.LineOrder, LineOrderType] = {
    OpenEXR.LineOrder.INCREASING_Y: LineOrderType.INCREASING_Y,
    OpenEXR.LineOrder.DECREASING_Y: LineOrderType.DECREASING_Y,
    OpenEXR.LineOrder.RANDOM_Y: LineOrderType.RANDOM_Y,
}

_EXR_STORAGE_TYPE_MAP: dict[OpenEXR.Storage, StorageType] = {
    OpenEXR.Storage.scanlineimage: StorageType.SCANLINE_IMAGE,
    OpenEXR.Storage.tiledimage: StorageType.TILED_IMAGE,
    OpenEXR.Storage.deepscanline: StorageType.DEEP_SCANLINE,
    OpenEXR.Storage.deeptile: StorageType.DEEP_TILED,
}

_EXR_LEVEL_MODE_MAP: dict[OpenEXR.LevelMode, LevelModeType] = {
    OpenEXR.LevelMode.ONE_LEVEL: LevelModeType.ONE_LEVEL,
    OpenEXR.LevelMode.MIPMAP_LEVELS: LevelModeType.MIPMAP_LEVELS,
    OpenEXR.LevelMode.RIPMAP_LEVELS: LevelModeType.RIPMAP_LEVELS,
}

_EXR_LEVEL_ROUNDING_MODE_MAP: dict[OpenEXR.LevelRoundingMode, LevelRoundingModeType] = {
    OpenEXR.LevelRoundingMode.ROUND_UP: LevelRoundingModeType.ROUND_UP,
    OpenEXR.LevelRoundingMode.ROUND_DOWN: LevelRoundingModeType.ROUND_DOWN,
}

# OpenEXR attribute names
_ATTR_DATA_WINDOW = "dataWindow"
_ATTR_DISPLAY_WINDOW = "displayWindow"
_ATTR_COMPRESSION = "compression"
_ATTR_LINE_ORDER = "lineOrder"
_ATTR_STORAGE_TYPE = "type"
_ATTR_TILE_DESCRIPTION = "tiles"
_ATTR_CHUNK_COUNT = "chunkCount"
_ATTR_NAME = "name"
_ATTR_PIXEL_ASPECT_RATIO = "pixelAspectRatio"
_ATTR_SCREEN_WINDOW_CENTER = "screenWindowCenter"
_ATTR_SCREEN_WINDOW_WIDTH = "screenWindowWidth"
_ATTR_OWNER = "owner"
_ATTR_COMMENTS = "comments"
_ATTR_CAP_DATE = "capDate"
_ATTR_SOFTWARE = "software"
_ATTR_TIME_CODE = "timeCode"
_ATTR_CHROMATICITIES = "chromaticities"
_ATTR_CHANNELS = "channels"

# Attributes handled as dedicated fields - excluded from EXRHeader.custom
_HEADER_SKIP_ATTRS: set[str] = {
    _ATTR_DATA_WINDOW,
    _ATTR_DISPLAY_WINDOW,
    _ATTR_COMPRESSION,
    _ATTR_LINE_ORDER,
    _ATTR_STORAGE_TYPE,
    _ATTR_TILE_DESCRIPTION,
    _ATTR_CHUNK_COUNT,
    _ATTR_NAME,
    _ATTR_PIXEL_ASPECT_RATIO,
    _ATTR_SCREEN_WINDOW_CENTER,
    _ATTR_SCREEN_WINDOW_WIDTH,
    _ATTR_OWNER,
    _ATTR_COMMENTS,
    _ATTR_CAP_DATE,
    _ATTR_SOFTWARE,
    _ATTR_TIME_CODE,
    _ATTR_CHROMATICITIES,
    _ATTR_CHANNELS,
    # Some files write "Software" with capital S; exclude both casings
    "Software",
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
    level_mode: LevelModeType
    rounding_mode: LevelRoundingModeType


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


# --- Channel structure ---


@dataclass
class EXRChannelInfo:
    """Channel metadata (no pixel data).

    Attributes:
        name: Full channel name as stored in the EXR (e.g. "beauty.R")
        pixel_type: Data type for this channel
        x_sampling: Horizontal subsampling factor (1 = full resolution)
        y_sampling: Vertical subsampling factor (1 = full resolution)
    """

    name: str
    pixel_type: PixelType
    x_sampling: int
    y_sampling: int


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
        name: Part name (empty for single-part or unnamed parts)
        channels: All channels in this part
        header: Full header metadata
        width: Image width in pixels
        height: Image height in pixels
    """

    name: str
    channels: list[EXRChannelInfo]
    header: EXRHeader
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


# --- Attribute value conversion ---


def _convert_attribute_value(value: Any) -> Any:
    """Normalise an OpenEXR attribute value to a plain Python type."""
    if isinstance(value, float):
        if math.isnan(value):
            return "NaN"
        if math.isinf(value):
            return "Infinity" if value > 0 else "-Infinity"
        return value
    if isinstance(value, (str, int, bool)):
        return value
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    if isinstance(value, (tuple, list)):
        return [_convert_attribute_value(v) for v in value]
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
