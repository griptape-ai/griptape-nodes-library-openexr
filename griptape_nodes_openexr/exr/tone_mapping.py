"""Exposure and tone mapping for HDR-to-display conversion."""

from __future__ import annotations

import numpy as np

TONE_FILMIC = "filmic"
TONE_LINEAR = "linear"

EV_MIN = -10.0  # Exposure Value in stops (EV); range matches practical HDR capture limits
EV_MAX = 10.0


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

    Args:
        arr: Float32 pixel array, any shape. Values may exceed [0, 1].

    Returns:
        Tone-mapped float32 array in approximately [0, 1], same shape as input.

    # TODO: Revisit when colorspace handling is enabled — assumes scene-linear input
    # in display-referred primaries (Rec.709/sRGB). Wide-gamut (AP1) input will have a
    # slight gamut mismatch: acceptable for preview, not for delivery.
    """
    x = arr.astype(np.float32)
    numerator = x * (2.51 * x + 0.03)
    denominator = x * (2.43 * x + 0.59) + 0.14
    return np.clip(numerator / denominator, 0.0, 1.0).astype(np.float32)


def apply_tone_mapping(rgb: np.ndarray, tone_mapping: str) -> np.ndarray:
    """Apply tone mapping to a float32 RGB array."""
    tone = tone_mapping.lower()
    if tone == TONE_FILMIC:
        return apply_filmic(rgb)
    elif tone == TONE_LINEAR:
        return np.clip(rgb, 0.0, 1.0).astype(np.float32)
    else:
        msg = f"Unsupported tone mapping: {tone!r}"
        raise ValueError(msg)


def to_uint8_srgb(rgb: np.ndarray) -> np.ndarray:
    """Clamp to [0, 1] and convert to uint8.

    Args:
        rgb: Float32 array of shape (H, W, 3), values nominally in [0, 1].

    Returns:
        uint8 array of shape (H, W, 3) with values in [0, 255].
    """
    return (np.clip(rgb, 0.0, 1.0) * 255.0 + 0.5).astype(np.uint8)
