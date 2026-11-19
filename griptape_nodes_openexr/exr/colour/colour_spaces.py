"""Derive RGB conversion matrices from published chromaticities and white points.

Matrices act on column vectors. Image arrays apply their transpose on the last axis.
Matrices are calculated in float64 and stored as read-only float32 arrays for pixel conversion.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

Chromaticity = tuple[float, float]
Primaries = tuple[Chromaticity, Chromaticity, Chromaticity]


@dataclass(frozen=True)
class RGBColourSpace:
    """CIE 1931 xy chromaticities for red, green, blue and the reference white."""

    primaries: Primaries
    white: Chromaticity


# Source attribution and licence text: THIRD_PARTY_NOTICES.md and licenses/.
# ACES coordinates are reproduced from the ACES documentation at revision
# ef03499b58a349de4d09c4c739742dab4ca61122, Copyright Contributors to the ACES Documentation,
# licensed CC-BY-4.0. Numeric coordinates are unchanged; matrices are calculated below.
# ACES2065-1 (AP0): SMPTE ST 2065-1, colour-space chromaticities.
# https://docs.acescentral.com/encodings/aces2065-1/ (Chromaticities table)
# ACEScg (AP1): Academy S-2014-004, colour-space chromaticities.
# https://docs.acescentral.com/encodings/acescg/ (Chromaticities table)
# Both use the ACES white point, which is close to D60.
ACES_WHITE: Chromaticity = (0.32168, 0.33767)
ACES2065 = RGBColourSpace(((0.7347, 0.2653), (0.0, 1.0), (0.0001, -0.0770)), ACES_WHITE)
ACESCG = RGBColourSpace(((0.713, 0.293), (0.165, 0.830), (0.128, 0.044)), ACES_WHITE)

# ITU-R BT.709-6 Table 1, sections 1.3 (primaries) and 1.4 (reference white).
# https://www.itu.int/rec/R-REC-BT.709-6-201506-I/en
# sRGB uses these same primaries and D65 white point (IEC 61966-2-1).
D65: Chromaticity = (0.3127, 0.3290)
REC709 = RGBColourSpace(((0.640, 0.330), (0.300, 0.600), (0.150, 0.060)), D65)

# Bradford XYZ-to-cone-response matrix: ICC v4 specification, section E1.2.
# https://www.color.org/chadtag.xalter reproduces the coefficients and adaptation steps.
# Basic ACES conversions use Bradford adaptation.
_BRADFORD = np.array(
    [[0.8951, 0.2664, -0.1614], [-0.7502, 1.7135, 0.0367], [0.0389, -0.0685, 1.0296]],
    dtype=np.float64,
)
_BRADFORD.flags.writeable = False


def xy_to_xyz(chromaticity: Chromaticity) -> np.ndarray:
    """Convert CIE xy to XYZ with unit luminance Y."""
    x, y = chromaticity
    return np.array([x / y, 1.0, (1.0 - x - y) / y], dtype=np.float64)


def rgb_to_xyz(space: RGBColourSpace) -> np.ndarray:
    """Build an RGB-to-XYZ matrix that maps equal RGB signals to the reference white.

    Scale the primary XYZ columns so their sum is the white point at unit luminance.
    AP0's blue primary has a negative y coordinate; retain it in the calculation.
    """
    columns = np.column_stack([xy_to_xyz(primary) for primary in space.primaries])
    scale = np.linalg.solve(columns, xy_to_xyz(space.white))
    return columns * scale


def bradford_adaptation(source_white: Chromaticity, destination_white: Chromaticity) -> np.ndarray:
    """Map source white to destination white by scaling Bradford cone responses."""
    source_cones = _BRADFORD @ xy_to_xyz(source_white)
    destination_cones = _BRADFORD @ xy_to_xyz(destination_white)
    scale = np.diag(destination_cones / source_cones)
    return np.linalg.solve(_BRADFORD, scale @ _BRADFORD)


def rgb_to_rgb(source: RGBColourSpace, destination: RGBColourSpace) -> np.ndarray:
    """Compose source RGB-to-XYZ, white adaptation and destination XYZ-to-RGB.

    Returns a float64 matrix for linear-light values. Encoding and clipping occur later.
    """
    adaptation = bradford_adaptation(source.white, destination.white)
    return np.linalg.solve(rgb_to_xyz(destination), adaptation @ rgb_to_xyz(source))


def _readonly_matrix_pair(source: RGBColourSpace, destination: RGBColourSpace) -> tuple[np.ndarray, np.ndarray]:
    """Derive the forward matrix and its inverse in float64, then store float32 copies."""
    derived = rgb_to_rgb(source, destination)
    forward = derived.astype(np.float32)
    inverse = np.linalg.inv(derived).astype(np.float32)
    forward.flags.writeable = False
    inverse.flags.writeable = False
    return forward, inverse


# Computed once at import. Equal ACES RGB signals map to equal Rec.709 RGB signals,
# adapting ACES white to the destination D65 white point.
ACESCG_TO_REC709, REC709_TO_ACESCG = _readonly_matrix_pair(ACESCG, REC709)
ACES2065_TO_REC709, REC709_TO_ACES2065 = _readonly_matrix_pair(ACES2065, REC709)
