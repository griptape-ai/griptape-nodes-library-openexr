"""Source-data, mathematical-invariant and regression checks for colour conversion."""

from __future__ import annotations

import numpy as np
import pytest

from griptape_nodes_openexr.exr.colour.colour_spaces import (
    ACES2065,
    ACES2065_TO_REC709,
    ACES_WHITE,
    ACESCG,
    ACESCG_TO_REC709,
    D65,
    REC709,
    REC709_TO_ACES2065,
    REC709_TO_ACESCG,
    bradford_adaptation,
    rgb_to_rgb,
    rgb_to_xyz,
    xy_to_xyz,
)


@pytest.mark.parametrize(
    ("space", "primaries", "white"),
    [
        (ACES2065, ((0.7347, 0.2653), (0.0, 1.0), (0.0001, -0.0770)), (0.32168, 0.33767)),
        (ACESCG, ((0.713, 0.293), (0.165, 0.830), (0.128, 0.044)), (0.32168, 0.33767)),
        (REC709, ((0.64, 0.33), (0.30, 0.60), (0.15, 0.06)), (0.3127, 0.3290)),
    ],
)
def test_published_colour_space_inputs(space, primaries, white) -> None:
    assert space.primaries == primaries
    assert space.white == white


def test_d65_xyz_with_unit_luminance() -> None:
    np.testing.assert_allclose(xy_to_xyz(D65), [0.9504559271, 1.0, 1.0890577508], atol=1e-10)


def test_rec709_rgb_to_xyz_matches_reference() -> None:
    # Reference normalised primary matrix for BT.709 Table 1 xy values, rounded
    # to ten decimal places. White-mapping and identity checks also constrain the construction.
    expected = [
        [0.4123907993, 0.3575843394, 0.1804807884],
        [0.2126390059, 0.7151686788, 0.0721923154],
        [0.0193308187, 0.1191947798, 0.9505321522],
    ]
    matrix = rgb_to_xyz(REC709)
    assert matrix.dtype == np.float64
    np.testing.assert_allclose(matrix, expected, atol=1e-10)
    np.testing.assert_allclose(matrix @ np.ones(3), xy_to_xyz(D65), atol=1e-14)


def test_bradford_maps_source_white_to_destination_white() -> None:
    adaptation = bradford_adaptation(ACES_WHITE, D65)
    assert adaptation.dtype == np.float64
    np.testing.assert_allclose(adaptation @ xy_to_xyz(ACES_WHITE), xy_to_xyz(D65), atol=1e-14)
    np.testing.assert_allclose(bradford_adaptation(D65, D65), np.eye(3), atol=1e-14)


@pytest.mark.parametrize(
    ("space", "forward", "inverse", "reference"),
    [
        (
            ACESCG,
            ACESCG_TO_REC709,
            REC709_TO_ACESCG,
            [
                [1.70505099, -0.62179212, -0.08325887],
                [-0.13025642, 1.14080474, -0.01054832],
                [-0.02400336, -0.12896898, 1.15297233],
            ],
        ),
        (
            ACES2065,
            ACES2065_TO_REC709,
            REC709_TO_ACES2065,
            [
                [2.52168619, -1.13413099, -0.38755520],
                [-0.27647991, 1.37271909, -0.09623917],
                [-0.01537806, -0.15297534, 1.16835340],
            ],
        ),
    ],
)
def test_derived_matrices_match_previous_coefficients(space, forward, inverse, reference) -> None:
    # Rounded coefficients from exr/tone_mapping.py at abfd4f4. These constrain
    # compatibility; the published-input and white-mapping tests check the construction.
    derived = rgb_to_rgb(space, REC709)
    assert derived.dtype == np.float64
    np.testing.assert_allclose(derived, reference, atol=1e-8, rtol=0)
    np.testing.assert_array_equal(forward, derived.astype(np.float32))
    np.testing.assert_array_equal(inverse, np.linalg.inv(derived).astype(np.float32))
    np.testing.assert_allclose(forward @ np.ones(3), np.ones(3), atol=2e-7)
    np.testing.assert_allclose(inverse @ forward, np.eye(3), atol=2e-7)
    for matrix in (forward, inverse):
        assert matrix.dtype == np.float32
        assert matrix.flags.writeable is False
        with pytest.raises(ValueError, match="read-only"):
            matrix[0, 0] = 0.0


@pytest.mark.parametrize("space", [ACESCG, ACES2065, REC709])
def test_same_colour_space_conversion_is_identity(space) -> None:
    np.testing.assert_allclose(rgb_to_rgb(space, space), np.eye(3), atol=1e-14)
