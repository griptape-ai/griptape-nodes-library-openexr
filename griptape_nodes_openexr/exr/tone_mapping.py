"""Exposure and tone mapping for HDR-to-display conversion."""

from __future__ import annotations

import numpy as np

TONE_FILMIC = "filmic"
TONE_LINEAR = "linear"
TONE_CURVES_DESCRIPTION = (
    "'filmic' applies the Narkowicz 2015 curve; 'linear' clamps to [0, 1]. Both are then sRGB-encoded for display."
)

EV_MIN = -10.0  # Exposure Value in stops (EV); range matches practical HDR capture limits
EV_MAX = 10.0

SRGB_LINEAR_CUTOFF = 0.0031308  # sRGB piecewise split: linear toe below, power curve above
SRGB_TOE_SLOPE = 12.92  # linear toe: encoded = linear * SRGB_TOE_SLOPE
SRGB_GAMMA = 1.0 / 2.4  # power-segment exponent
SRGB_GAIN = 1.055  # power-segment scale
SRGB_OFFSET = 0.055  # power-segment offset: encoded = SRGB_GAIN * linear**SRGB_GAMMA - SRGB_OFFSET


def apply_exposure(arr: np.ndarray, ev: float) -> np.ndarray:
    """Scale pixel values by 2^ev (EV stops).

    Args:
        arr: Float32 pixel array, any shape.
        ev: Exposure value in stops. Positive = brighter, negative = darker.

    Returns:
        Scaled float32 array, same shape as input.
    """
    return (arr * (2.0**ev)).astype(np.float32)


def apply_filmic(arr: np.ndarray) -> np.ndarray:
    """Apply Narkowicz 2015 filmic tone curve.

    Formula: (x*(2.51x+0.03))/(x*(2.43x+0.59)+0.14)

    The curve output is display-linear. The sRGB transfer function is applied
    separately by `apply_tone_mapping`, which is what callers should use.

    Args:
        arr: Float32 pixel array, any shape. Values may exceed [0, 1].

    Returns:
        Tone-mapped float32 array in approximately [0, 1], same shape as input.

    # TODO: Revisit when colorspace handling is enabled. Assumes scene-linear input
    # in display-referred primaries (Rec.709/sRGB). Wide-gamut (AP1) input will have a
    # slight gamut mismatch: acceptable for preview, not for delivery.
    """
    x = arr.astype(np.float32)
    numerator = x * (2.51 * x + 0.03)
    denominator = x * (2.43 * x + 0.59) + 0.14
    return np.clip(numerator / denominator, 0.0, 1.0).astype(np.float32)


def linear_to_srgb(arr: np.ndarray) -> np.ndarray:
    """Encode display-linear values with the sRGB transfer function (IEC 61966-2-1).

    Args:
        arr: Float32 pixel array, any shape. Values outside [0, 1] are clamped.

    Returns:
        Display-encoded float32 array in [0, 1], same shape as input.
    """
    # np.power on a negative base with a fractional exponent returns NaN.
    x = np.clip(arr, 0.0, 1.0).astype(np.float32, copy=False)

    # Power segment over the whole array; the linear toe is patched in below.
    encoded = np.power(x, np.float32(SRGB_GAMMA), dtype=np.float32)
    encoded *= np.float32(SRGB_GAIN)
    encoded -= np.float32(SRGB_OFFSET)

    np.copyto(encoded, x * np.float32(SRGB_TOE_SLOPE), where=x <= SRGB_LINEAR_CUTOFF)
    return encoded


def apply_tone_mapping(rgb: np.ndarray, tone_mapping: str) -> np.ndarray:
    """Convert scene-linear RGB to display-encoded RGB.

    Applies the selected tone curve, then the sRGB transfer function. Both steps
    are needed: the tone curves produce display-linear values, and an 8-bit PNG
    with no embedded profile is read as sRGB by viewers.

    Not used for OCIO colour management, where the display/view transform already
    includes the display's transfer function.

    Args:
        rgb: Float32 scene-linear RGB array.
        tone_mapping: Either 'filmic' or 'linear' (case-insensitive).

    Returns:
        Display-encoded float32 array in [0, 1], same shape as input.

    Raises:
        ValueError: If tone_mapping is not a supported curve.
    """
    tone = tone_mapping.lower()
    if tone == TONE_FILMIC:
        display_linear = apply_filmic(rgb)
    elif tone == TONE_LINEAR:
        # linear_to_srgb clips internally; no need to clip here too.
        display_linear = rgb.astype(np.float32)
    else:
        msg = f"Unsupported tone mapping: {tone!r}"
        raise ValueError(msg)
    return linear_to_srgb(display_linear)


def to_uint8(arr: np.ndarray) -> np.ndarray:
    """Clamp to [0, 1] and quantize to uint8, rounding to nearest.

    Expects display-encoded colour, or an alpha plane. This does not apply a
    transfer function; use `apply_tone_mapping` or an OCIO display transform for
    that. Alpha is coverage rather than colour, so quantizing it here is correct.

    Args:
        arr: Float32 array of any shape, values nominally in [0, 1].

    Returns:
        uint8 array of the same shape, with values in [0, 255].
    """
    return (np.clip(arr, 0.0, 1.0) * 255.0 + 0.5).astype(np.uint8)
