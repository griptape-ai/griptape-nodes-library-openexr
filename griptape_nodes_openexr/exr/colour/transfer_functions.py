"""Display transfer functions and the derived sRGB uint8 decoding lookup table."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True)
class PiecewiseTransferCurve:
    """Linear toe and power-segment parameters for a display encoding."""

    linear_cutoff: float
    toe_inclusive: bool
    toe_slope: float
    exponent: float
    gain: float
    offset: float


# Colour-standard acknowledgements: THIRD_PARTY_NOTICES.md.
# IEC 61966-2-1 sRGB encoding: linear toe followed by the 1/2.4 power segment.
SRGB = PiecewiseTransferCurve(
    linear_cutoff=0.0031308,
    toe_inclusive=True,
    toe_slope=12.92,
    exponent=1.0 / 2.4,
    gain=1.055,
    offset=0.055,
)

# ITU-R BT.709-6 Table 1, section 1.2, opto-electronic transfer characteristic.
# https://www.itu.int/rec/R-REC-BT.709-6-201506-I/en
REC709 = PiecewiseTransferCurve(
    linear_cutoff=0.018,
    toe_inclusive=False,
    toe_slope=4.5,
    exponent=0.45,
    gain=1.099,
    offset=0.099,
)

# ITU-R BT.1886 Annex 1, inverse EOTF with zero black luminance.
# https://www.itu.int/rec/R-REC-BT.1886/en
REC1886_GAMMA = 1.0 / 2.4  # zero-black-level BT.1886 display inverse EOTF (gamma 2.4)


_SPARSE_TOE_DENOMINATOR = 16


def _clamp_unit(arr: np.ndarray) -> np.ndarray:
    """Clamp to float32 [0, 1], mapping NaN and -inf to 0.0 and +inf to 1.0."""
    with np.errstate(over="ignore", invalid="ignore"):
        x = np.asarray(arr, dtype=np.float32)
        out = np.empty_like(x)
        np.fmax(x, np.float32(0.0), out=out)
        np.fmin(out, np.float32(1.0), out=out)
    return out


def _patch_linear_toe(
    src: np.ndarray, toe: np.ndarray, output: np.ndarray, slope: np.float32, operation: np.ufunc
) -> np.ndarray:
    """Patch the linear segment, limiting temporary arrays when the toe is sparse."""
    toe_count = int(np.count_nonzero(toe))
    if not toe_count:
        return output
    with np.errstate(over="ignore", invalid="ignore"):
        if toe_count * _SPARSE_TOE_DENOMINATOR < src.size:
            output[toe] = operation(np.fmax(src[toe], np.float32(0.0)), slope)
            return output
        tmp = np.empty_like(src)
        np.fmax(src, np.float32(0.0), out=tmp)
        operation(tmp, slope, out=tmp)
        return np.asarray(np.where(toe, tmp, output), dtype=np.float32)


def _encode_piecewise(arr: np.ndarray, *, curve: PiecewiseTransferCurve) -> np.ndarray:
    """Apply linear-toe and power encoding, allocating full-frame toe arrays only for a dense mask."""
    cutoff_f32 = np.float32(curve.linear_cutoff)
    slope_f32 = np.float32(curve.toe_slope)
    with np.errstate(over="ignore", invalid="ignore"):
        src = np.asarray(arr, dtype=np.float32)
        toe = ~(src > cutoff_f32) if curve.toe_inclusive else ~(src >= cutoff_f32)
        encoded = np.empty_like(src)
        np.fmax(src, cutoff_f32, out=encoded)
        np.fmin(encoded, np.float32(1.0), out=encoded)

    np.power(encoded, np.float32(curve.exponent), out=encoded, dtype=np.float32)
    encoded *= np.float32(curve.gain)
    encoded -= np.float32(curve.offset)

    return _patch_linear_toe(src, toe, encoded, slope_f32, np.multiply)


def linear_to_srgb(arr: np.ndarray) -> np.ndarray:
    """Encode display-linear values with the sRGB transfer function (IEC 61966-2-1).

    Args:
        arr: Float32 pixel array, any shape. Values outside [0, 1] are clamped.

    Returns:
        Display-encoded float32 array in [0, 1], same shape as input.
    """
    return _encode_piecewise(arr, curve=SRGB)


def srgb_to_linear(arr: np.ndarray) -> np.ndarray:
    """Decode sRGB-encoded values to linear light (the inverse of `linear_to_srgb`).

    Args:
        arr: Float32 pixel array, any shape. Values outside [0, 1] are clamped.

    Returns:
        Linear float32 array in [0, 1], same shape as input.
    """
    # The toe ends at linear_to_srgb's encoded continuity point: 0.0031308 * 12.92 = 0.040449936.
    # IEC 61966-2-1 rounds this to 0.04045.
    cutoff_f32 = np.float32(SRGB.linear_cutoff * SRGB.toe_slope)
    slope_f32 = np.float32(SRGB.toe_slope)
    with np.errstate(over="ignore", invalid="ignore"):
        src = np.asarray(arr, dtype=np.float32)
        toe = ~(src > cutoff_f32)
        linear = np.empty_like(src)
        np.fmax(src, cutoff_f32, out=linear)
        np.fmin(linear, np.float32(1.0), out=linear)

    linear += np.float32(SRGB.offset)
    linear /= np.float32(SRGB.gain)
    np.power(linear, np.float32(1.0 / SRGB.exponent), out=linear, dtype=np.float32)

    return _patch_linear_toe(src, toe, linear, slope_f32, np.divide)


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
    return _encode_piecewise(arr, curve=REC709)


def linear_to_rec1886(arr: np.ndarray) -> np.ndarray:
    """Encode display-linear values for an idealised zero-black BT.1886 display.

    ITU-R BT.1886 defines a display EOTF with black-level lift. At zero black
    luminance, its inverse is a 1/2.4 power curve with no linear toe.

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
