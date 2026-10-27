"""Decode an EXR part to display-ready PNG bytes.

Shared by the nodes that turn float EXR pixels into something the canvas can
show. The work is synchronous and CPU-bound (a 4K frame is seconds, not
milliseconds), so callers on the engine's event loop run it through
`griptape_nodes.utils.async_utils.to_thread`.
"""

from __future__ import annotations

import io

import numpy as np
from PIL import Image

from griptape_nodes_openexr.exr.exr_io import build_rgb_array, load_exr_channels
from griptape_nodes_openexr.exr.ocio_helpers import ColorParamsProtocol, apply_color_management
from griptape_nodes_openexr.exr.tone_mapping import apply_exposure, to_uint8

_RGB_CHANNELS = 3
_RGBA_CHANNELS = 4


def render_part_png(  # noqa: PLR0913
    *,
    file_path: str,
    part_index: int,
    width: int,
    height: int,
    selected: list[str],
    alpha_channel: str | None,
    ev: float = 0.0,
    color_params: ColorParamsProtocol | None = None,
    tone_mapping: str,
) -> tuple[bytes, str]:
    """Load the selected channels, tone map them, and encode the result as PNG.

    Args:
        file_path: Engine path to the EXR file.
        part_index: Zero-based part index within the file.
        width: Part width in pixels, used to reshape the alpha plane.
        height: Part height in pixels, used to reshape the alpha plane.
        selected: 1 or 3 channel names to map onto RGB, as returned by
            `select_display_channels`.
        alpha_channel: Channel to use as alpha, or None for RGB output.
        ev: Exposure in EV stops, applied before tone mapping.
        color_params: OCIO parameters, or None to use local tone mapping.
        tone_mapping: Local tone mapping mode, used when color_params is None.

    Returns:
        A `(png_bytes, tone_mode)` pair, where tone_mode names the transform
        that was applied, for the node's status details.

    Raises:
        RuntimeError: If the channels cannot be read or assembled.
        ValueError: If colour management fails (OCIO errors carry their own
            user-facing message).
    """
    channels_to_load = selected + ([alpha_channel] if alpha_channel else [])

    try:
        pixels = load_exr_channels(file_path, part_index, channels_to_load)
    except (ValueError, RuntimeError) as e:
        msg = f"Failed to load channels: {e}"
        raise RuntimeError(msg) from e

    try:
        rgb = build_rgb_array(pixels, selected)
    except Exception as e:
        msg = f"Failed to build RGB: {e}"
        raise RuntimeError(msg) from e

    rgb = apply_exposure(rgb, ev)
    rgb, tone_mode = apply_color_management(rgb, color_params, tone_mapping)
    uint8_rgb = to_uint8(rgb)

    if alpha_channel:
        alpha_plane = np.clip(pixels[alpha_channel].reshape(height, width), 0.0, 1.0)
        alpha_uint8 = (alpha_plane * 255.0 + 0.5).astype(np.uint8)[..., np.newaxis]
        uint8 = np.concatenate([uint8_rgb, alpha_uint8], axis=-1)
    else:
        uint8 = uint8_rgb

    return ndarray_to_png(uint8), tone_mode


def ndarray_to_png(uint8: np.ndarray) -> bytes:
    """Encode a (H, W, 3) or (H, W, 4) uint8 array as PNG bytes."""
    if uint8.ndim != 3 or uint8.shape[2] not in (_RGB_CHANNELS, _RGBA_CHANNELS):  # noqa: PLR2004
        msg = f"expected (H, W, 3|4) uint8 array, got shape {uint8.shape}"
        raise ValueError(msg)
    mode = "RGBA" if uint8.shape[2] == _RGBA_CHANNELS else "RGB"
    buf = io.BytesIO()
    Image.fromarray(uint8, mode=mode).save(buf, format="PNG")
    return buf.getvalue()
