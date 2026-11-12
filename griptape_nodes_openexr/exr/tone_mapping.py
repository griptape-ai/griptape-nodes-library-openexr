"""Exposure and tone mapping for HDR-to-display conversion."""

from __future__ import annotations

import numpy as np

TONE_SRGB = "sRGB"
TONE_REC709 = "rec709"
TONE_REC1886 = "rec1886"
TONE_RAW = "raw"
TONE_ACESCG_SRGB = "ACEScg to sRGB"
TONE_ACESCG_REC709 = "ACEScg to rec709"
TONE_ACES2065_SRGB = "ACES2065-1 to sRGB"

TONE_CHOICES = [
    TONE_SRGB,
    TONE_REC709,
    TONE_REC1886,
    TONE_RAW,
    TONE_ACESCG_SRGB,
    TONE_ACESCG_REC709,
    TONE_ACES2065_SRGB,
]
TONE_DEFAULT = TONE_SRGB

TONE_CURVES_DESCRIPTION = (
    "'sRGB' applies the sRGB transfer function; 'rec709' applies the BT.709 OETF; "
    "'rec1886' applies BT.1886 (gamma 2.4); 'raw' clamps to [0, 1] with no transfer "
    "function. These assume linear Rec.709/sRGB primaries, match the views in Nuke's "
    "default config, and like that viewer clip scene values above 1.0. "
    "'ACEScg to sRGB', 'ACEScg to rec709' and 'ACES2065-1 to sRGB' first convert ACES "
    "primaries to Rec.709, then apply the named encoding. That is a gamut conversion, "
    "not the ACES output transform, so highlights still clip. Use 'ocio' mode for a "
    "tone-mapped view."
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

# Linear ACES to linear Rec.709 primaries, with a Bradford chromatic adaptation from the
# ACES white (about D60) to D65, so ACES white lands on display white. Each row gives
# one output channel as a weighted sum of the input (R, G, B), so every row sums to 1.
#
# Values are the published ones from colour-science (colour.RGB_to_RGB with
# chromatic_adaptation_transform="Bradford"), built from the primaries in SMPTE ST 2065-1 (AP0, ACES2065-1) and Academy S-2014-004 (AP1,
# ACEScg), and the BT.709 primaries and D65 white. tests/unit/test_tone_mapping.py
# rederives them from those primaries.
ACESCG_TO_REC709 = np.array(
    [
        [1.70505099, -0.62179212, -0.08325887],
        [-0.13025642, 1.14080474, -0.01054832],
        [-0.02400336, -0.12896898, 1.15297233],
    ]
)
ACES2065_TO_REC709 = np.array(
    [
        [2.52168619, -1.13413099, -0.38755520],
        [-0.27647991, 1.37271909, -0.09623917],
        [-0.01537806, -0.15297534, 1.16835340],
    ]
)

_RGB_CHANNELS = 3


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


def srgb_to_linear(arr: np.ndarray) -> np.ndarray:
    """Decode sRGB-encoded values to linear light (the inverse of `linear_to_srgb`).

    Args:
        arr: Float32 pixel array, any shape. Values outside [0, 1] are clamped.

    Returns:
        Linear float32 array in [0, 1], same shape as input.
    """
    x = np.clip(arr, 0.0, 1.0).astype(np.float32, copy=False)

    # Power segment over the whole array; the linear toe is patched in below.
    linear = (x + np.float32(SRGB_OFFSET)) / np.float32(SRGB_GAIN)
    np.power(linear, np.float32(1.0 / SRGB_GAMMA), out=linear, dtype=np.float32)

    # The toe ends where linear_to_srgb's toe ends, in the encoded domain: the linear cutoff
    # times the slope, about 0.04045 (the IEC 61966-2-1 decode threshold).
    np.copyto(linear, x / np.float32(SRGB_TOE_SLOPE), where=x <= SRGB_LINEAR_CUTOFF * SRGB_TOE_SLOPE)
    return linear


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


def convert_gamut(rgb: np.ndarray, matrix: np.ndarray) -> np.ndarray:
    """Convert linear RGB between primaries with a 3x3 matrix.

    Values a target gamut cannot hold come out negative or above 1.0; the
    display encode that follows clamps them.

    Args:
        rgb: Float32 linear RGB array whose last axis holds the three channels.
        matrix: 3x3 conversion matrix, such as ACESCG_TO_REC709.

    Returns:
        Float32 linear RGB in the target primaries, same shape as input.

    Raises:
        ValueError: If the last axis does not hold three channels.
    """
    if rgb.ndim == 0 or rgb.shape[-1] != _RGB_CHANNELS:
        msg = f"Gamut conversion needs RGB with 3 channels on the last axis, got shape {rgb.shape}."
        raise ValueError(msg)
    return np.matmul(rgb, matrix.T.astype(np.float32), dtype=np.float32)


# Display encoding per base choice, keyed by the lowercased name.
_TONE_TRANSFER_FUNCTIONS = {
    TONE_SRGB.lower(): linear_to_srgb,
    TONE_REC709.lower(): linear_to_rec709,
    TONE_REC1886.lower(): linear_to_rec1886,
    TONE_RAW.lower(): _clamp_only,
}

# ACES choices, keyed by the lowercased name: the matrix into Rec.709 primaries and
# the base choice whose encoding follows it. Every choice in TONE_CHOICES has an entry
# here or in _TONE_TRANSFER_FUNCTIONS.
_TONE_GAMUT_CONVERSIONS = {
    TONE_ACESCG_SRGB.lower(): (ACESCG_TO_REC709, TONE_SRGB),
    TONE_ACESCG_REC709.lower(): (ACESCG_TO_REC709, TONE_REC709),
    TONE_ACES2065_SRGB.lower(): (ACES2065_TO_REC709, TONE_SRGB),
}


def gamut_matrix(tone_mapping: str) -> np.ndarray | None:
    """The matrix a display transform applies into Rec.709 primaries, if any.

    Args:
        tone_mapping: A display transform name (case-insensitive).

    Returns:
        The 3x3 matrix for an ACES choice, or None for a choice that assumes
        Rec.709 primaries already, or an unknown name.
    """
    conversion = _TONE_GAMUT_CONVERSIONS.get(tone_mapping.lower())
    return None if conversion is None else conversion[0]


def display_encoding(tone_mapping: str) -> str | None:
    """The base choice whose encoding a display transform's output carries.

    Every choice writes Rec.709 primaries, so the ICC profile and video tags
    for a choice depend only on this. An ACES choice returns the base choice
    it encodes with; a base choice returns itself.

    Args:
        tone_mapping: A display transform name (case-insensitive).

    Returns:
        The lowercased base choice, or None for an unknown name.
    """
    tone = tone_mapping.lower()
    if tone in _TONE_GAMUT_CONVERSIONS:
        return _TONE_GAMUT_CONVERSIONS[tone][1].lower()
    if tone in _TONE_TRANSFER_FUNCTIONS:
        return tone
    return None


def retired_tone_message(tone_mapping: str) -> str | None:
    """The message for a retired display transform name, or None if it isn't retired."""
    return _RETIRED_TONES.get(tone_mapping.lower())


def apply_tone_mapping(rgb: np.ndarray, tone_mapping: str) -> np.ndarray:
    """Convert scene-linear RGB to display-encoded RGB.

    The base choices assume linear Rec.709/sRGB primaries. The ACES choices
    convert ACES primaries to Rec.709 first, then apply a base choice's
    encoding. Every choice ends in a pure display transfer function, so scene
    values above 1.0 clip. Nuke's default viewer behaves the same way; a tone-mapped view
    that rolls off highlights belongs in 'ocio' mode.

    Output is an 8-bit PNG with no embedded profile unless `embed_color_profile`
    is set, so by default viewers read it as sRGB. Choosing 'rec709' or
    'rec1886' therefore shows the same deliberate mismatch a Rec.709 view
    shows on an sRGB monitor.

    Not used for OCIO colour management, where the display/view transform already
    includes the display's transfer function.

    Args:
        rgb: Float32 scene-linear RGB array. The ACES choices need the three
            channels on the last axis.
        tone_mapping: One of TONE_CHOICES (case-insensitive).

    Returns:
        Display-encoded float32 array in [0, 1], same shape as input.

    Raises:
        ValueError: If tone_mapping is not a supported curve, or an ACES
            choice gets an array without three channels on its last axis.
    """
    tone = tone_mapping.lower()

    retired = retired_tone_message(tone)
    if retired is not None:
        raise ValueError(retired)

    if tone in _TONE_GAMUT_CONVERSIONS:
        matrix, encoding = _TONE_GAMUT_CONVERSIONS[tone]
        return _TONE_TRANSFER_FUNCTIONS[encoding.lower()](convert_gamut(rgb.astype(np.float32), matrix))

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
