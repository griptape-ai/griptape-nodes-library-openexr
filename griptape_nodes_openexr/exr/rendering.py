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
from griptape_nodes_openexr.exr.icc_profiles import profile_for_tone_mapping
from griptape_nodes_openexr.exr.ocio_helpers import ColorParamsProtocol, apply_color_management
from griptape_nodes_openexr.exr.tone_mapping import apply_exposure, to_uint8

_RGB_CHANNELS = 3
_RGBA_CHANNELS = 4


def render_part_png(  # noqa: PLR0913
    *,
    file_path: str,
    part_index: int,
    selected: list[str],
    alpha_channel: str | None,
    ev: float = 0.0,
    color_params: ColorParamsProtocol | None = None,
    tone_mapping: str,
    embed_color_profile: bool = False,
) -> tuple[bytes, str]:
    """Load the selected channels, tone map them, and encode the result as PNG.

    Args:
        file_path: Engine path to the EXR file.
        part_index: Zero-based part index within the file.
        selected: 1 or 3 channel names to map onto RGB, as returned by
            `select_display_channels`.
        alpha_channel: Channel to use as alpha, or None for RGB output.
        ev: Exposure in EV stops, applied before tone mapping.
        color_params: OCIO parameters, or None to use local tone mapping.
        tone_mapping: Local display transform, used when color_params is None.
        embed_color_profile: Embed an ICC profile describing that transform.
            Ignored under OCIO, whose output is already display-referred.

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
        alpha_uint8 = to_uint8(pixels[alpha_channel].reshape(uint8_rgb.shape[:2]))[..., np.newaxis]
        uint8 = np.concatenate([uint8_rgb, alpha_uint8], axis=-1)
    else:
        uint8 = uint8_rgb

    # OCIO output carries no profile: the display/view transform resolves to
    # whatever the config's display colorspace is, which we cannot describe.
    profile = profile_for_tone_mapping(tone_mapping) if embed_color_profile and color_params is None else None
    return ndarray_to_png(uint8, icc_profile=profile), tone_mode


def ndarray_to_png(uint8: np.ndarray, icc_profile: bytes | None = None) -> bytes:
    """Encode a (H, W, 3) or (H, W, 4) uint8 array as PNG bytes.

    Args:
        uint8: Display-encoded pixels, RGB or RGBA.
        icc_profile: Profile describing the encoding, embedded as an iCCP
            chunk. None writes an untagged PNG, which viewers read as sRGB.

    Returns:
        The encoded PNG.

    Raises:
        ValueError: If the array is not (H, W, 3) or (H, W, 4).
    """
    if uint8.ndim != 3 or uint8.shape[2] not in (_RGB_CHANNELS, _RGBA_CHANNELS):  # noqa: PLR2004
        msg = f"expected (H, W, 3|4) uint8 array, got shape {uint8.shape}"
        raise ValueError(msg)
    mode = "RGBA" if uint8.shape[2] == _RGBA_CHANNELS else "RGB"
    buf = io.BytesIO()
    # Only pass icc_profile when set: Pillow writes an empty iCCP chunk for an
    # explicit None on some formats.
    save_kwargs = {"icc_profile": icc_profile} if icc_profile is not None else {}
    Image.fromarray(uint8, mode=mode).save(buf, format="PNG", **save_kwargs)
    return buf.getvalue()
