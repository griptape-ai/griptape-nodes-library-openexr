"""ICC profiles describing the display transforms in basic colour mode.

An 8-bit PNG with no embedded profile is read as sRGB by every viewer. That
suits 'Linear sRGB to sRGB' and mislabels other transfer curves, so a file
written with 'Linear sRGB to rec709' is misinterpreted by other applications.

These profiles identify the file's transfer curve without changing pixel values.
Colour-managed applications use the profile to decode them. Transforms that assume the same source primaries
(such as 'Linear sRGB to sRGB', 'Linear sRGB to rec709' and
'Linear sRGB to rec1886', or 'ACEScg to sRGB' and 'ACEScg to rec709') then
render to the same display colour in a managed viewer. Transforms with
different source primaries still differ because they apply different gamut
conversions before encoding.

ImageCms.createProfile offers only sRGB, LAB and XYZ, with no way to specify
primaries or a transfer curve. This module builds the profiles from the ICC v2
spec without importing PIL or OpenColorIO. Pillow writes the profile bytes
with `save(icc_profile=...)` without parsing them.

Every transform writes Rec.709 primaries, so the profiles differ only in
their tone reproduction curve. The ACES transforms convert to Rec.709 before
encoding, so each shares the profile of the base transform it encodes with.
"""

from __future__ import annotations

import struct
from typing import Literal

from griptape_nodes_openexr.exr.colour.tone_mapping import (
    TONE_ALIASES,
    TONE_CHOICES,
    TONE_RAW,
    TONE_REC709,
    TONE_REC1886,
    TONE_SRGB,
    display_encoding,
)
from griptape_nodes_openexr.exr.colour.transfer_functions import (
    REC709_CUTOFF,
    REC709_GAIN,
    REC709_GAMMA,
    REC709_OFFSET,
    REC709_TOE_SLOPE,
    REC1886_GAMMA,
    SRGB_GAIN,
    SRGB_GAMMA,
    SRGB_LINEAR_CUTOFF,
    SRGB_OFFSET,
    SRGB_TOE_SLOPE,
)

TransferCurve = Literal["srgb", "rec709", "gamma"]

# Rec.709 primaries chromatically adapted via Bradford from D65 (0.3127, 0.3290)
# to the ICC D50 profile connection space illuminant, with fixed-point columns
# summing to _D50_XYZ. ICC v2 records the actual D65 media white point in wtpt
# alongside the D65-to-D50 Bradford matrix in chad.
_RED_XYZ = (0.43603516, 0.22248840, 0.01391602)
_GREEN_XYZ = (0.38511658, 0.71690369, 0.09706116)
_BLUE_XYZ = (0.14305115, 0.06060791, 0.71392822)
_D50_XYZ = (0.96420288, 1.00000000, 0.82490540)
_D65_XYZ = (0.95045471, 1.00000000, 1.08905029)
_D65_TO_D50_CHAD = (
    1.04788208,
    0.02291870,
    -0.05020142,
    0.02958679,
    0.99047852,
    -0.01705933,
    -0.00924683,
    0.01506042,
    0.75178528,
)

_REC1886_GAMMA = 1.0 / REC1886_GAMMA  # invert the forward encode gamma to get the decode (EOTF) gamma
_LINEAR_GAMMA = 1.0  # 'raw' has no transfer function

_ICC_HEADER_SIZE = 128
_TAG_ENTRY_SIZE = 12
_CURVE_SAMPLE_COUNT = 1024  # resolution of the sampled 'curv' TRC tags

_ENCODING_DESCRIPTIONS = {
    TONE_SRGB.lower(): "sRGB IEC61966-2.1",
    TONE_REC709.lower(): "Rec.709",
    TONE_REC1886.lower(): "Rec.709 (BT.1886)",
    TONE_RAW.lower(): "Linear Rec.709",
}


def _encoding_of(tone: str) -> str:
    encoding = display_encoding(tone)
    if encoding is None:
        msg = f"No display encoding for {tone!r}"
        raise ValueError(msg)
    return encoding


# Every choice and earlier alias maps to a description. ACES choices share their
# base encoding's description because both output Rec.709 primaries.
PROFILE_DESCRIPTIONS = {
    tone.lower(): _ENCODING_DESCRIPTIONS[_encoding_of(tone)] for tone in (*TONE_CHOICES, *TONE_ALIASES)
}


