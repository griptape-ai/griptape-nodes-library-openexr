"""Exposure and tone mapping for HDR-to-display conversion."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

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

# Earlier names for the linear sRGB choices, kept so saved workflows and scripted values
# still resolve. Keyed by the lowercased name.
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
    "clamps to [0, 1] with no transfer function. These assume linear Rec.709/sRGB "
    "primaries, match the sRGB, rec709, rec1886 and raw views in Nuke's default config, "
    "and like that viewer clip scene values above 1.0. "
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
def _readonly_matrix_pair(rows: list[list[float]]) -> tuple[np.ndarray, np.ndarray]:
    f64 = np.array(rows, dtype=np.float64)
    forward = f64.astype(np.float32)
    inverse = np.linalg.inv(f64).astype(np.float32)
    forward.flags.writeable = False
    inverse.flags.writeable = False
    return forward, inverse


ACESCG_TO_REC709, REC709_TO_ACESCG = _readonly_matrix_pair(
    [
        [1.70505099, -0.62179212, -0.08325887],
        [-0.13025642, 1.14080474, -0.01054832],
        [-0.02400336, -0.12896898, 1.15297233],
    ]
)
ACES2065_TO_REC709, REC709_TO_ACES2065 = _readonly_matrix_pair(
    [
        [2.52168619, -1.13413099, -0.38755520],
        [-0.27647991, 1.37271909, -0.09623917],
        [-0.01537806, -0.15297534, 1.16835340],
    ]
)

_RGB_CHANNELS = 3
_FLOAT32_MAX = float(np.finfo(np.float32).max)
_SPARSE_TOE_DENOMINATOR = 16


def _clamp_unit(arr: np.ndarray) -> np.ndarray:
    """Clamp to float32 [0, 1], mapping NaN and -inf to 0.0 and +inf to 1.0."""
    with np.errstate(over="ignore", invalid="ignore"):
        x = np.asarray(arr, dtype=np.float32)
        out = np.empty_like(x)
        np.fmax(x, np.float32(0.0), out=out)
        np.fmin(out, np.float32(1.0), out=out)
    return out


def _encode_piecewise(  # noqa: PLR0913
    arr: np.ndarray,
    *,
    cutoff: float,
    toe_inclusive: bool,
    toe_slope: float,
    gamma: float,
    gain: float,
    offset: float,
) -> np.ndarray:
    """Apply a piecewise linear-toe + power encoding without eager full-frame temporaries."""
    cutoff_f32 = np.float32(cutoff)
    slope_f32 = np.float32(toe_slope)
    with np.errstate(over="ignore", invalid="ignore"):
        src = np.asarray(arr, dtype=np.float32)
        toe = ~(src > cutoff_f32) if toe_inclusive else ~(src >= cutoff_f32)
        encoded = np.empty_like(src)
        np.fmax(src, cutoff_f32, out=encoded)
        np.fmin(encoded, np.float32(1.0), out=encoded)

    np.power(encoded, np.float32(gamma), out=encoded, dtype=np.float32)
    encoded *= np.float32(gain)
    encoded -= np.float32(offset)

    toe_count = int(np.count_nonzero(toe))
    if toe_count:
        with np.errstate(over="ignore", invalid="ignore"):
            if toe_count * _SPARSE_TOE_DENOMINATOR < src.size:
                encoded[toe] = np.fmax(src[toe], np.float32(0.0)) * slope_f32
            else:
                tmp = np.empty_like(src)
                np.fmax(src, np.float32(0.0), out=tmp)
                tmp *= slope_f32
                encoded = np.asarray(np.where(toe, tmp, encoded), dtype=np.float32)
    return encoded


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


def linear_to_srgb(arr: np.ndarray) -> np.ndarray:
    """Encode display-linear values with the sRGB transfer function (IEC 61966-2-1).

    Args:
        arr: Float32 pixel array, any shape. Values outside [0, 1] are clamped.

    Returns:
        Display-encoded float32 array in [0, 1], same shape as input.
    """
    return _encode_piecewise(
        arr,
        cutoff=SRGB_LINEAR_CUTOFF,
        toe_inclusive=True,
        toe_slope=SRGB_TOE_SLOPE,
        gamma=SRGB_GAMMA,
        gain=SRGB_GAIN,
        offset=SRGB_OFFSET,
    )


def srgb_to_linear(arr: np.ndarray) -> np.ndarray:
    """Decode sRGB-encoded values to linear light (the inverse of `linear_to_srgb`).

    Args:
        arr: Float32 pixel array, any shape. Values outside [0, 1] are clamped.

    Returns:
        Linear float32 array in [0, 1], same shape as input.
    """
    # The toe ends where linear_to_srgb's toe ends, in the encoded domain: the linear cutoff
    # times the slope, about 0.04045 (the IEC 61966-2-1 decode threshold).
    cutoff_f32 = np.float32(SRGB_LINEAR_CUTOFF * SRGB_TOE_SLOPE)
    slope_f32 = np.float32(SRGB_TOE_SLOPE)
    with np.errstate(over="ignore", invalid="ignore"):
        src = np.asarray(arr, dtype=np.float32)
        toe = ~(src > cutoff_f32)
        linear = np.empty_like(src)
        np.fmax(src, cutoff_f32, out=linear)
        np.fmin(linear, np.float32(1.0), out=linear)

    linear += np.float32(SRGB_OFFSET)
    linear /= np.float32(SRGB_GAIN)
    np.power(linear, np.float32(1.0 / SRGB_GAMMA), out=linear, dtype=np.float32)

    toe_count = int(np.count_nonzero(toe))
    if toe_count:
        with np.errstate(over="ignore", invalid="ignore"):
            if toe_count * _SPARSE_TOE_DENOMINATOR < src.size:
                linear[toe] = np.fmax(src[toe], np.float32(0.0)) / slope_f32
            else:
                tmp = np.empty_like(src)
                np.fmax(src, np.float32(0.0), out=tmp)
                tmp /= slope_f32
                linear = np.asarray(np.where(toe, tmp, linear), dtype=np.float32)
    return linear


_SRGB_UINT8_TO_LINEAR = srgb_to_linear(np.arange(256, dtype=np.float32) / np.float32(255.0))
_SRGB_UINT8_TO_LINEAR.flags.writeable = False


def srgb_uint8_to_linear(arr: np.ndarray) -> np.ndarray:
    """Decode uint8 sRGB code values in [0, 255] to float32 linear light via a 256-entry LUT."""
    if arr.dtype != np.uint8:
        msg = f"srgb_uint8_to_linear expects a uint8 array, got {arr.dtype}."
        raise ValueError(msg)
    return _SRGB_UINT8_TO_LINEAR[arr]


def linear_to_rec709(arr: np.ndarray) -> np.ndarray:
    """Encode display-linear values with the BT.709 OETF (ITU-R BT.709).

    This is the curve Nuke's 'rec709' view applies. It is darker through the
    midtones than sRGB, whose effective gamma is closer to 2.2.

    Args:
        arr: Float32 pixel array, any shape. Values outside [0, 1] are clamped.

    Returns:
        Display-encoded float32 array in [0, 1], same shape as input.
    """
    return _encode_piecewise(
        arr,
        cutoff=REC709_CUTOFF,
        toe_inclusive=False,
        toe_slope=REC709_TOE_SLOPE,
        gamma=REC709_GAMMA,
        gain=REC709_GAIN,
        offset=REC709_OFFSET,
    )


def linear_to_rec1886(arr: np.ndarray) -> np.ndarray:
    """Encode display-linear values with the BT.1886 transfer function.

    A pure 2.4 power function with no linear toe, matching an idealised
    Rec.709 display. Nuke exposes this as its 'rec1886' view.

    Args:
        arr: Float32 pixel array, any shape. Values outside [0, 1] are clamped.

    Returns:
        Display-encoded float32 array in [0, 1], same shape as input.
    """
    x = _clamp_unit(arr)
    np.power(x, np.float32(REC1886_GAMMA), out=x, dtype=np.float32)
    return x


def _clamp_only(arr: np.ndarray) -> np.ndarray:
    """Clamp to [0, 1] without applying any transfer function."""
    return _clamp_unit(arr)


def convert_gamut(rgb: np.ndarray, matrix: np.ndarray) -> np.ndarray:
    """Convert linear RGB between primaries with a 3x3 matrix.

    Values a target gamut cannot hold come out negative or above 1.0; the
    display encode that follows clamps them. Non-finite inputs (such as half-float
    EXR highlights that overflowed to +inf, or NaN samples) are resolved without
    cross-channel NaN contamination.

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
    """The canonical TONE_CHOICES spelling for a display transform or earlier alias.

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
    """The matrix a display transform applies into Rec.709 primaries, if any.

    Args:
        tone_mapping: A display transform name (case-insensitive).

    Returns:
        The precomputed float32 3x3 matrix for an ACES choice, or None for a
        choice that assumes Rec.709 primaries already, or an unknown name.
    """
    conversion = _TONE_GAMUT_CONVERSIONS.get(_tone_key(tone_mapping))
    return None if conversion is None else conversion.forward


def inverse_gamut_matrix(tone_mapping: str) -> np.ndarray | None:
    """The matrix mapping Rec.709 primaries back into `tone_mapping`'s source primaries.

    Args:
        tone_mapping: A display transform name (case-insensitive).

    Returns:
        The precomputed float32 3x3 inverse matrix for an ACES choice, or None
        for a choice that assumes Rec.709 primaries already, or an unknown name.
    """
    conversion = _TONE_GAMUT_CONVERSIONS.get(_tone_key(tone_mapping))
    return None if conversion is None else conversion.inverse


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
    tone = _tone_key(tone_mapping)
    if tone in _TONE_GAMUT_CONVERSIONS:
        return _TONE_GAMUT_CONVERSIONS[tone].encoding.lower()
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
    is set, so by default viewers read it as sRGB. Choosing 'Linear sRGB to
    rec709' or 'Linear sRGB to rec1886' therefore shows the same deliberate mismatch a Rec.709 view
    shows on an sRGB monitor.

    Not used for OCIO colour management, where the display/view transform already
    includes the display's transfer function.

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

    Expects display-encoded colour, or an alpha plane. This does not apply a
    transfer function; use `apply_tone_mapping` or an OCIO display transform for
    that. Alpha is coverage rather than colour, so quantizing it here is correct.

    Args:
        arr: Float32 array of any shape, values nominally in [0, 1].

    Returns:
        uint8 array of the same shape, with values in [0, 255].
    """
    scaled = _clamp_unit(arr)
    scaled *= np.float32(255.0)
    scaled += np.float32(0.5)
    return scaled.astype(np.uint8)
