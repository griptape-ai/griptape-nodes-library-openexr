"""OpenEXR-based EXR file scanning.

Two-phase design:
- scan_exr_header(): reads metadata for all parts; pixel loading is controlled by the
  `header_only` flag (configurable via the `openexr.header_only` engine setting).
- When header_only=True (default): headers are read without touching pixel data — fast
  even on large multi-part files, but every channel's pixel type is reported as HALF,
  since the OpenEXR binding only exposes pixel types through the decoded pixel arrays.
- When header_only=False: the full file is opened, pixel types are read accurately from
  the file, at the cost of loading pixel data into memory.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

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
    LineOrderType,
    PixelType,
    StorageType,
    TileDescription,
    WindowCoordinates,
    _convert_attribute_value,
    _format_time_code,
)

if TYPE_CHECKING:
    from collections.abc import Mapping

    from griptape_nodes_openexr.exr.exr_header_artifact import EXRChannelArtifact

logger = logging.getLogger("griptape_nodes")


def build_rgb_array(pixels: dict[str, np.ndarray], selected: list[str]) -> np.ndarray:
    """Stack selected channels into a (H, W, 3) float32 array.

    Single-channel selections are broadcast to grey RGB; multi-channel selections
    are stacked in order. Shape is inferred from the first channel in pixels.
    """
    first_plane = next(iter(pixels.values()))
    h, w = first_plane.shape
    if len(selected) == 1:
        ch = pixels[selected[0]].reshape(h, w)
        return np.stack([ch, ch, ch], axis=-1)
    return np.stack([pixels[name].reshape(h, w) for name in selected], axis=-1)


def scan_exr_header(file_path: str, *, header_only: bool = True) -> EXRData:
    """Scan an EXR file and return metadata for all parts.

    Opens the file, iterates all parts, and builds the full EXRData structure
    (headers + channel metadata).

    Args:
        file_path: Path to the EXR file
        header_only: When True (default), only header data is read — pixel chunks are
            never loaded, making this fast even for large multi-part files.  Every
            channel's pixel type is reported as HALF in this mode.  When False, the
            full file is opened so pixel types are read accurately from the file at
            the cost of loading pixel data.

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
        # separate_channels keeps part.channels keyed by the file's own channel names,
        # rather than grouping R/G/B/A into one interleaved "RGBA" entry.
        with OpenEXR.File(resolved_path, separate_channels=True, header_only=header_only) as exr_file:
            for part in exr_file.parts:
                # In header_only mode part.width()/height() return 0 and
                # part.channels is empty; read both from the header.
                raw_header = part.header
                channels = _build_channel_list_from_header(
                    raw_header.get(_ATTR_CHANNELS, []),
                    None if header_only else part.channels,
                )
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
        channel_names: Channels to return. ``None`` returns all channels in the part.
            Every call decodes the whole file, so this limits what is returned, not
            what is read. Use :func:`load_slot_channels` to read several channels of
            one file in a single decode.

    Returns:
        Mapping of channel name to float32 array of shape ``(height, width)``.

    Raises:
        ValueError: For empty ``file_path``, out-of-range ``part_index``, or
            unknown ``channel_names``.
        RuntimeError: If the file cannot be opened or read.
    """
    return _load_parts_channels(file_path, {part_index: channel_names})[part_index]


def load_slot_channels(slots: Mapping[str, EXRChannelArtifact]) -> dict[str, np.ndarray]:
    """Read the pixel array for each slot's channel, decoding each source file once.

    Slots may come from different files or parts; slots that share a file are read
    with one decode, whichever parts they come from.

    Args:
        slots: Mapping of slot name (e.g. ``"R"``) to the channel connected to it.

    Returns:
        Mapping of slot name to float32 array of shape ``(height, width)``, in ``slots`` order.

    Raises:
        ValueError: As :func:`load_exr_channels`, with the message naming the affected slots.
        RuntimeError: As :func:`load_exr_channels`, with the message naming the affected slots.
    """
    groups: dict[str, list[str]] = {}
    for slot, artifact in slots.items():
        groups.setdefault(artifact.file_path, []).append(slot)

    pixels: dict[str, np.ndarray] = {}
    for file_path, group in groups.items():
        requests: dict[int, list[str]] = {}
        for slot in group:
            names = requests.setdefault(slots[slot].part_index, [])
            if slots[slot].channel.name not in names:
                names.append(slots[slot].channel.name)
        try:
            loaded = _load_parts_channels(file_path, requests)
        except (ValueError, RuntimeError) as e:
            label = f"{group[0]} channel" if len(group) == 1 else f"{', '.join(group)} channels"
            msg = f"Failed to load {label}: {e}"
            raise type(e)(msg) from e
        for slot in group:
            pixels[slot] = loaded[slots[slot].part_index][slots[slot].channel.name]

    return {slot: pixels[slot] for slot in slots}


def _load_parts_channels(
    file_path: str,
    requests: Mapping[int, list[str] | None],
) -> dict[int, dict[str, np.ndarray]]:
    """Read channels from several parts of one EXR file with a single decode.

    ``requests`` maps part index to the channel names to return, ``None`` meaning
    all channels in that part. Returns the same keys, each mapped to channel name
    and float32 array. Raises as :func:`load_exr_channels`.
    """
    if not file_path:
        msg = "file_path must not be empty"
        raise ValueError(msg)

    resolved_path = File(str(file_path)).resolve()
    try:
        # TODO: revisit direct file I/O once artifact manager is pluggable https://github.com/griptape-ai/griptape-nodes-library-openexr/issues/9
        # The binding decodes every channel of every part here; it has no partial-read API.
        exr = OpenEXR.File(resolved_path, separate_channels=True)
    except Exception as e:
        msg = f"Failed to read EXR file '{resolved_path}': {e}"
        raise RuntimeError(msg) from e

    with exr as exr_file:
        # Validate the parts and channels before doing any pixel conversion.
        parts = exr_file.parts
        for part_index, channel_names in requests.items():
            if part_index < 0 or part_index >= len(parts):
                msg = f"part_index {part_index} is out of range (file has {len(parts)} part(s))"
                raise ValueError(msg)
            if channel_names is not None:
                unknown = set(channel_names) - parts[part_index].channels.keys()
                if unknown:
                    msg = f"Unknown channel(s) {sorted(unknown)!r} in part {part_index}"
                    raise ValueError(msg)

        # Pixels are already decoded; selecting channel names only skips the float32 conversion.
        result: dict[int, dict[str, np.ndarray]] = {}
        try:
            for part_index, channel_names in requests.items():
                part = parts[part_index]
                names_to_load = channel_names if channel_names is not None else list(part.channels)
                result[part_index] = {
                    name: part.channels[name].pixels.astype(np.float32, copy=False) for name in names_to_load
                }
        except Exception as e:
            msg = f"Failed to read EXR file '{resolved_path}': {e}"
            raise RuntimeError(msg) from e
        return result


def write_exr_channels(
    output_path: str,
    channels: dict[str, np.ndarray],
    compression: OpenEXR.Compression | None = None,
    pixel_type: str = "half",
    extra_header: dict[str, Any] | None = None,
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
        extra_header: Optional additional header attributes (owner, comments, etc.)
            merged into the header dict before writing.  ``compression`` and ``type``
            are always set from the arguments above and cannot be overridden here.

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
    converted = {name: np.ascontiguousarray(arr.astype(dtype, copy=False)) for name, arr in channels.items()}

    # extra_header goes in first so the writer's own compression/type always win: a
    # caller-supplied "compression" would otherwise silently override the argument,
    # and a "type" of tiledimage produces a file that cannot be reopened.
    header: dict[str, Any] = dict(extra_header or {})
    header["compression"] = compression
    header["type"] = OpenEXR.scanlineimage

    try:
        # OpenEXR binding requires a real filesystem path — no in-memory API. Callers
        # pass a temp path here; the final destination write goes through WriteFileRequest.
        # TODO: revisit once artifact manager is pluggable https://github.com/griptape-ai/griptape-nodes-library-openexr/issues/9
        OpenEXR.File(header, converted).write(output_path)
    except Exception as e:
        msg = f"Failed to write EXR file '{output_path}': {e}"
        raise RuntimeError(msg) from e


def _build_channel_list_from_header(
    exr_channels: list[OpenEXR.Channel],
    loaded_channels: dict[str, OpenEXR.Channel] | None,
) -> list[EXRChannelInfo]:
    """Build channel metadata list from the header 'channels' attribute.

    Names and sampling come from part.header['channels'], which is populated in
    both modes. Its Channel objects carry no pixel array, so .type() cannot be
    called on them (the binding derives the type from the pixels' dtype).

    Pixel types come from loaded_channels, the part's decoded channels, when the
    pixels were loaded. When they were not (header_only mode), every channel
    reports HALF. A deep channel with no samples reports HALF too: the binding
    has no sample data to derive its type from and returns NUM_PIXELTYPES.
    """
    result: list[EXRChannelInfo] = []
    for exr_channel in exr_channels:
        if loaded_channels is None:
            pixel_type = PixelType.HALF
        else:
            exr_pixel_type = loaded_channels[exr_channel.name].type()
            if exr_pixel_type == OpenEXR.PixelType.NUM_PIXELTYPES:
                pixel_type = PixelType.HALF
            else:
                pixel_type = _EXR_PIXEL_TYPE_MAP[exr_pixel_type]
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
    compression: CompressionType = _map_exr_enum(_EXR_COMPRESSION_MAP, exr_compression, _ATTR_COMPRESSION)

    # Line Order
    exr_line_order = _require_exr_attribute(header, _ATTR_LINE_ORDER)
    line_order: LineOrderType = _map_exr_enum(_EXR_LINE_ORDER_MAP, exr_line_order, _ATTR_LINE_ORDER)

    # Storage Type (scanline/tiled)
    exr_storage_type = _require_exr_attribute(header, _ATTR_STORAGE_TYPE)
    storage_type = _map_exr_enum(_EXR_STORAGE_TYPE_MAP, exr_storage_type, _ATTR_STORAGE_TYPE)
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
        level_mode=_map_exr_enum(_EXR_LEVEL_MODE_MAP, exr_tile_description.mode, "tile level mode"),
        rounding_mode=_map_exr_enum(
            _EXR_LEVEL_ROUNDING_MODE_MAP, exr_tile_description.roundingMode, "tile rounding mode"
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
    if name not in header:
        msg = f"Required EXR header attribute '{name}' is missing"
        raise ValueError(msg)
    return header[name]


def _map_exr_enum[K, V](mapping: dict[K, V], exr_value: K, label: str) -> V:
    """Map an OpenEXR binding enum value to its StrEnum, raising on a value with no mapping.

    A miss means the binding knows a value this library does not. Raising keeps that
    from being reported as a confident, wrong value such as "uncompressed".
    """
    if exr_value in mapping:
        return mapping[exr_value]
    msg = f"Unsupported EXR {label}: '{exr_value}'"
    raise ValueError(msg)


def _optional_exr_attribute(header: dict[str, Any], name: str, default: Any) -> Any:
    """Get an optional attribute from an OpenEXR Part Header, returning default if absent."""
    return header.get(name, default)
