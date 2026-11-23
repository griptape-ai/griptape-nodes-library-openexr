"""Exposure and tone mapping for HDR-to-display conversion."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from griptape_nodes_openexr.exr.colour.colour_spaces import (
    ACES2065_TO_REC709,
    ACESCG_TO_REC709,
    REC709_TO_ACES2065,
    REC709_TO_ACESCG,
)
from griptape_nodes_openexr.exr.colour.transfer_functions import (
    _clamp_only,
    _clamp_unit,
    linear_to_rec709,
    linear_to_rec1886,
    linear_to_srgb,
)

TONE_SRGB = "Linear sRGB to sRGB"
TONE_REC709 = "Linear sRGB to rec709"
TONE_REC1886 = "Linear sRGB to rec1886"
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

# Lowercase aliases preserve earlier names used by saved workflows and scripts.
TONE_ALIASES = {
    "srgb": TONE_SRGB,
    "rec709": TONE_REC709,
    "rec1886": TONE_REC1886,
}

# Every supported choice and earlier alias, keyed in lowercase to its canonical TONE_CHOICES name.
_CANONICAL_TONES = {
    **{tone.lower(): tone for tone in TONE_CHOICES},
    **TONE_ALIASES,
}

TONE_CURVES_DESCRIPTION = (
    "'Linear sRGB to sRGB' applies the sRGB transfer function; 'Linear sRGB to rec709' "
    "applies the BT.709 OETF; 'Linear sRGB to rec1886' applies BT.1886 (gamma 2.4); 'raw' "
    "clamps to [0, 1] with no transfer function. These choices expect scene-linear RGB with Rec.709/sRGB primaries. "
    "Values above 1.0 clip. Lower exposure to bring highlights into range. "
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
        "Nuke's default viewer. Use 'Linear sRGB to sRGB' for a similar look, or switch "
        "to 'ocio' mode for a real tone-mapped view transform."
    ),
    "linear": (
        "'linear' is no longer supported: despite the name it sRGB-encoded the image. "
        "Use 'Linear sRGB to sRGB' for the same result, or 'raw' for true unencoded linear."
    ),
}

EV_MIN = -10.0  # Exposure Value in stops (EV); range matches practical HDR capture limits
EV_MAX = 10.0

_RGB_CHANNELS = 3
_FLOAT32_MAX = float(np.finfo(np.float32).max)


def apply_exposure(arr: np.ndarray, ev: float) -> np.ndarray:
    """Scale pixel values by 2^ev (EV stops).

    Args:
        arr: Float32 pixel array, any shape.
        ev: Exposure value in stops. Positive = brighter, negative = darker.

    Returns:
        Scaled float32 array, same shape as input.
    """
    with np.errstate(over="ignore", invalid="ignore"):
        x = np.asarray(arr, dtype=np.float32)
        if ev == 0.0:
            return x
        return x * np.float32(2.0**ev)


def convert_gamut(rgb: np.ndarray, matrix: np.ndarray) -> np.ndarray:
    """Convert linear RGB between primaries with a 3x3 matrix.

    Out-of-gamut values become negative or exceed 1.0. The following display
    encoding clamps them. Non-finite inputs, including half-float EXR highlights
    that overflowed to +inf and NaN samples, are handled without spreading NaNs
    to other channels.

    Args:
        rgb: Float32 linear RGB array whose last axis holds the three channels.
        matrix: 3x3 conversion matrix, such as ACESCG_TO_REC709.

    Returns:
        Finite float32 linear RGB in the target primaries, same shape as input.

    Raises:
        ValueError: If the last axis does not hold three channels.
    """
    if rgb.ndim == 0 or rgb.shape[-1] != _RGB_CHANNELS:
        msg = f"Gamut conversion needs RGB with 3 channels on the last axis, got shape {rgb.shape}."
        raise ValueError(msg)
    mat_t = matrix.T if matrix.dtype == np.float32 else np.asarray(matrix, dtype=np.float32).T
    with np.errstate(over="ignore", invalid="ignore"):
        x = np.asarray(rgb, dtype=np.float32)
        out = np.matmul(x, mat_t, dtype=np.float32)
    if not np.isfinite(out).all():
        bad = ~np.isfinite(out).all(axis=-1)
        clean = np.nan_to_num(x[bad], nan=0.0, posinf=_FLOAT32_MAX, neginf=-_FLOAT32_MAX).astype(np.float64)
        with np.errstate(over="ignore", invalid="ignore"):
            out64 = np.matmul(clean, np.asarray(matrix, dtype=np.float64).T)
        out[bad] = np.clip(out64, -_FLOAT32_MAX, _FLOAT32_MAX).astype(np.float32)
    return out


# Display encoding per base choice, keyed by the lowercased name.
_TONE_TRANSFER_FUNCTIONS = {
    TONE_SRGB.lower(): linear_to_srgb,
    TONE_REC709.lower(): linear_to_rec709,
    TONE_REC1886.lower(): linear_to_rec1886,
    TONE_RAW.lower(): _clamp_only,
}


@dataclass(frozen=True)
class _GamutConversion:
    forward: np.ndarray
    inverse: np.ndarray
    encoding: str


# ACES choices, keyed by the lowercased name: precomputed forward/inverse float32
# matrices and the base choice whose encoding follows. Every choice in TONE_CHOICES
# has an entry here or in _TONE_TRANSFER_FUNCTIONS.
_TONE_GAMUT_CONVERSIONS = {
    TONE_ACESCG_SRGB.lower(): _GamutConversion(ACESCG_TO_REC709, REC709_TO_ACESCG, TONE_SRGB),
    TONE_ACESCG_REC709.lower(): _GamutConversion(ACESCG_TO_REC709, REC709_TO_ACESCG, TONE_REC709),
    TONE_ACES2065_SRGB.lower(): _GamutConversion(ACES2065_TO_REC709, REC709_TO_ACES2065, TONE_SRGB),
}


def canonical_tone_mapping(tone_mapping: str) -> str:
    """Return the canonical TONE_CHOICES spelling for a transform or earlier alias.

    Args:
        tone_mapping: A display transform name (case-insensitive).

    Returns:
        The canonical choice from TONE_CHOICES when recognised, otherwise
        tone_mapping unchanged so validation can report it.
    """
    return _CANONICAL_TONES.get(tone_mapping.lower(), tone_mapping)


def validate_tone_mapping(tone_mapping: str) -> str:
    """Resolve `tone_mapping` to its canonical choice, raising ValueError if retired or unknown."""
    key = tone_mapping.lower()
    if key in _CANONICAL_TONES:
        return _CANONICAL_TONES[key]
    retired = retired_tone_message(key)
    if retired is not None:
        raise ValueError(retired)
    msg = f"Unsupported tone mapping: {tone_mapping!r}. Choose one of: {', '.join(TONE_CHOICES)}."
    raise ValueError(msg)


def _tone_key(tone_mapping: str) -> str:
    return canonical_tone_mapping(tone_mapping).lower()


def gamut_matrix(tone_mapping: str) -> np.ndarray | None:
    """Return the transform's conversion matrix to Rec.709 primaries, if any.

    Args:
        tone_mapping: A display transform name (case-insensitive).

    Returns:
        The precomputed float32 3x3 matrix for an ACES choice, or None for a
        choice that assumes Rec.709 primaries already, or an unknown name.
    """
    conversion = _TONE_GAMUT_CONVERSIONS.get(_tone_key(tone_mapping))
    return None if conversion is None else conversion.forward


def inverse_gamut_matrix(tone_mapping: str) -> np.ndarray | None:
    """Return the matrix from Rec.709 to `tone_mapping`'s source primaries.

    Args:
        tone_mapping: A display transform name (case-insensitive).

    Returns:
        The precomputed float32 3x3 inverse matrix for an ACES choice, or None
        for a choice that assumes Rec.709 primaries already, or an unknown name.
    """
    conversion = _TONE_GAMUT_CONVERSIONS.get(_tone_key(tone_mapping))
    return None if conversion is None else conversion.inverse


def display_encoding(tone_mapping: str) -> str | None:
    """Return the base encoding used by a display transform.

    All choices output Rec.709 primaries. Their ICC profile and video tags
    therefore depend on the encoding. ACES choices return their base encoding;
    base choices return themselves.

    Args:
        tone_mapping: A display transform name (case-insensitive).

    Returns:
        The lowercased base choice, or None for an unknown name.
    """
    tone = _tone_key(tone_mapping)
    if tone in _TONE_GAMUT_CONVERSIONS:
        return _TONE_GAMUT_CONVERSIONS[tone].encoding.lower()
    if tone in _TONE_TRANSFER_FUNCTIONS:
        return tone
    return None


def retired_tone_message(tone_mapping: str) -> str | None:
    """Return the message for a retired transform name, or None for other names."""
    return _RETIRED_TONES.get(tone_mapping.lower())


def apply_tone_mapping(rgb: np.ndarray, tone_mapping: str) -> np.ndarray:
    """Convert scene-linear RGB to display-encoded RGB.

    The base choices assume linear Rec.709/sRGB primaries. The ACES choices
    convert ACES primaries to Rec.709 first, then apply a base choice's
    encoding. Each choice applies a display transfer function that clips scene
    values above 1.0. Use 'ocio' mode for tone mapping with highlight roll-off.

    Output is an 8-bit PNG with no embedded profile unless `embed_color_profile`
    is set, so by default viewers read it as sRGB. Choosing 'Linear sRGB to
    rec709' or 'Linear sRGB to rec1886' therefore shows the same deliberate mismatch a Rec.709 view
    shows on an sRGB monitor.

    OCIO colour management bypasses this function. Its display/view transform
    already includes the display's transfer function.

    Args:
        rgb: Float32 scene-linear RGB array. The ACES choices need the three
            channels on the last axis.
        tone_mapping: One of TONE_CHOICES or an earlier name for one
            (case-insensitive).

    Returns:
        Display-encoded float32 array in [0, 1], same shape as input.

    Raises:
        ValueError: If tone_mapping is not a supported curve, or an ACES
            choice gets an array without three channels on its last axis.
    """
    tone = validate_tone_mapping(tone_mapping).lower()
    if tone in _TONE_GAMUT_CONVERSIONS:
        conversion = _TONE_GAMUT_CONVERSIONS[tone]
        return _TONE_TRANSFER_FUNCTIONS[conversion.encoding.lower()](convert_gamut(rgb, conversion.forward))
    return _TONE_TRANSFER_FUNCTIONS[tone](rgb)


def to_uint8(arr: np.ndarray) -> np.ndarray:
    """Clamp to [0, 1] and quantize to uint8, rounding to nearest.

    Accepts display-encoded colour or an alpha plane. Apply the colour transfer
    function first with `apply_tone_mapping` or an OCIO display transform.
    Alpha represents coverage and can be quantized directly.

    Args:
        arr: Float32 array of any shape, values nominally in [0, 1].

    Returns:
        uint8 array of the same shape, with values in [0, 255].
    """
    scaled = _clamp_unit(arr)
    scaled *= np.float32(255.0)
    scaled += np.float32(0.5)
    return scaled.astype(np.uint8)