def _s15fixed16(value: float) -> bytes:
    """Encode a float as the ICC s15Fixed16Number type."""
    return struct.pack(">i", round(value * 65536))


def _xyz_type(xyz: tuple[float, float, float]) -> bytes:
    return b"XYZ " + bytes(4) + b"".join(_s15fixed16(c) for c in xyz)


def _sf32_type(values: tuple[float, ...]) -> bytes:
    """Encode a tuple of floats as an ICC s15Fixed16ArrayType ('sf32') tag."""
    return b"sf32" + bytes(4) + b"".join(_s15fixed16(v) for v in values)


def _desc_type(text: str) -> bytes:
    """Encode an ICC v2 'desc' tag (textDescriptionType).

    An ASCII string is followed by fixed-size Unicode and ScriptCode fields.
    These fields are unused here but required by strict parsers.
    """
    ascii_bytes = text.encode("ascii") + b"\0"
    # Unicode language code and count (4 + 4, no string), ScriptCode code and
    # count (2 + 1), then the ScriptCode string's fixed 67 bytes.
    unicode_and_scriptcode = bytes(4 + 4 + 2 + 1 + 67)
    return b"desc" + bytes(4) + struct.pack(">I", len(ascii_bytes)) + ascii_bytes + unicode_and_scriptcode


def _text_type(text: str) -> bytes:
    """Encode an ICC v2 'text' tag (textType), required for 'cprt'.

    The tag contains a signature, four reserved bytes and a null-terminated
    ASCII string. It has no Unicode or ScriptCode fields.
    """
    return b"text" + bytes(4) + text.encode("ascii") + b"\0"


def _gamma_curve(gamma: float) -> bytes:
    """Encode a pure power curve as a 'curv' tag with a single u8Fixed8 value."""
    return b"curv" + bytes(4) + struct.pack(">I", 1) + struct.pack(">H", round(gamma * 256))


def _sampled_curve(decode_exponent: float, gain: float, offset: float, toe_slope: float, linear_cutoff: float) -> bytes:
    """Encode a piecewise curve as an ICC v2 'curv' tag of sampled u16 values.

    The parametric 'para' type was added in ICC v4. A v2.1 profile represents
    a non-power curve with a sampled lookup table. Both sRGB and BT.709 use
    Y = (aX + b)^g for X >= d, else Y = cX.

    The curve decodes device values to linear light, inverting the encoding
    used by `tone_mapping`. Its coefficients come from the encoding constants:
    `g` is the reciprocal exponent, `a` and `b` undo the gain and offset, and
    `c` undoes the toe slope. The breakpoint `d` is in the encoded domain.
    Multiply the linear cutoff by the toe slope to obtain it.
    """
    decode_gain = 1.0 / gain
    decode_offset = offset / gain
    decode_toe_slope = 1.0 / toe_slope
    breakpoint_x = linear_cutoff * toe_slope

    samples = []
    for i in range(_CURVE_SAMPLE_COUNT):
        x = i / (_CURVE_SAMPLE_COUNT - 1)
        y = decode_toe_slope * x if x < breakpoint_x else (decode_gain * x + decode_offset) ** decode_exponent
        samples.append(round(min(max(y, 0.0), 1.0) * 65535))
    return b"curv" + bytes(4) + struct.pack(">I", len(samples)) + b"".join(struct.pack(">H", s) for s in samples)


def _curve_tag(trc: TransferCurve, gamma: float) -> bytes:
    if trc == "srgb":
        # SRGB_GAMMA is the forward exponent 1/2.4, so its reciprocal is the
        # 2.4 the ICC decode curve needs.
        return _sampled_curve(1.0 / SRGB_GAMMA, SRGB_GAIN, SRGB_OFFSET, SRGB_TOE_SLOPE, SRGB_LINEAR_CUTOFF)
    if trc == "rec709":
        return _sampled_curve(1.0 / REC709_GAMMA, REC709_GAIN, REC709_OFFSET, REC709_TOE_SLOPE, REC709_CUTOFF)
    if trc == "gamma":
        return _gamma_curve(gamma)
    msg = f"Unknown transfer curve: {trc!r}"
    raise ValueError(msg)


