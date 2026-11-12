"""Frame a rendered EXR image by its data window or display window.

An EXR part stores pixels for its data window, which can extend past the
display window (overscan) or cover only part of it. The nodes that render a
part decode the data window, select its intersection with the display window
before colour processing, then hand the display-encoded result to
`apply_window_mode`, which places it in the display window or outlines the
display window on top of it. Only the rendered image changes: the float
channels and header metadata the nodes pass on keep the data window.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

import numpy as np
from griptape_nodes.exe_types.core_types import ParameterMode
from griptape_nodes.exe_types.param_types.parameter_string import ParameterString
from griptape_nodes.traits.options import Options

from griptape_nodes_openexr.exr.exr_io import scan_exr_header
from griptape_nodes_openexr.exr.exr_types import WindowCoordinates

if TYPE_CHECKING:
    from griptape_nodes.exe_types.node_types import BaseNode

    from griptape_nodes_openexr.exr.exr_types import EXRHeader

WINDOW_MODE_DATA = "data window"
WINDOW_MODE_DISPLAY = "display window"
WINDOW_MODE_OVERLAY = "data window with display overlay"
WINDOW_MODE_CHOICES = [WINDOW_MODE_DATA, WINDOW_MODE_DISPLAY, WINDOW_MODE_OVERLAY]
WINDOW_MODE_DEFAULT = WINDOW_MODE_DATA

WINDOW_MODE_TOOLTIP = (
    "Which region of the EXR to render. 'data window' shows every stored pixel. "
    "'display window' crops to the intended frame, removing overscan and filling frame areas "
    "with no stored pixels with transparent black. 'data window with display overlay' shows every "
    "stored pixel and outlines the intended frame. Only the rendered image changes; the EXR's "
    "channels and metadata are passed on as stored."
)

# The outline alternates these two colours so it reads against both light and dark pixels.
_OUTLINE_LIGHT = 255
_OUTLINE_DARK = 0
# Outline thickness is one pixel per this many pixels of the image's longer side, at least one.
_PIXELS_PER_OUTLINE_PIXEL = 512
_DASH_LENGTH_IN_THICKNESSES = 8
_RGBA_CHANNELS = 4
_OPAQUE = 255


@dataclass(frozen=True)
class WindowFraming:
    """How to frame one rendered part: the mode and the part's two windows."""

    mode: str
    data_window: WindowCoordinates
    display_window: WindowCoordinates

    @classmethod
    def for_header(cls, mode: str, header: EXRHeader) -> WindowFraming:
        return cls(mode=mode, data_window=header.data_window, display_window=header.display_window)


def read_framing(window_mode: str, file_path: str, part_index: int) -> WindowFraming:
    """Read a part's windows from its file's header, for callers that hold no header.

    Raises:
        ValueError: If the file has no part at part_index.
        RuntimeError: If the header cannot be read.
    """
    parts = scan_exr_header(file_path, header_only=True).parts
    if part_index >= len(parts):
        msg = f"part_index {part_index} is out of range (file has {len(parts)} part(s))"
        raise ValueError(msg)
    return WindowFraming.for_header(window_mode, parts[part_index].header)


def add_window_mode_parameter(node: BaseNode) -> ParameterString:
    """Create and register the shared `window_mode` parameter on a node."""
    parameter = ParameterString(
        name="window_mode",
        default_value=WINDOW_MODE_DEFAULT,
        tooltip=WINDOW_MODE_TOOLTIP,
        allowed_modes={ParameterMode.INPUT, ParameterMode.PROPERTY},
    )
    # allow_custom for the same reason as tone_mapping: the converter would rewrite an
    # off-list value to choices[0] silently, where apply_window_mode reports it instead.
    parameter.add_trait(Options(choices=WINDOW_MODE_CHOICES, allow_custom=True))
    node.add_parameter(parameter)
    return parameter


def window_size(framing: WindowFraming) -> tuple[int, int]:
    """The `(width, height)` of the image `apply_window_mode` produces for this framing."""
    window = framing.display_window if framing.mode == WINDOW_MODE_DISPLAY else framing.data_window
    return _width(window), _height(window)


@dataclass(frozen=True)
class WindowIntersection:
    """Source and destination slices for the overlapping EXR coordinates."""

    window: WindowCoordinates
    source: tuple[slice, slice]
    destination: tuple[slice, slice]

    @classmethod
    def for_framing(cls, framing: WindowFraming) -> WindowIntersection | None:
        data, display = framing.data_window, framing.display_window
        xmin, xmax = max(data.xmin, display.xmin), min(data.xmax, display.xmax)
        ymin, ymax = max(data.ymin, display.ymin), min(data.ymax, display.ymax)
        if xmin > xmax or ymin > ymax:
            return None
        return cls(
            window=WindowCoordinates(xmin, ymin, xmax, ymax),
            source=(slice(ymin - data.ymin, ymax - data.ymin + 1), slice(xmin - data.xmin, xmax - data.xmin + 1)),
            destination=(
                slice(ymin - display.ymin, ymax - display.ymin + 1),
                slice(xmin - display.xmin, xmax - display.xmin + 1),
            ),
        )


