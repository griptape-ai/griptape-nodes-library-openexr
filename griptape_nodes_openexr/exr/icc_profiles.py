"""ICC profiles describing the display transforms in basic colour mode.

An 8-bit PNG with no embedded profile is read as sRGB by every viewer. That
suits the 'sRGB' transform and mislabels the others, so a file written with
'rec709' cannot be converted correctly by another application.

These profiles let the file say which curve it was written with. Embedding one
does not change a single pixel; it changes what a colour-managed application
does with them. The visible consequence is that rec709 and rec1886 stop looking
different from sRGB on screen, because a managed viewer now converts them to
the same colour. That is the feature working, not a bug.

Pillow cannot build these: ImageCms.createProfile offers only sRGB, LAB and
XYZ, with no way to specify primaries or a transfer curve. They are therefore
assembled here from the ICC v2 spec. This module needs no colour management
library of its own, and deliberately imports neither PIL nor OpenColorIO: a
profile is a bytes blob, and Pillow writes it with `save(icc_profile=...)`
without parsing it.

All our transforms share Rec.709 primaries, so the profiles differ only in
their tone reproduction curve.
"""

from __future__ import annotations

import struct
from typing import Literal

from griptape_nodes_openexr.exr.tone_mapping import (
    REC709_CUTOFF,
    REC709_GAIN,
    REC709_GAMMA,
    REC709_OFFSET,
    REC709_TOE_SLOPE,
    SRGB_GAIN,
    SRGB_GAMMA,
    SRGB_LINEAR_CUTOFF,
    SRGB_OFFSET,
    SRGB_TOE_SLOPE,
    TONE_RAW,
    TONE_REC709,
    TONE_REC1886,
    TONE_SRGB,
)

TransferCurve = Literal["srgb", "rec709", "gamma"]

# Rec.709 primaries chromatically adapted to the D50 white point the ICC
# profile connection space requires. These are the standard values published
# for the sRGB profile, whose primaries Rec.709 shares.
_RED_XYZ = (0.4360, 0.2225, 0.0139)
_GREEN_XYZ = (0.3851, 0.7169, 0.0971)
_BLUE_XYZ = (0.1431, 0.0606, 0.7141)
_D50_XYZ = (0.9642, 1.0000, 0.8249)

_REC1886_GAMMA = 2.4  # BT.1886 display EOTF; the decode a viewer must apply
_LINEAR_GAMMA = 1.0  # 'raw' has no transfer function

_ICC_HEADER_SIZE = 128
_TAG_ENTRY_SIZE = 12
_PARAMETRIC_TYPE_3 = 3  # Y = (aX + b)^g for X >= d, else cX

PROFILE_DESCRIPTIONS = {
    TONE_SRGB.lower(): "sRGB IEC61966-2.1",
    TONE_REC709.lower(): "Rec.709",
    TONE_REC1886.lower(): "Rec.709 (BT.1886)",
    TONE_RAW.lower(): "Linear Rec.709",
}


def _s15fixed16(value: float) -> bytes:
    """Encode a float as the ICC s15Fixed16Number type."""
    return struct.pack(">i", round(value * 65536))


def _xyz_type(xyz: tuple[float, float, float]) -> bytes:
    return b"XYZ " + bytes(4) + b"".join(_s15fixed16(c) for c in xyz)


def _text_type(text: str) -> bytes:
    """Encode an ICC v2 'desc' tag.

    The type carries an ASCII string followed by fixed-size Unicode and
    ScriptCode fields. Those are unused here but must still be present, or
    a strict parser rejects the tag.
    """
    ascii_bytes = text.encode("ascii") + b"\0"
    unicode_and_scriptcode = bytes(12 + 67 + 1 + 2 + 8 + 67)
    return b"desc" + bytes(4) + struct.pack(">I", len(ascii_bytes)) + ascii_bytes + unicode_and_scriptcode


def _gamma_curve(gamma: float) -> bytes:
    """Encode a pure power curve as a 'curv' tag with a single u8Fixed8 value."""
    return b"curv" + bytes(4) + struct.pack(">I", 1) + struct.pack(">H", round(gamma * 256))


