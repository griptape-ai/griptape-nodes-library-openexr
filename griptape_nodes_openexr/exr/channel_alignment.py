"""Assemble channel planes with an explicit coordinate and frame policy."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

import numpy as np

from griptape_nodes_openexr.exr.display_window import (
    WINDOW_MODE_DISPLAY,
    WindowFraming,
    WindowIntersection,
    select_window_channels,
    validate_window_mode,
    window_size,
)
from griptape_nodes_openexr.exr.exr_io import ChannelSource
from griptape_nodes_openexr.exr.exr_types import WindowCoordinates
from griptape_nodes_openexr.exr.render_limits import check_canvas, validate_render_limit

ALIGN_PIXELS = "Match Pixel Positions"
ALIGN_COORDINATES = "Match EXR Coordinates"
ALIGN_STRICT = "Require Matching Windows"
ALIGN_CHOICES = [ALIGN_PIXELS, ALIGN_COORDINATES, ALIGN_STRICT]
REFERENCE_DEFAULT = "First Connected RGB"
REFERENCE_CHOICES = [REFERENCE_DEFAULT, "R", "G", "B", "A"]


@dataclass(frozen=True)
class ChannelAssembly:
    """Aligned planes and RGB coverage, ready for colour processing."""

    planes: dict[str, np.ndarray]
    framing: WindowFraming
    rgb_coverage: np.ndarray
    reference_slot: str


def resolve_reference(slots: Mapping[str, object], policy: str, reference: str, mode: str) -> str:
    """Validate choices and resolve the intended frame's source before decoding."""
    if policy not in ALIGN_CHOICES:
        msg = f"Unknown channel_alignment '{policy}'. Choose one of: {', '.join(ALIGN_CHOICES)}"
        raise ValueError(msg)
    if reference not in REFERENCE_CHOICES:
        msg = f"Unknown frame_reference '{reference}'. Choose one of: {', '.join(REFERENCE_CHOICES)}"
        raise ValueError(msg)
    validate_window_mode(mode)
    if not any(slot in slots for slot in "RGB"):
        msg = "At least one RGB channel slot must be connected"
        raise ValueError(msg)
    if reference == REFERENCE_DEFAULT:
        return next(slot for slot in "RGB" if slot in slots)
    if reference not in slots:
        msg = f"Frame reference {reference} is not connected. Connect that slot or choose {REFERENCE_DEFAULT}."
        raise ValueError(msg)
    return reference


def assemble_channels(
    sources: Mapping[str, ChannelSource], policy: str, reference: str, mode: str, max_pixels: int
) -> ChannelAssembly:
    """Align source planes without resampling and retain coverage for black padding.

    Coordinate-mode cropping assembles directly into the reference display
    window. Full and overlay modes use the union of all connected data windows.
    Pixel-position mode retains the existing equal-shape, reference-origin path.
    """
    mode = validate_window_mode(mode)
    reference_slot = resolve_reference(sources, policy, reference, mode)
    limit = validate_render_limit(max_pixels)
    source = sources[reference_slot]
    if policy == ALIGN_PIXELS:
        shapes = {slot: item.pixels.shape for slot, item in sources.items()}
        if len(set(shapes.values())) > 1:
            msg = f"Channel dimensions do not match: {shapes}"
            raise ValueError(msg)
    else:
        for slot, item in sources.items():
            if item.x_sampling != 1 or item.y_sampling != 1:
                msg = (
                    f"{slot} channel is subsampled ({item.x_sampling}x{item.y_sampling}); "
                    f"{policy} requires one sample per EXR coordinate."
                )
                raise ValueError(msg)
            expected = (item.data_window.height, item.data_window.width)
            if item.pixels.shape != expected:
                msg = f"{slot} channel needs pixels the size of its data window {expected}, got {item.pixels.shape}"
                raise ValueError(msg)
            if policy == ALIGN_STRICT and (
                item.data_window != source.data_window or item.display_window != source.display_window
            ):
                msg = (
                    f"{slot} channel windows do not match reference {reference_slot}. "
                    f"Data window {tuple(int(value) for value in item.data_window)}, "
                    f"display window {tuple(int(value) for value in item.display_window)}; "
                    f"reference data window {tuple(int(value) for value in source.data_window)}, "
                    f"display window {tuple(int(value) for value in source.display_window)}."
                )
                raise ValueError(msg)
    if policy != ALIGN_COORDINATES:
        return _assemble_pixel_positions(sources, source, reference_slot, mode, limit)
    windows = [item.data_window for item in sources.values()]
    canvas = WindowCoordinates(
        min(window.xmin for window in windows),
        min(window.ymin for window in windows),
        max(window.xmax for window in windows),
        max(window.ymax for window in windows),
    )
    if mode == WINDOW_MODE_DISPLAY:
        canvas = source.display_window
    check_canvas(canvas, limit)
    planes: dict[str, np.ndarray] = {}
    coverage = np.zeros((canvas.height, canvas.width), dtype=bool)
    for slot, item in sources.items():
        plane = np.zeros((canvas.height, canvas.width), dtype=np.float32)
        intersection = WindowIntersection.for_framing(WindowFraming(WINDOW_MODE_DISPLAY, item.data_window, canvas))
        if intersection is not None:
            plane[intersection.destination] = item.pixels[intersection.source]
            if slot in "RGB":
                coverage[intersection.destination] = True
        planes[slot] = plane
    return ChannelAssembly(planes, WindowFraming(mode, canvas, source.display_window), coverage, reference_slot)


def _assemble_pixel_positions(
    sources: Mapping[str, ChannelSource], source: ChannelSource, reference_slot: str, mode: str, limit: int
) -> ChannelAssembly:
    framing = WindowFraming(mode, source.data_window, source.display_window)
    output_width, output_height = window_size(framing)
    check_canvas(WindowCoordinates(0, 0, output_width - 1, output_height - 1), limit)
    planes, framing = select_window_channels({slot: item.pixels for slot, item in sources.items()}, framing)
    height, width = next(iter(planes.values())).shape
    check_canvas(WindowCoordinates(0, 0, width - 1, height - 1), limit)
    coverage = np.ones((height, width), dtype=bool)
    return ChannelAssembly(planes, framing, coverage, reference_slot)