def select_window_channels(
    pixels: dict[str, np.ndarray], framing: WindowFraming
) -> tuple[dict[str, np.ndarray], WindowFraming]:
    """Select retained channel planes before RGB assembly and colour processing.

    The loader still decodes and converts the full planes. Returned views keep
    those arrays alive, while subsequent RGB allocations cover only the overlap.
    The returned framing describes the selected region for final placement.
    Disjoint windows retain full processing to preserve colour validation errors
    and avoid passing empty arrays to colour processors.
    """
    for plane in pixels.values():
        _validate_window_shape(plane, framing)
    if framing.mode != WINDOW_MODE_DISPLAY:
        return pixels, framing
    intersection = WindowIntersection.for_framing(framing)
    if intersection is None:
        return pixels, framing
    selected = {name: plane[intersection.source] for name, plane in pixels.items()}
    return selected, WindowFraming(framing.mode, intersection.window, framing.display_window)


def _validate_window_shape(image: np.ndarray, framing: WindowFraming) -> None:
    if framing.mode not in WINDOW_MODE_CHOICES:
        msg = f"Unknown window_mode '{framing.mode}'. Choose one of: {', '.join(WINDOW_MODE_CHOICES)}"
        raise ValueError(msg)
    if framing.mode == WINDOW_MODE_DATA:
        return
    expected = (_height(framing.data_window), _width(framing.data_window))
    if image.shape[:2] != expected:
        msg = (
            f"window_mode '{framing.mode}' needs an image the size of the data window "
            f"({expected[1]}x{expected[0]}), got {image.shape[1]}x{image.shape[0]}"
        )
        raise ValueError(msg)


def apply_window_mode(image: np.ndarray, framing: WindowFraming) -> np.ndarray:
    """Frame a display-encoded `(H, W, C)` image of the data window.

    Args:
        image: Rendered pixels covering exactly the data window, RGB or RGBA.
        framing: The mode and the part's windows.

    Returns:
        The image unchanged for 'data window'; cropped and padded to the display
        window for 'display window'; a copy with the display window outlined for
        the overlay mode.

    Raises:
        ValueError: If the mode is unknown, or the image does not match the data
            window (subsampled channels, for instance).
    """
    _validate_window_shape(image, framing)
    if framing.mode == WINDOW_MODE_DATA:
        return image

    data = framing.data_window
    if framing.mode == WINDOW_MODE_DISPLAY:
        return _crop_to_display_window(image, data, framing.display_window)
    return _outline_display_window(image, data, framing.display_window)


def _crop_to_display_window(image: np.ndarray, data: WindowCoordinates, display: WindowCoordinates) -> np.ndarray:
    if data == display:
        return image
    # Zeros are black, and transparent when the image has alpha.
    out = np.zeros((_height(display), _width(display), image.shape[2]), dtype=image.dtype)
    intersection = WindowIntersection.for_framing(WindowFraming(WINDOW_MODE_DISPLAY, data, display))
    if intersection is not None:
        out[intersection.destination] = image[intersection.source]
    return out


def _outline_display_window(image: np.ndarray, data: WindowCoordinates, display: WindowCoordinates) -> np.ndarray:
    out = image.copy()
    height, width = out.shape[:2]
    thickness = max(1, max(height, width) // _PIXELS_PER_OUTLINE_PIXEL)
    dash = thickness * _DASH_LENGTH_IN_THICKNESSES

    # The display window in image coordinates, inclusive. Anything past the image is clipped.
    left, right = display.xmin - data.xmin, display.xmax - data.xmin
    top, bottom = display.ymin - data.ymin, display.ymax - data.ymin
    # Each band is drawn just inside the display window's edge.
    bands = [
        (top, top + thickness, left, right + 1),
        (bottom - thickness + 1, bottom + 1, left, right + 1),
        (top, bottom + 1, left, left + thickness),
        (top, bottom + 1, right - thickness + 1, right + 1),
    ]
    for y0, y1, x0, x1 in bands:
        y0, y1 = max(y0, 0), min(y1, height)
        x0, x1 = max(x0, 0), min(x1, width)
        if y0 >= y1 or x0 >= x1:
            continue
        ys, xs = np.ogrid[y0:y1, x0:x1]
        light = ((xs + ys) // dash) % 2 == 0
        value = np.where(light, _OUTLINE_LIGHT, _OUTLINE_DARK).astype(out.dtype)
        out[y0:y1, x0:x1, :3] = value[..., np.newaxis]
        if out.shape[2] == _RGBA_CHANNELS:
            out[y0:y1, x0:x1, 3] = _OPAQUE
    return out


def _width(window: WindowCoordinates) -> int:
    return int(window.xmax - window.xmin + 1)


def _height(window: WindowCoordinates) -> int:
    return int(window.ymax - window.ymin + 1)