def _header(total_size: int) -> bytes:
    """Build the 128-byte ICC v2.4 header for a display device profile."""
    header = struct.pack(">I", total_size)
    header += b"none"  # preferred CMM: no preference
    header += struct.pack(">I", 0x02400000)  # version 2.4.0 (ICC.1:2001-04, includes chad)
    header += b"mntr" + b"RGB " + b"XYZ "  # display device, RGB data, XYZ PCS
    header += struct.pack(">HHHHHH", 2024, 1, 1, 0, 0, 0)  # fixed date, for reproducible bytes
    header += b"acsp" + bytes(4) + bytes(4)  # signature, platform, flags
    header += bytes(4) + bytes(4) + bytes(8)  # manufacturer, model, attributes
    header += struct.pack(">I", 0)  # rendering intent: perceptual
    header += b"".join(_s15fixed16(c) for c in _D50_XYZ)
    header += bytes(4)  # creator
    return header + bytes(_ICC_HEADER_SIZE - len(header))


def build_icc_profile(description: str, trc: TransferCurve, gamma: float = 1.0) -> bytes:
    """Build a minimal ICC v2 matrix/TRC profile with Rec.709 primaries.

    Args:
        description: Human-readable profile name, shown by colour-managed apps.
        trc: Which transfer curve to encode, 'srgb', 'rec709' or 'gamma'.
        gamma: Exponent, used only when trc is 'gamma'.

    Returns:
        The complete profile as bytes, ready to pass to Pillow's
        `save(icc_profile=...)`.

    Raises:
        ValueError: If trc is not a supported curve.
    """
    curve = _curve_tag(trc, gamma)
    tags = {
        b"desc": _desc_type(description),
        b"wtpt": _xyz_type(_D65_XYZ),
        b"chad": _sf32_type(_D65_TO_D50_CHAD),
        b"rXYZ": _xyz_type(_RED_XYZ),
        b"gXYZ": _xyz_type(_GREEN_XYZ),
        b"bXYZ": _xyz_type(_BLUE_XYZ),
        b"rTRC": curve,
        b"gTRC": curve,
        b"bTRC": curve,
        b"cprt": _text_type("Public Domain"),
    }
    # Fixed tag order keeps the bytes stable. Readers accept any order.
    order = [b"desc", b"wtpt", b"chad", b"rXYZ", b"gXYZ", b"bXYZ", b"rTRC", b"gTRC", b"bTRC", b"cprt"]

    table_size = 4 + len(order) * _TAG_ENTRY_SIZE
    body_start = _ICC_HEADER_SIZE + table_size

    body = b""
    entries = b""
    offsets: dict[bytes, tuple[int, int]] = {}
    for signature in order:
        data = tags[signature]
        if data not in offsets:
            # The three TRC tags are identical, so they share one copy.
            offsets[data] = (body_start + len(body), len(data))
            padding = -len(data) % 4
            body += data + bytes(padding)
        offset, length = offsets[data]
        entries += signature + struct.pack(">II", offset, length)

    total = body_start + len(body)
    return _header(total) + struct.pack(">I", len(order)) + entries + body


_ENCODING_PROFILES = {
    TONE_SRGB.lower(): build_icc_profile(_ENCODING_DESCRIPTIONS[TONE_SRGB.lower()], "srgb"),
    TONE_REC709.lower(): build_icc_profile(_ENCODING_DESCRIPTIONS[TONE_REC709.lower()], "rec709"),
    TONE_REC1886.lower(): build_icc_profile(_ENCODING_DESCRIPTIONS[TONE_REC1886.lower()], "gamma", _REC1886_GAMMA),
    TONE_RAW.lower(): build_icc_profile(_ENCODING_DESCRIPTIONS[TONE_RAW.lower()], "gamma", _LINEAR_GAMMA),
}


def profile_for_tone_mapping(tone_mapping: str) -> bytes | None:
    """Return the ICC profile for the encoding produced by `apply_tone_mapping`.

    Args:
        tone_mapping: A display transform name (case-insensitive).

    Returns:
        The profile bytes, or None if the name has no profile. Callers write
        an untagged image when None, which viewers read as sRGB.
    """
    encoding = display_encoding(tone_mapping)
    return None if encoding is None else _ENCODING_PROFILES[encoding]
