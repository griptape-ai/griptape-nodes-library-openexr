"""OIIO-based EXR pixel loading, colorspace detection, and tone mapping.

No opencolorio dependency. All colour math is self-contained.

Colorspace detection is best-effort: reads oiio:ColorSpace first, then
falls back to a chromaticity-derived label. Callers should treat the result
as advisory — a hook for future OCIO integration rather than a guarantee.

Tone mapping converts scene-linear float32 data to a displayable [0, 1]
range. Three operators are provided in ascending quality order:
  simple   — per-channel x/(1+x), fastest, hue-shifting under saturation
  reinhard — per-channel Reinhard, slightly better highlight roll-off
  filmic   — Hable/Uncharted2 approximation, best hue preservation

All pixel arrays are float32 (H, W, C) or (H, W) for single-channel.
"""

from __future__ import annotations

import logging
from typing import Any

import numpy as np
from PIL import Image

logger = logging.getLogger("griptape_nodes")

# ---------------------------------------------------------------------------
# Chromaticity → colorspace label
# ---------------------------------------------------------------------------

# Known primary sets as (rx, ry, gx, gy, bx, by) rounded to 3 dp.
# White point is not included — D65 is universal for all entries below.
_KNOWN_PRIMARIES: list[tuple[tuple[float, ...], str]] = [
    ((0.64, 0.33, 0.30, 0.60, 0.15, 0.06), "lin_srgb"),   # Rec.709 / sRGB
    ((0.68, 0.32, 0.265, 0.69, 0.15, 0.06), "ACEScg"),     # AP1
    ((0.7347, 0.2653, 0.0, 1.0, 0.0001, -0.077), "ACES2065-1"),  # AP0
    ((0.708, 0.292, 0.170, 0.797, 0.131, 0.046), "lin_rec2020"),
    ((0.63, 0.34, 0.31, 0.595, 0.155, 0.07), "lin_dcip3"),
]
_PRIMARY_TOLERANCE = 0.01


def _chromaticities_to_colorspace(chromaticities: Any) -> str | None:
    """Map EXR chromaticities to a known colorspace label, or return None."""
    if chromaticities is None:
        return None
    try:
        key = (
            round(chromaticities.red_x, 3),
            round(chromaticities.red_y, 3),
            round(chromaticities.green_x, 3),
            round(chromaticities.green_y, 3),
            round(chromaticities.blue_x, 3),
            round(chromaticities.blue_y, 3),
        )
    except AttributeError:
        return None

    for reference, label in _KNOWN_PRIMARIES:
        if all(abs(a - b) <= _PRIMARY_TOLERANCE for a, b in zip(key, reference, strict=True)):
            return label
    return None


# ---------------------------------------------------------------------------
# Colorspace detection
# ---------------------------------------------------------------------------


def detect_colorspace(file_path: str, part_index: int = 0, chromaticities: Any = None) -> str:
    """Detect the source colorspace of an EXR part.

    Reads oiio:ColorSpace first (most authoritative), then falls back to a
    chromaticity-derived label, then returns "unknown".

    Args:
        file_path: Path to the EXR file.
        part_index: Zero-based part index.
        chromaticities: Optional Chromaticities object from the parsed header.
            If provided, used as fallback when oiio:ColorSpace is absent.

    Returns:
        Colorspace label string, e.g. "lin_srgb", "ACEScg", or "unknown".
    """
    import OpenImageIO as oiio  # type: ignore[import-not-found]

    inp = oiio.ImageInput.open(file_path)
    if not inp:
        return "unknown"
    try:
        if not inp.seek_subimage(part_index, 0):
            return "unknown"
        spec = inp.spec()
        oiio_cs = spec.get_string_attribute("oiio:ColorSpace", "")
        if oiio_cs:
            return oiio_cs
    finally:
        inp.close()

    chroma_label = _chromaticities_to_colorspace(chromaticities)
    if chroma_label:
        return chroma_label

    return "unknown"


# ---------------------------------------------------------------------------
# Pixel loading
# ---------------------------------------------------------------------------


