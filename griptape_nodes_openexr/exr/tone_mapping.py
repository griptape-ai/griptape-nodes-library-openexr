"""Exposure and tone mapping for HDR-to-display conversion."""

from __future__ import annotations

import numpy as np

TONE_SRGB = "sRGB"
TONE_REC709 = "rec709"
TONE_REC1886 = "rec1886"
TONE_RAW = "raw"

TONE_CHOICES = [TONE_SRGB, TONE_REC709, TONE_REC1886, TONE_RAW]
TONE_DEFAULT = TONE_SRGB

TONE_CURVES_DESCRIPTION = (
    "'sRGB' applies the sRGB transfer function; 'rec709' applies the BT.709 OETF; "
    "'rec1886' applies BT.1886 (gamma 2.4); 'raw' clamps to [0, 1] with no transfer "
    "function. These match the views in Nuke's default config, and like that viewer "
    "they clip scene values above 1.0. Use 'ocio' mode for a tone-mapped view."
)

# Retired choice names, mapped to the message shown when one is still in use.
_RETIRED_TONES = {
    "filmic": (
        "'filmic' is no longer supported: it was a Narkowicz 2015 approximation of "
        "the ACES RRT, and basic mode now offers only display encodings, matching "
        "Nuke's default viewer. Use 'sRGB' for a similar look, or switch to 'ocio' "
        "mode for a real tone-mapped view transform."
    ),
    "linear": (
        "'linear' is no longer supported: despite the name it sRGB-encoded the image. "
        "Use 'sRGB' for the same result, or 'raw' for true unencoded linear."
    ),
}

EV_MIN = -10.0  # Exposure Value in stops (EV); range matches practical HDR capture limits
EV_MAX = 10.0

SRGB_LINEAR_CUTOFF = 0.0031308  # sRGB piecewise split: linear toe below, power curve above
SRGB_TOE_SLOPE = 12.92  # linear toe: encoded = linear * SRGB_TOE_SLOPE
SRGB_GAMMA = 1.0 / 2.4  # power-segment exponent
SRGB_GAIN = 1.055  # power-segment scale
SRGB_OFFSET = 0.055  # power-segment offset: encoded = SRGB_GAIN * linear**SRGB_GAMMA - SRGB_OFFSET

REC709_CUTOFF = 0.018  # BT.709 OETF piecewise split: linear toe below, power curve above
REC709_TOE_SLOPE = 4.5  # linear toe: encoded = linear * REC709_TOE_SLOPE
REC709_GAMMA = 0.45  # power-segment exponent
REC709_GAIN = 1.099  # power-segment scale
REC709_OFFSET = 0.099  # power-segment offset

REC1886_GAMMA = 1.0 / 2.4  # BT.1886 is a pure power function with no linear toe


def apply_exposure(arr: np.ndarray, ev: float) -> np.ndarray:
    """Scale pixel values by 2^ev (EV stops).

    Args:
        arr: Float32 pixel array, any shape.
        ev: Exposure value in stops. Positive = brighter, negative = darker.

    Returns:
        Scaled float32 array, same shape as input.
    """
    return (arr * (2.0**ev)).astype(np.float32)


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


def linear_to_rec709(arr: np.ndarray) -> np.ndarray:
    """Encode display-linear values with the BT.709 OETF (ITU-R BT.709).

    This is the curve Nuke's 'rec709' view applies. It is darker through the
    midtones than sRGB, whose effective gamma is closer to 2.2.

    Args:
        arr: Float32 pixel array, any shape. Values outside [0, 1] are clamped.

    Returns:
        Display-encoded float32 array in [0, 1], same shape as input.
    """
    # np.power on a negative base with a fractional exponent returns NaN.
    x = np.clip(arr, 0.0, 1.0).astype(np.float32, copy=False)

    # Power segment over the whole array; the linear toe is patched in below.
    encoded = np.power(x, np.float32(REC709_GAMMA), dtype=np.float32)
    encoded *= np.float32(REC709_GAIN)
    encoded -= np.float32(REC709_OFFSET)

    np.copyto(encoded, x * np.float32(REC709_TOE_SLOPE), where=x < REC709_CUTOFF)
    return encoded


def linear_to_rec1886(arr: np.ndarray) -> np.ndarray:
    """Encode display-linear values with the BT.1886 transfer function.

    A pure 2.4 power function with no linear toe, matching an idealised
    Rec.709 display. Nuke exposes this as its 'rec1886' view.

    Args:
        arr: Float32 pixel array, any shape. Values outside [0, 1] are clamped.

    Returns:
        Display-encoded float32 array in [0, 1], same shape as input.
    """
    x = np.clip(arr, 0.0, 1.0).astype(np.float32, copy=False)
    return np.power(x, np.float32(REC1886_GAMMA), dtype=np.float32)


def _clamp_only(arr: np.ndarray) -> np.ndarray:
    """Clamp to [0, 1] without applying any transfer function."""
    return np.clip(arr, 0.0, 1.0).astype(np.float32, copy=False)


# Transfer function per choice, keyed by the lowercased name. A new choice
# needs an entry here and in TONE_CHOICES.
_TONE_TRANSFER_FUNCTIONS = {
    TONE_SRGB.lower(): linear_to_srgb,
    TONE_REC709.lower(): linear_to_rec709,
    TONE_REC1886.lower(): linear_to_rec1886,
    TONE_RAW.lower(): _clamp_only,
}


def apply_tone_mapping(rgb: np.ndarray, tone_mapping: str) -> np.ndarray:
    """Convert scene-linear RGB to display-encoded RGB.

    Every choice is a pure display transfer function, so scene values above
    1.0 clip. Nuke's default viewer behaves the same way; a tone-mapped view
    that rolls off highlights belongs in 'ocio' mode.

    Output is an 8-bit PNG with no embedded profile, which viewers read as
    sRGB. Choosing 'rec709' or 'rec1886' therefore shows the same deliberate
    mismatch a Rec.709 view shows on an sRGB monitor.

    Not used for OCIO colour management, where the display/view transform already
    includes the display's transfer function.

    Args:
        rgb: Float32 scene-linear RGB array.
        tone_mapping: One of TONE_CHOICES (case-insensitive).

    Returns:
        Display-encoded float32 array in [0, 1], same shape as input.

    Raises:
        ValueError: If tone_mapping is not a supported curve.
    """
    tone = tone_mapping.lower()

    retired = _RETIRED_TONES.get(tone)
    if retired is not None:
        raise ValueError(retired)

    if tone not in _TONE_TRANSFER_FUNCTIONS:
        msg = f"Unsupported tone mapping: {tone_mapping!r}. Choose one of: {', '.join(TONE_CHOICES)}."
        raise ValueError(msg)

    return _TONE_TRANSFER_FUNCTIONS[tone](rgb.astype(np.float32))


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