def _parametric_curve(
    decode_exponent: float, gain: float, offset: float, toe_slope: float, linear_cutoff: float
) -> bytes:
    """Encode a piecewise curve as a 'para' tag of function type 3.

    Type 3 is Y = (aX + b)^g for X >= d, else Y = cX, which is the shape both
    the sRGB and BT.709 encodes take.

    The ICC curve runs device-to-linear, the inverse of the forward encode in
    `tone_mapping`, so every parameter is derived from the encode constants:
    `g` is the reciprocal of the forward exponent, `a` and `b` undo the gain
    and offset, and `c` undoes the toe slope. `d` is the breakpoint, which ICC
    wants in the encoded domain while `tone_mapping` states it as a linear
    value, so it is the linear cutoff carried through the toe.
    """
    parameters = (
        decode_exponent,
        1.0 / gain,
        offset / gain,
        1.0 / toe_slope,
        linear_cutoff * toe_slope,
    )
    return b"para" + bytes(4) + struct.pack(">HH", _PARAMETRIC_TYPE_3, 0) + b"".join(_s15fixed16(p) for p in parameters)


def _curve_tag(trc: TransferCurve, gamma: float) -> bytes:
    if trc == "srgb":
        # SRGB_GAMMA is the forward exponent 1/2.4, so its reciprocal is the
        # 2.4 the ICC decode curve needs.
        return _parametric_curve(1.0 / SRGB_GAMMA, SRGB_GAIN, SRGB_OFFSET, SRGB_TOE_SLOPE, SRGB_LINEAR_CUTOFF)
    if trc == "rec709":
        return _parametric_curve(1.0 / REC709_GAMMA, REC709_GAIN, REC709_OFFSET, REC709_TOE_SLOPE, REC709_CUTOFF)
    if trc == "gamma":
        return _gamma_curve(gamma)
    msg = f"Unknown transfer curve: {trc!r}"
    raise ValueError(msg)


def _header(total_size: int) -> bytes:
    """Build the 128-byte ICC v2.1 header for a display device profile."""
    header = struct.pack(">I", total_size)
    header += b"none"  # preferred CMM: no preference
    header += struct.pack(">I", 0x02100000)  # version 2.1.0
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
        b"desc": _text_type(description),
        b"wtpt": _xyz_type(_D50_XYZ),
        b"rXYZ": _xyz_type(_RED_XYZ),
        b"gXYZ": _xyz_type(_GREEN_XYZ),
        b"bXYZ": _xyz_type(_BLUE_XYZ),
        b"rTRC": curve,
        b"gTRC": curve,
        b"bTRC": curve,
        b"cprt": _text_type("Public Domain"),
    }
    # Order is not significant to readers, but fixing it keeps the bytes stable.
    order = [b"desc", b"wtpt", b"rXYZ", b"gXYZ", b"bXYZ", b"rTRC", b"gTRC", b"bTRC", b"cprt"]

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


_PROFILES = {
    TONE_SRGB.lower(): build_icc_profile(PROFILE_DESCRIPTIONS[TONE_SRGB.lower()], "srgb"),
    TONE_REC709.lower(): build_icc_profile(PROFILE_DESCRIPTIONS[TONE_REC709.lower()], "rec709"),
    TONE_REC1886.lower(): build_icc_profile(PROFILE_DESCRIPTIONS[TONE_REC1886.lower()], "gamma", _REC1886_GAMMA),
    TONE_RAW.lower(): build_icc_profile(PROFILE_DESCRIPTIONS[TONE_RAW.lower()], "gamma", _LINEAR_GAMMA),
}


def profile_for_tone_mapping(tone_mapping: str) -> bytes | None:
    """ICC profile describing the encoding `apply_tone_mapping` produced.

    Args:
        tone_mapping: A display transform name (case-insensitive).

    Returns:
        The profile bytes, or None if the name has no profile. Callers write
        an untagged image when None, which viewers read as sRGB.
    """
    return _PROFILES.get(tone_mapping.lower())