def load_layer_pixels(
    file_path: str,
    part_index: int,
    channel_indices: list[int],
) -> np.ndarray:
    """Load specific channels from an EXR part into a float32 numpy array.

    Only the requested channels are read — no full-image decode. Large EXRs
    with many channels are handled efficiently.

    Args:
        file_path: Absolute path to the EXR file.
        part_index: Zero-based part (subimage) index.
        channel_indices: Ordered list of channel indices (from EXRChannelInfo.channel_index).

    Returns:
        float32 array of shape (H, W, C) where C = len(channel_indices).

    Raises:
        RuntimeError: If the file cannot be opened or the subimage not found.
        ValueError: If channel_indices is empty.
    """
    import OpenImageIO as oiio  # type: ignore[import-not-found]

    if not channel_indices:
        msg = "channel_indices must not be empty"
        raise ValueError(msg)

    buf = oiio.ImageBuf(file_path, part_index, 0)
    if buf.has_error:
        msg = f"Failed to open '{file_path}' subimage {part_index}: {buf.geterror()}"
        raise RuntimeError(msg)

    spec = buf.spec()
    nchannels_total = spec.nchannels

    # Validate indices
    invalid = [i for i in channel_indices if i < 0 or i >= nchannels_total]
    if invalid:
        msg = f"Channel indices {invalid} out of range (file has {nchannels_total} channels)"
        raise ValueError(msg)

    chbegin = min(channel_indices)
    chend = max(channel_indices) + 1
    sub = oiio.ImageBufAlgo.channels(buf, tuple(range(chbegin, chend)))
    if sub.has_error:
        msg = f"Failed to extract channels {chbegin}:{chend}: {sub.geterror()}"
        raise RuntimeError(msg)

    pixels = sub.get_pixels(oiio.FLOAT)  # (H, W, chend-chbegin)
    if pixels is None:
        msg = f"get_pixels returned None for '{file_path}'"
        raise RuntimeError(msg)

    pixels = np.asarray(pixels, dtype=np.float32)

    # Remap to only the requested indices within the sub-buffer
    local_indices = [i - chbegin for i in channel_indices]
    return pixels[:, :, local_indices]


# ---------------------------------------------------------------------------
# Tone mapping
# ---------------------------------------------------------------------------

_HABLE_A = 0.15
_HABLE_B = 0.50
_HABLE_C = 0.10
_HABLE_D = 0.20
_HABLE_E = 0.02
_HABLE_F = 0.30
_HABLE_W = 11.2


def _hable(x: np.ndarray) -> np.ndarray:
    return (x * (_HABLE_A * x + _HABLE_C * _HABLE_B) + _HABLE_D * _HABLE_E) / (
        x * (_HABLE_A * x + _HABLE_B) + _HABLE_D * _HABLE_F
    ) - _HABLE_E / _HABLE_F


def tone_map(pixels: np.ndarray, method: str = "simple") -> np.ndarray:
    """Apply tone mapping to compress scene-linear HDR into [0, 1].

    Args:
        pixels: float32 array, any shape. Negative values are clipped to 0 first.
        method: "simple" | "reinhard" | "filmic"

    Returns:
        float32 array of same shape, values in [0, 1].
    """
    x = np.maximum(pixels, 0.0).astype(np.float32)

    match method:
        case "reinhard":
            return x / (1.0 + x)
        case "filmic":
            white = _hable(np.full(1, _HABLE_W, dtype=np.float32))
            return np.clip(_hable(x * 2.0) / white, 0.0, 1.0)
        case _:
            # "simple" and fallback
            return x / (1.0 + x)


def apply_exposure(pixels: np.ndarray, stops: float) -> np.ndarray:
    """Scale pixel values by 2**stops (exposure adjustment in stops).

    Args:
        pixels: float32 array, any shape.
        stops: Positive values brighten, negative darken.

    Returns:
        float32 array of same shape.
    """
    if stops == 0.0:
        return pixels
    return pixels * np.float32(2.0 ** stops)


def apply_gamma(pixels: np.ndarray, gamma: float = 2.2) -> np.ndarray:
    """Apply gamma encoding: out = in ** (1/gamma).

    Clips negative values to 0 before power to avoid NaN.

    Args:
        pixels: float32 array in [0, 1], any shape.
        gamma: Gamma value, must be > 0.

    Returns:
        float32 array of same shape.
    """
    if gamma == 1.0:
        return pixels
    return np.power(np.maximum(pixels, 0.0), np.float32(1.0 / gamma))


# ---------------------------------------------------------------------------
# PIL conversion
# ---------------------------------------------------------------------------

_SRGB_BREAKPOINT = 0.0031308
_SRGB_SCALE = 12.92
_SRGB_A = 0.055
_SRGB_GAMMA = 2.4


def _apply_srgb_transfer(linear: np.ndarray) -> np.ndarray:
    """Apply the sRGB piecewise transfer function to linear [0,1] data."""
    clipped = np.clip(linear, 0.0, 1.0)
    lo = clipped * _SRGB_SCALE
    hi = (1.0 + _SRGB_A) * np.power(np.maximum(clipped, _SRGB_BREAKPOINT), 1.0 / _SRGB_GAMMA) - _SRGB_A
    return np.where(clipped <= _SRGB_BREAKPOINT, lo, hi).astype(np.float32)


def to_pil_rgb(
    pixels_hwc: np.ndarray,
    max_width: int = 1024,
    max_height: int = 1024,
    apply_srgb: bool = True,
) -> Image.Image:
    """Convert a float32 (H, W, C) array to a PIL RGB image.

    Takes the first 3 channels if C > 3, single channel is broadcast to RGB.

    Args:
        pixels_hwc: float32 array (H, W, C), values expected in [0, 1].
        max_width: Maximum output width in pixels.
        max_height: Maximum output height in pixels.
        apply_srgb: Apply sRGB transfer function before quantising to uint8.

    Returns:
        PIL Image in mode "RGB".
    """
    c = pixels_hwc.shape[2] if pixels_hwc.ndim == 3 else 1  # noqa: PLR2004

    if pixels_hwc.ndim == 2 or c == 1:  # noqa: PLR2004
        gray = pixels_hwc[:, :, 0] if pixels_hwc.ndim == 3 else pixels_hwc
        rgb = np.stack([gray, gray, gray], axis=-1)
    elif c >= 3:  # noqa: PLR2004
        rgb = pixels_hwc[:, :, :3]
    else:
        # 2-channel: use first channel for R/G/B
        rgb = np.stack([pixels_hwc[:, :, 0]] * 3, axis=-1)

    if apply_srgb:
        rgb = _apply_srgb_transfer(rgb)
    else:
        rgb = np.clip(rgb, 0.0, 1.0)

    img = Image.fromarray((rgb * 255.0).astype(np.uint8), mode="RGB")
    img.thumbnail((max_width, max_height), Image.Resampling.LANCZOS)
    return img


def to_pil_gray(
    pixels_hw: np.ndarray,
    max_width: int = 1024,
    max_height: int = 1024,
) -> Image.Image:
    """Convert a float32 (H, W) array to a PIL greyscale image.

    Args:
        pixels_hw: float32 array (H, W) or (H, W, 1), values in [0, 1].
        max_width: Maximum output width in pixels.
        max_height: Maximum output height in pixels.

    Returns:
        PIL Image in mode "L".
    """
    if pixels_hw.ndim == 3:  # noqa: PLR2004
        pixels_hw = pixels_hw[:, :, 0]
    clipped = np.clip(pixels_hw, 0.0, 1.0)
    img = Image.fromarray((clipped * 255.0).astype(np.uint8), mode="L")
    img.thumbnail((max_width, max_height), Image.Resampling.LANCZOS)
    return img


def normalize_pixels(pixels: np.ndarray) -> np.ndarray:
    """Remap pixel values to [0, 1] based on actual min/max.

    Handles constant-value channels (max == min) by returning zeros.

    Args:
        pixels: float32 array, any shape.

    Returns:
        float32 array of same shape, values in [0, 1].
    """
    pmin = float(np.min(pixels))
    pmax = float(np.max(pixels))
    if pmax == pmin:
        return np.zeros_like(pixels)
    return ((pixels - pmin) / (pmax - pmin)).astype(np.float32)
