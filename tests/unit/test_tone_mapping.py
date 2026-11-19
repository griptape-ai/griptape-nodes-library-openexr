"""Unit tests for exposure and tone mapping utilities."""

from __future__ import annotations

import warnings
from unittest.mock import patch

import numpy as np
import pytest

from griptape_nodes_openexr.exr.colour.tone_mapping import (
    ACES2065_TO_REC709,
    ACESCG_TO_REC709,
    REC709_TO_ACES2065,
    REC709_TO_ACESCG,
    TONE_ACES2065_SRGB,
    TONE_ACESCG_REC709,
    TONE_ACESCG_SRGB,
    TONE_CHOICES,
    TONE_DEFAULT,
    TONE_REC709,
    TONE_REC1886,
    TONE_SRGB,
    apply_exposure,
    apply_tone_mapping,
    canonical_tone_mapping,
    convert_gamut,
    display_encoding,
    gamut_matrix,
    inverse_gamut_matrix,
    to_uint8,
    validate_tone_mapping,
)
from griptape_nodes_openexr.exr.colour.transfer_functions import (
    REC709_CUTOFF,
    SRGB_LINEAR_CUTOFF,
    SRGB_TOE_SLOPE,
    linear_to_rec709,
    linear_to_rec1886,
    linear_to_srgb,
    srgb_to_linear,
    srgb_uint8_to_linear,
)


class TestApplyExposure:
    def test_zero_ev_is_identity(self) -> None:
        arr = np.array([0.5, 1.0, 2.0], dtype=np.float32)
        result = apply_exposure(arr, 0.0)
        np.testing.assert_allclose(result, arr)

    def test_positive_ev_brightens(self) -> None:
        arr = np.array([0.5], dtype=np.float32)
        result = apply_exposure(arr, 1.0)
        np.testing.assert_allclose(result, [1.0], rtol=1e-5)

    def test_negative_ev_darkens(self) -> None:
        arr = np.array([1.0], dtype=np.float32)
        result = apply_exposure(arr, -1.0)
        np.testing.assert_allclose(result, [0.5], rtol=1e-5)

    def test_two_ev_stops(self) -> None:
        arr = np.array([0.25], dtype=np.float32)
        result = apply_exposure(arr, 2.0)
        np.testing.assert_allclose(result, [1.0], rtol=1e-5)

    def test_preserves_shape(self) -> None:
        arr = np.ones((4, 8, 3), dtype=np.float32)
        result = apply_exposure(arr, 1.0)
        assert result.shape == (4, 8, 3)

    def test_output_dtype_float32(self) -> None:
        arr = np.array([0.5], dtype=np.float32)
        result = apply_exposure(arr, 1.0)
        assert result.dtype == np.float32

    def test_zero_ev_returns_float32_input_without_copying(self) -> None:
        arr = np.array([0.5, 1.0, 2.0], dtype=np.float32)
        result = apply_exposure(arr, 0.0)
        assert result is arr

    def test_zero_ev_casts_float64_input_to_float32(self) -> None:
        arr = np.array([0.5, 1.0], dtype=np.float64)
        result = apply_exposure(arr, 0.0)
        assert result.dtype == np.float32
        np.testing.assert_allclose(result, [0.5, 1.0])

    def test_large_positive_ev_overflows_cleanly_without_warnings(self) -> None:
        arr = np.array([np.finfo(np.float32).max], dtype=np.float32)
        with warnings.catch_warnings():
            warnings.simplefilter("error", RuntimeWarning)
            result = apply_exposure(arr, 2.0)
        assert result.dtype == np.float32
        assert np.isposinf(result[0])


class TestToUint8:
    def test_zero_maps_to_0(self) -> None:
        arr = np.zeros((1, 1, 3), dtype=np.float32)
        result = to_uint8(arr)
        assert result[0, 0, 0] == 0

    def test_one_maps_to_255(self) -> None:
        arr = np.ones((1, 1, 3), dtype=np.float32)
        result = to_uint8(arr)
        assert result[0, 0, 0] == 255

    def test_half_maps_near_128(self) -> None:
        arr = np.full((1, 1, 3), 0.5, dtype=np.float32)
        result = to_uint8(arr)
        assert 127 <= result[0, 0, 0] <= 128

    def test_above_one_clamped_to_255(self) -> None:
        arr = np.array([[[2.0, 3.0, 100.0]]], dtype=np.float32)
        result = to_uint8(arr)
        np.testing.assert_array_equal(result[0, 0], [255, 255, 255])

    def test_below_zero_clamped_to_0(self) -> None:
        arr = np.array([[[-1.0, -0.5, -0.1]]], dtype=np.float32)
        result = to_uint8(arr)
        np.testing.assert_array_equal(result[0, 0], [0, 0, 0])

    def test_output_dtype_uint8(self) -> None:
        arr = np.ones((4, 8, 3), dtype=np.float32)
        result = to_uint8(arr)
        assert result.dtype == np.uint8

    def test_preserves_spatial_shape(self) -> None:
        arr = np.random.rand(16, 32, 3).astype(np.float32)
        result = to_uint8(arr)
        assert result.shape == (16, 32, 3)


class TestLinearToSrgb:
    def test_zero_maps_to_zero(self) -> None:
        result = linear_to_srgb(np.array([0.0], dtype=np.float32))
        np.testing.assert_allclose(result, [0.0], atol=1e-6)

    def test_one_maps_to_one(self) -> None:
        result = linear_to_srgb(np.array([1.0], dtype=np.float32))
        np.testing.assert_allclose(result, [1.0], rtol=1e-6)

    def test_toe_breakpoint(self) -> None:
        # The piecewise split must be continuous: SRGB_LINEAR_CUTOFF * 12.92.
        result = linear_to_srgb(np.array([SRGB_LINEAR_CUTOFF], dtype=np.float32))
        assert result[0] == pytest.approx(0.040450, rel=1e-4)

    def test_mid_value(self) -> None:
        result = linear_to_srgb(np.array([0.5], dtype=np.float32))
        assert result[0] == pytest.approx(0.735357, rel=1e-5)

    def test_brightens_midtones(self) -> None:
        # The whole point of the encode: linear 0.18 must land well above 0.18.
        result = linear_to_srgb(np.array([0.18], dtype=np.float32))
        assert result[0] > 0.4

    def test_negative_input_is_zero_not_nan(self) -> None:
        # A negative base with a fractional exponent yields NaN unless clamped first,
        # and this is a public entry point reachable with unclipped scene-linear data.
        result = linear_to_srgb(np.array([-0.5, -1e-8], dtype=np.float32))
        assert not np.any(np.isnan(result))
        np.testing.assert_allclose(result, [0.0, 0.0], atol=1e-6)

    def test_above_one_clamped(self) -> None:
        result = linear_to_srgb(np.array([2.0, 100.0], dtype=np.float32))
        np.testing.assert_allclose(result, [1.0, 1.0], rtol=1e-6)

    def test_monotonically_increasing(self) -> None:
        arr = np.linspace(0.0, 1.0, 500).astype(np.float32)
        result = linear_to_srgb(arr)
        assert np.all(np.diff(result) >= 0)

    def test_output_dtype_float32(self) -> None:
        result = linear_to_srgb(np.array([0.5], dtype=np.float32))
        assert result.dtype == np.float32

    def test_preserves_shape(self) -> None:
        result = linear_to_srgb(np.ones((4, 8, 3), dtype=np.float32))
        assert result.shape == (4, 8, 3)


class TestLinearToRec709:
    """The BT.709 OETF, the curve Nuke's 'rec709' view applies."""

    def test_zero_maps_to_zero(self) -> None:
        result = linear_to_rec709(np.array([0.0], dtype=np.float32))
        np.testing.assert_allclose(result, [0.0], atol=1e-6)

    def test_one_maps_to_one(self) -> None:
        result = linear_to_rec709(np.array([1.0], dtype=np.float32))
        np.testing.assert_allclose(result, [1.0], rtol=1e-6)

    def test_toe_segment_is_linear(self) -> None:
        # Below the cutoff the curve is a straight 4.5x ramp. This is the branch
        # patched in after the power segment, so it needs its own cover.
        result = linear_to_rec709(np.array([0.004, 0.01], dtype=np.float32))
        np.testing.assert_allclose(result, [0.018, 0.045], rtol=1e-5)

    def test_toe_breakpoint(self) -> None:
        # At the cutoff the toe has been left behind and the power segment applies.
        # BT.709 is very slightly discontinuous here (0.0810 toe vs 0.08125 power);
        # the spec defines it this way, so pin the power-segment value.
        result = linear_to_rec709(np.array([REC709_CUTOFF], dtype=np.float32))
        assert result[0] == pytest.approx(0.081248, rel=1e-4)

    def test_mid_value(self) -> None:
        result = linear_to_rec709(np.array([0.5], dtype=np.float32))
        assert result[0] == pytest.approx(0.705515, rel=1e-5)

    def test_middle_grey(self) -> None:
        result = linear_to_rec709(np.array([0.18], dtype=np.float32))
        assert result[0] == pytest.approx(0.409008, rel=1e-5)

    def test_negative_input_is_zero_not_nan(self) -> None:
        result = linear_to_rec709(np.array([-0.5, -1e-8], dtype=np.float32))
        assert not np.any(np.isnan(result))
        np.testing.assert_allclose(result, [0.0, 0.0], atol=1e-6)

    def test_above_one_clamped(self) -> None:
        result = linear_to_rec709(np.array([2.0, 100.0], dtype=np.float32))
        np.testing.assert_allclose(result, [1.0, 1.0], rtol=1e-6)

    def test_monotonically_increasing(self) -> None:
        arr = np.linspace(0.0, 1.0, 500).astype(np.float32)
        result = linear_to_rec709(arr)
        assert np.all(np.diff(result) >= 0)

    def test_output_dtype_float32(self) -> None:
        result = linear_to_rec709(np.array([0.5], dtype=np.float32))
        assert result.dtype == np.float32

    def test_preserves_shape(self) -> None:
        result = linear_to_rec709(np.ones((4, 8, 3), dtype=np.float32))
        assert result.shape == (4, 8, 3)


class TestLinearToRec1886:
    """Zero-black-level BT.1886 display inverse EOTF (gamma 2.4)."""

    def test_zero_maps_to_zero(self) -> None:
        result = linear_to_rec1886(np.array([0.0], dtype=np.float32))
        np.testing.assert_allclose(result, [0.0], atol=1e-6)

    def test_one_maps_to_one(self) -> None:
        result = linear_to_rec1886(np.array([1.0], dtype=np.float32))
        np.testing.assert_allclose(result, [1.0], rtol=1e-6)

    def test_mid_value(self) -> None:
        result = linear_to_rec1886(np.array([0.5], dtype=np.float32))
        assert result[0] == pytest.approx(0.749154, rel=1e-5)

    def test_middle_grey(self) -> None:
        result = linear_to_rec1886(np.array([0.18], dtype=np.float32))
        assert result[0] == pytest.approx(0.489437, rel=1e-5)

    def test_has_no_linear_toe(self) -> None:
        # At zero black level this curve follows x**(1/2.4) down to 0 with no linear toe.
        result = linear_to_rec1886(np.array([0.001], dtype=np.float32))
        assert result[0] == pytest.approx(0.001 ** (1.0 / 2.4), rel=1e-5)

    def test_negative_input_is_zero_not_nan(self) -> None:
        result = linear_to_rec1886(np.array([-0.5, -1e-8], dtype=np.float32))
        assert not np.any(np.isnan(result))
        np.testing.assert_allclose(result, [0.0, 0.0], atol=1e-6)

    def test_above_one_clamped(self) -> None:
        result = linear_to_rec1886(np.array([2.0, 100.0], dtype=np.float32))
        np.testing.assert_allclose(result, [1.0, 1.0], rtol=1e-6)

    def test_monotonically_increasing(self) -> None:
        arr = np.linspace(0.0, 1.0, 500).astype(np.float32)
        result = linear_to_rec1886(arr)
        assert np.all(np.diff(result) >= 0)

    def test_output_dtype_float32(self) -> None:
        result = linear_to_rec1886(np.array([0.5], dtype=np.float32))
        assert result.dtype == np.float32

    def test_preserves_shape(self) -> None:
        result = linear_to_rec1886(np.ones((4, 8, 3), dtype=np.float32))
        assert result.shape == (4, 8, 3)


class TestApplyToneMapping:
    """End-to-end scene-linear to uint8, the values a viewer actually sees.

    These pin the fix for the crushed-shadow bug: apply_tone_mapping must emit
    display-encoded values, not display-linear ones.
    """

    def test_srgb_half(self) -> None:
        arr = np.full((1, 1, 3), 0.5, dtype=np.float32)
        result = to_uint8(apply_tone_mapping(arr, "sRGB"))
        np.testing.assert_array_equal(result[0, 0], [188, 188, 188])

    def test_srgb_clamps_above_one(self) -> None:
        arr = np.full((1, 1, 3), 4.0, dtype=np.float32)
        result = to_uint8(apply_tone_mapping(arr, "sRGB"))
        np.testing.assert_array_equal(result[0, 0], [255, 255, 255])

    def test_srgb_clamps_below_zero(self) -> None:
        arr = np.full((1, 1, 3), -2.0, dtype=np.float32)
        result = to_uint8(apply_tone_mapping(arr, "sRGB"))
        np.testing.assert_array_equal(result[0, 0], [0, 0, 0])

    @pytest.mark.parametrize(
        ("tone", "expected"),
        [
            ("sRGB", 118),
            ("rec709", 104),
            ("rec1886", 125),
            ("raw", 46),
        ],
    )
    def test_middle_grey_per_curve(self, tone: str, expected: int) -> None:
        # Middle grey through every choice, as a viewer sees it.
        arr = np.full((1, 1, 3), 0.18, dtype=np.float32)
        result = to_uint8(apply_tone_mapping(arr, tone))
        np.testing.assert_array_equal(result[0, 0], [expected] * 3)

    @pytest.mark.parametrize(
        ("tone", "expected"),
        [
            ("sRGB", 188),
            ("rec709", 180),
            ("rec1886", 191),
            ("raw", 128),
        ],
    )
    def test_half_per_curve(self, tone: str, expected: int) -> None:
        arr = np.full((1, 1, 3), 0.5, dtype=np.float32)
        result = to_uint8(apply_tone_mapping(arr, tone))
        np.testing.assert_array_equal(result[0, 0], [expected] * 3)

    def test_raw_applies_no_transfer_function(self) -> None:
        # The distinction the old 'linear' name hid: raw is the value itself.
        arr = np.full((1, 1, 3), 0.5, dtype=np.float32)
        np.testing.assert_allclose(apply_tone_mapping(arr, "raw")[0, 0], [0.5] * 3, rtol=1e-6)

    def test_raw_clamps_out_of_range(self) -> None:
        arr = np.array([[[-1.0, 0.25, 4.0]]], dtype=np.float32)
        np.testing.assert_allclose(apply_tone_mapping(arr, "raw")[0, 0], [0.0, 0.25, 1.0], rtol=1e-6)

    def test_curve_ordering_across_midtones(self) -> None:
        # rec709 is darker than sRGB, which is darker than rec1886, because
        # sRGB's effective gamma sits between the two. Easy to invert when
        # editing the constants, so pin the relationship rather than one value.
        arr = np.array([[[0.05, 0.18, 0.5]]], dtype=np.float32)
        rec709 = apply_tone_mapping(arr, "rec709")
        srgb = apply_tone_mapping(arr, "sRGB")
        rec1886 = apply_tone_mapping(arr, "rec1886")
        assert np.all(rec709 < srgb)
        assert np.all(srgb < rec1886)

    def test_srgb_matches_retired_linear_output(self) -> None:
        # 'sRGB' is a rename of what 'linear' always did, not a maths change.
        # 188 is the value the old 'linear' produced at 0.5.
        arr = np.full((1, 1, 3), 0.5, dtype=np.float32)
        np.testing.assert_array_equal(to_uint8(apply_tone_mapping(arr, "sRGB"))[0, 0], [188, 188, 188])

    def test_linear_is_retired_and_names_alternatives(self) -> None:
        # 'linear' used to mean sRGB-encoded. Failing loudly beats silently
        # rendering a saved workflow darker than it was authored.
        arr = np.zeros((1, 1, 3), dtype=np.float32)
        with pytest.raises(ValueError, match="no longer supported") as excinfo:
            apply_tone_mapping(arr, "linear")
        message = str(excinfo.value)
        assert TONE_SRGB in message
        assert "raw" in message

    def test_filmic_is_retired_and_names_alternatives(self) -> None:
        # Dropped to match Nuke's default viewer, which offers only the
        # encodings. Real tone mapping belongs in ocio mode.
        arr = np.zeros((1, 1, 3), dtype=np.float32)
        with pytest.raises(ValueError, match="no longer supported") as excinfo:
            apply_tone_mapping(arr, "filmic")
        message = str(excinfo.value)
        assert TONE_SRGB in message
        assert "ocio" in message

    def test_no_choice_rolls_off_above_one(self) -> None:
        # Every remaining choice is a pure transfer function, so scene values
        # above 1.0 clip. This is Nuke default-viewer behaviour, and is the
        # tradeoff accepted when filmic was dropped.
        arr = np.full((1, 1, 3), 4.0, dtype=np.float32)
        for tone in TONE_CHOICES:
            result = to_uint8(apply_tone_mapping(arr, tone))
            np.testing.assert_array_equal(result[0, 0], [255, 255, 255], err_msg=tone)

    @pytest.mark.parametrize("tone", ["srgb", "SRGB", "sRGB", "sRgB"])
    def test_srgb_name_is_case_insensitive(self, tone: str) -> None:
        # The only mixed-case choice, so dispatch cannot compare it directly.
        arr = np.full((1, 1, 3), 0.18, dtype=np.float32)
        np.testing.assert_array_equal(
            apply_tone_mapping(arr, tone),
            apply_tone_mapping(arr, "sRGB"),
        )

    @pytest.mark.parametrize("tone", TONE_CHOICES)
    def test_every_advertised_choice_dispatches(self, tone: str) -> None:
        # Guards against a name reaching the UI choice list without a branch.
        arr = np.full((1, 1, 3), 0.18, dtype=np.float32)
        assert apply_tone_mapping(arr, tone).shape == (1, 1, 3)

    def test_default_is_srgb(self) -> None:
        assert TONE_DEFAULT == "Linear sRGB to sRGB"
        assert TONE_DEFAULT in TONE_CHOICES

    def test_case_insensitive(self) -> None:
        arr = np.full((1, 1, 3), 0.18, dtype=np.float32)
        np.testing.assert_array_equal(
            apply_tone_mapping(arr, "REC709"),
            apply_tone_mapping(arr, "rec709"),
        )

    def test_unsupported_tone_raises(self) -> None:
        arr = np.zeros((1, 1, 3), dtype=np.float32)
        with pytest.raises(ValueError, match="Unsupported tone mapping"):
            apply_tone_mapping(arr, "reinhard")

    def test_output_dtype_float32(self) -> None:
        arr = np.full((1, 1, 3), 0.18, dtype=np.float32)
        assert apply_tone_mapping(arr, "rec709").dtype == np.float32

    def test_preserves_shape(self) -> None:
        arr = np.ones((4, 8, 3), dtype=np.float32)
        assert apply_tone_mapping(arr, "rec709").shape == (4, 8, 3)


def _xy_to_xyz(x: float, y: float) -> np.ndarray:
    return np.array([x / y, 1.0, (1.0 - x - y) / y])


def _rgb_to_xyz(primaries: list[tuple[float, float]], white: tuple[float, float]) -> np.ndarray:
    columns = np.array([_xy_to_xyz(*p) for p in primaries]).T
    scale = np.linalg.solve(columns, _xy_to_xyz(*white))
    return columns * scale


def _bradford(source_white: tuple[float, float], target_white: tuple[float, float]) -> np.ndarray:
    cone = np.array([[0.8951, 0.2664, -0.1614], [-0.7502, 1.7135, 0.0367], [0.0389, -0.0685, 1.0296]])
    gain = (cone @ _xy_to_xyz(*target_white)) / (cone @ _xy_to_xyz(*source_white))
    return np.linalg.inv(cone) @ np.diag(gain) @ cone


# Primaries and white points from SMPTE ST 2065-1, Academy S-2014-004 and ITU-R BT.709.
_ACES_WHITE = (0.32168, 0.33767)
_D65 = (0.3127, 0.3290)
_AP0 = [(0.7347, 0.2653), (0.0, 1.0), (0.0001, -0.0770)]
_AP1 = [(0.713, 0.293), (0.165, 0.830), (0.128, 0.044)]
_REC709 = [(0.64, 0.33), (0.30, 0.60), (0.15, 0.06)]


def _derived_to_rec709(primaries: list[tuple[float, float]]) -> np.ndarray:
    return (
        np.linalg.inv(_rgb_to_xyz(_REC709, _D65)) @ _bradford(_ACES_WHITE, _D65) @ _rgb_to_xyz(primaries, _ACES_WHITE)
    )


class TestAcesMatrices:
    """The published matrices, checked against a derivation from the primaries."""

    @pytest.mark.parametrize(
        ("matrix", "primaries"),
        [(ACESCG_TO_REC709, _AP1), (ACES2065_TO_REC709, _AP0)],
        ids=["ACEScg", "ACES2065-1"],
    )
    def test_matches_derivation_from_primaries(self, matrix: np.ndarray, primaries: list) -> None:
        np.testing.assert_allclose(matrix, _derived_to_rec709(primaries), rtol=1e-7, atol=1e-7)

    @pytest.mark.parametrize(
        "matrix",
        [ACESCG_TO_REC709, ACES2065_TO_REC709, REC709_TO_ACESCG, REC709_TO_ACES2065],
    )
    def test_matrix_constants_are_readonly_float32(self, matrix: np.ndarray) -> None:
        assert matrix.dtype == np.float32
        assert matrix.flags.writeable is False

    @pytest.mark.parametrize(
        ("forward", "inverse"),
        [(ACESCG_TO_REC709, REC709_TO_ACESCG), (ACES2065_TO_REC709, REC709_TO_ACES2065)],
        ids=["ACEScg", "ACES2065-1"],
    )
    def test_precomputed_inverse_matrices_invert_forward_matrices(
        self, forward: np.ndarray, inverse: np.ndarray
    ) -> None:
        np.testing.assert_allclose(inverse @ forward, np.eye(3, dtype=np.float32), atol=1e-6)

    def test_gamut_matrix_accessors_return_precomputed_constants(self) -> None:
        assert gamut_matrix(TONE_ACESCG_SRGB) is ACESCG_TO_REC709
        assert gamut_matrix(TONE_ACES2065_SRGB) is ACES2065_TO_REC709
        assert gamut_matrix(TONE_SRGB) is None
        assert inverse_gamut_matrix(TONE_ACESCG_SRGB) is REC709_TO_ACESCG
        assert inverse_gamut_matrix(TONE_ACESCG_REC709) is REC709_TO_ACESCG
        assert inverse_gamut_matrix(TONE_ACES2065_SRGB) is REC709_TO_ACES2065
        assert inverse_gamut_matrix(TONE_SRGB) is None
        assert inverse_gamut_matrix("reinhard") is None

    @pytest.mark.parametrize("matrix", [ACESCG_TO_REC709, ACES2065_TO_REC709], ids=["ACEScg", "ACES2065-1"])
    def test_aces_white_maps_to_display_white(self, matrix: np.ndarray) -> None:
        result = convert_gamut(np.ones((1, 1, 3), dtype=np.float32), matrix)
        np.testing.assert_allclose(result[0, 0], [1.0, 1.0, 1.0], atol=1e-6)

    def test_acescg_red_primary(self) -> None:
        # Pure AP1 red lies outside Rec.709: more red than display red can show,
        # with negative green and blue.
        result = convert_gamut(np.array([[[1.0, 0.0, 0.0]]], dtype=np.float32), ACESCG_TO_REC709)
        np.testing.assert_allclose(result[0, 0], [1.70505099, -0.13025642, -0.02400336], rtol=1e-6)

    def test_aces2065_green_primary(self) -> None:
        result = convert_gamut(np.array([[[0.0, 1.0, 0.0]]], dtype=np.float32), ACES2065_TO_REC709)
        np.testing.assert_allclose(result[0, 0], [-1.13413099, 1.37271909, -0.15297534], rtol=1e-6)

    def test_rejects_non_rgb_input(self) -> None:
        with pytest.raises(ValueError, match="3 channels"):
            convert_gamut(np.ones((4, 4), dtype=np.float32), ACESCG_TO_REC709)


class TestAcesDisplayTransforms:
    def test_choices_are_offered(self) -> None:
        assert {TONE_ACESCG_SRGB, TONE_ACESCG_REC709, TONE_ACES2065_SRGB} <= set(TONE_CHOICES)

    def test_linear_srgb_choices_come_first(self) -> None:
        assert TONE_CHOICES[:4] == ["Linear sRGB to sRGB", "Linear sRGB to rec709", "Linear sRGB to rec1886", "raw"]
        assert TONE_DEFAULT == "Linear sRGB to sRGB"

    @pytest.mark.parametrize(
        ("spelling", "canonical"),
        [
            ("sRGB", TONE_SRGB),
            ("SRGB", TONE_SRGB),
            ("rec709", TONE_REC709),
            ("rec1886", TONE_REC1886),
            ("acescg to srgb", TONE_ACESCG_SRGB),
            ("ACESCG TO REC709", TONE_ACESCG_REC709),
            ("aces2065-1 to srgb", TONE_ACES2065_SRGB),
            ("RAW", "raw"),
            ("linear srgb to srgb", TONE_SRGB),
        ],
    )
    def test_earlier_and_mixed_case_names_resolve_to_canonical_choices(self, spelling: str, canonical: str) -> None:
        # Saved workflows and connected string inputs can carry earlier aliases or arbitrary casing.
        assert canonical_tone_mapping(spelling) == canonical
        assert validate_tone_mapping(spelling) == canonical
        arr = np.array([[[0.18, 0.5, 0.9]]], dtype=np.float32)
        np.testing.assert_array_equal(apply_tone_mapping(arr, spelling), apply_tone_mapping(arr, canonical))
        assert display_encoding(spelling) == display_encoding(canonical)

    @pytest.mark.parametrize("tone", ["raw", TONE_ACESCG_SRGB, "filmic", "reinhard", ""])
    def test_other_names_pass_through(self, tone: str) -> None:
        assert canonical_tone_mapping(tone) == tone

    @pytest.mark.parametrize("tone", ["filmic", "linear"])
    def test_validate_tone_mapping_reports_retired_names(self, tone: str) -> None:
        with pytest.raises(ValueError, match="no longer supported"):
            validate_tone_mapping(tone)

    @pytest.mark.parametrize("tone", ["reinhard", ""])
    def test_validate_tone_mapping_rejects_unknown_names(self, tone: str) -> None:
        with pytest.raises(ValueError, match="Unsupported tone mapping"):
            validate_tone_mapping(tone)

    @pytest.mark.parametrize(
        ("tone", "base"),
        [(TONE_ACESCG_SRGB, "sRGB"), (TONE_ACESCG_REC709, "rec709"), (TONE_ACES2065_SRGB, "sRGB")],
    )
    def test_grey_matches_the_base_curve(self, tone: str, base: str) -> None:
        # Grey is neutral in every RGB space here, so only the encode shows.
        arr = np.full((1, 1, 3), 0.18, dtype=np.float32)
        np.testing.assert_array_equal(to_uint8(apply_tone_mapping(arr, tone)), to_uint8(apply_tone_mapping(arr, base)))

    @pytest.mark.parametrize(
        ("tone", "matrix", "encode"),
        [
            (TONE_ACESCG_SRGB, ACESCG_TO_REC709, linear_to_srgb),
            (TONE_ACESCG_REC709, ACESCG_TO_REC709, linear_to_rec709),
            (TONE_ACES2065_SRGB, ACES2065_TO_REC709, linear_to_srgb),
        ],
    )
    def test_converts_gamut_then_encodes(self, tone: str, matrix: np.ndarray, encode) -> None:
        arr = np.array([[[0.5, 0.2, 0.1], [0.05, 0.3, 0.6]]], dtype=np.float32)
        expected = encode(arr @ matrix.T.astype(np.float32))
        np.testing.assert_allclose(apply_tone_mapping(arr, tone), expected, atol=1e-6)

    def test_colour_differs_from_the_rec709_view(self) -> None:
        # Viewing an ACEScg plate as if it were Rec.709 desaturates it; the ACES choice must not.
        arr = np.array([[[0.5, 0.2, 0.1]]], dtype=np.float32)
        aces = to_uint8(apply_tone_mapping(arr, TONE_ACESCG_SRGB))[0, 0].astype(int)
        plain = to_uint8(apply_tone_mapping(arr, "sRGB"))[0, 0].astype(int)
        assert aces[0] - aces[2] > plain[0] - plain[2]

    def test_out_of_gamut_values_clip_to_display_range(self) -> None:
        arr = np.array([[[1.0, 0.0, 0.0], [0.0, 0.0, 4.0]]], dtype=np.float32)
        for tone in (TONE_ACESCG_SRGB, TONE_ACESCG_REC709, TONE_ACES2065_SRGB):
            result = apply_tone_mapping(arr, tone)
            assert result.dtype == np.float32
            assert result.min() >= 0.0
            assert result.max() <= 1.0

    def test_lookup_is_case_insensitive(self) -> None:
        arr = np.full((1, 1, 3), 0.3, dtype=np.float32)
        np.testing.assert_array_equal(
            apply_tone_mapping(arr, "acescg TO SRGB"), apply_tone_mapping(arr, TONE_ACESCG_SRGB)
        )

    @pytest.mark.parametrize(
        ("tone", "expected"),
        [
            (TONE_ACESCG_SRGB, TONE_SRGB.lower()),
            (TONE_ACESCG_REC709, TONE_REC709.lower()),
            (TONE_ACES2065_SRGB, TONE_SRGB.lower()),
            (TONE_REC1886, TONE_REC1886.lower()),
            ("RAW", "raw"),
            ("reinhard", None),
        ],
    )
    def test_display_encoding(self, tone: str, expected: str | None) -> None:
        assert display_encoding(tone) == expected

    def test_every_choice_has_a_display_encoding(self) -> None:
        for tone in TONE_CHOICES:
            assert display_encoding(tone) is not None


class TestNonFinitePixels:
    """Half-float EXR highlights can overflow to +inf, and damaged channels can hold NaN."""

    @pytest.mark.parametrize("tone", TONE_CHOICES)
    def test_positive_infinity_highlights_render_white_without_warnings(self, tone: str) -> None:
        # Given an achromatic +inf highlight and a yellow [inf, inf, 0] highlight
        arr = np.array([[[np.inf, np.inf, np.inf], [np.inf, np.inf, 0.0]]], dtype=np.float32)

        # When tone mapping and quantizing with RuntimeWarnings treated as errors
        with warnings.catch_warnings():
            warnings.simplefilter("error", RuntimeWarning)
            mapped = apply_tone_mapping(arr, tone)
            quantized = to_uint8(mapped)

        # Then both pixels stay finite and clip to white and yellow
        assert np.isfinite(mapped).all()
        np.testing.assert_array_equal(quantized[0, 0], [255, 255, 255])
        np.testing.assert_array_equal(quantized[0, 1], [255, 255, 0])

    @pytest.mark.parametrize("tone", TONE_CHOICES)
    def test_negative_infinity_clamps_to_black_without_warnings(self, tone: str) -> None:
        arr = np.array([[[-np.inf, -np.inf, -np.inf]]], dtype=np.float32)
        with warnings.catch_warnings():
            warnings.simplefilter("error", RuntimeWarning)
            quantized = to_uint8(apply_tone_mapping(arr, tone))
        np.testing.assert_array_equal(quantized[0, 0], [0, 0, 0])

    @pytest.mark.parametrize("tone", TONE_CHOICES)
    def test_single_channel_nan_zeros_broken_channel_and_preserves_others(self, tone: str) -> None:
        # Given a pixel whose red channel is NaN while green and blue are 0.5
        arr = np.array([[[np.nan, 0.5, 0.5]]], dtype=np.float32)

        # When tone mapping and quantizing with RuntimeWarnings treated as errors
        with warnings.catch_warnings():
            warnings.simplefilter("error", RuntimeWarning)
            mapped = apply_tone_mapping(arr, tone)
            quantized = to_uint8(mapped)

        # Then output stays finite in [0, 1], red maps to 0, and green/blue stay lit
        assert np.isfinite(mapped).all()
        assert mapped.min() >= 0.0
        assert mapped.max() <= 1.0
        assert quantized[0, 0, 0] == 0
        assert quantized[0, 0, 1] > 0
        assert quantized[0, 0, 2] > 0

    @pytest.mark.parametrize("matrix", [ACESCG_TO_REC709, ACES2065_TO_REC709], ids=["ACEScg", "ACES2065-1"])
    def test_float32_max_does_not_overflow_gamut_conversion_to_nan(self, matrix: np.ndarray) -> None:
        fmax = np.finfo(np.float32).max
        arr = np.array([[[fmax, fmax, fmax], [fmax, fmax, 0.0]]], dtype=np.float32)
        with warnings.catch_warnings():
            warnings.simplefilter("error", RuntimeWarning)
            converted = convert_gamut(arr, matrix)
        assert np.isfinite(converted).all()
        assert (converted[0, 0] > 1.0).all()
        assert converted[0, 1, 0] > 1.0
        assert converted[0, 1, 1] > 1.0

    def test_to_uint8_handles_non_finite_values_without_warnings(self) -> None:
        arr = np.array([np.nan, -np.inf, np.inf], dtype=np.float32)
        with warnings.catch_warnings():
            warnings.simplefilter("error", RuntimeWarning)
            result = to_uint8(arr)
        np.testing.assert_array_equal(result, [0, 0, 255])


class TestSrgbUint8ToLinear:
    def test_matches_float_srgb_to_linear_across_all_256_levels(self) -> None:
        levels = np.arange(256, dtype=np.uint8)
        expected = srgb_to_linear(levels.astype(np.float32) / np.float32(255.0))
        actual = srgb_uint8_to_linear(levels)
        assert actual.dtype == np.float32
        np.testing.assert_array_equal(actual, expected)

    def test_preserves_image_shape(self) -> None:
        img = np.array([[[0, 128, 255], [10, 60, 200]]], dtype=np.uint8)
        out = srgb_uint8_to_linear(img)
        assert out.shape == (1, 2, 3)
        assert out.dtype == np.float32

    def test_rejects_non_uint8_input(self) -> None:
        with pytest.raises(ValueError, match="uint8"):
            srgb_uint8_to_linear(np.array([0.5], dtype=np.float32))


class TestToePatchRegression:
    @pytest.mark.parametrize("curve", [linear_to_srgb, linear_to_rec709, srgb_to_linear])
    @pytest.mark.parametrize("toe_count", [1, 3, 4, 5, 64])
    def test_preserves_float32_results_and_input(self, curve, toe_count: int) -> None:
        # Given toe values on both sides of the sparse/dense threshold, including non-finite input.
        cutoff = np.float32(
            REC709_CUTOFF
            if curve is linear_to_rec709
            else SRGB_LINEAR_CUTOFF
            if curve is linear_to_srgb
            else SRGB_LINEAR_CUTOFF * SRGB_TOE_SLOPE
        )
        toe_values = np.array([np.nan, -np.inf, -1.0, 0.0, 1e-40, 0.001], dtype=np.float32)
        source = np.full(64, 0.5, dtype=np.float32)
        source[:toe_count] = np.resize(toe_values, toe_count)
        if toe_count < 64:
            source[-1] = np.inf
        original = source.copy()
        with np.errstate(invalid="ignore", over="ignore"):
            toe = ~(source >= cutoff) if curve is linear_to_rec709 else ~(source > cutoff)
            expected = np.fmin(np.fmax(source, cutoff), np.float32(1.0))
            if curve is srgb_to_linear:
                expected += np.float32(0.055)
                expected /= np.float32(1.055)
                np.power(expected, np.float32(2.4), out=expected)
                toe_result = np.fmax(source, np.float32(0.0)) / np.float32(12.92)
            else:
                gamma, gain, offset, slope = (
                    (0.45, 1.099, 0.099, 4.5) if curve is linear_to_rec709 else (1.0 / 2.4, 1.055, 0.055, 12.92)
                )
                np.power(expected, np.float32(gamma), out=expected)
                expected *= np.float32(gain)
                expected -= np.float32(offset)
                toe_result = np.fmax(source, np.float32(0.0)) * np.float32(slope)
            expected[toe] = toe_result[toe]

        # When the transfer function runs.
        with warnings.catch_warnings():
            warnings.simplefilter("error", RuntimeWarning)
            actual = curve(source)

        # Then the result is bit-exact float32 and the source is unchanged.
        assert actual.dtype == np.float32
        assert actual.shape == source.shape
        np.testing.assert_array_equal(actual, expected)
        np.testing.assert_array_equal(source, original)

    @pytest.mark.parametrize("curve", [linear_to_srgb, linear_to_rec709, srgb_to_linear])
    def test_curves_share_the_toe_patch(self, curve) -> None:
        from griptape_nodes_openexr.exr.colour import transfer_functions

        with patch.object(
            transfer_functions, "_patch_linear_toe", wraps=transfer_functions._patch_linear_toe
        ) as helper:
            curve(np.array([0.0, 0.5], dtype=np.float32))
        helper.assert_called_once()


class TestPiecewiseTransferPerformance:
    """Piecewise curves avoid eager full-frame toe allocations and subnormal powf inputs."""

    @pytest.mark.parametrize("curve", [linear_to_srgb, linear_to_rec709, srgb_to_linear])
    def test_skips_toe_patch_when_all_values_lie_above_cutoff(self, curve) -> None:
        arr = np.full((16, 16, 3), 0.5, dtype=np.float32)
        with (
            patch(
                "griptape_nodes_openexr.exr.colour.transfer_functions.np.copyto",
                side_effect=AssertionError("no copyto"),
            ),
            patch(
                "griptape_nodes_openexr.exr.colour.transfer_functions.np.where", side_effect=AssertionError("no where")
            ),
        ):
            out = curve(arr)
        assert out.shape == arr.shape
        assert out.dtype == np.float32

    @pytest.mark.parametrize(
        ("curve", "cutoff"),
        [(linear_to_srgb, SRGB_LINEAR_CUTOFF), (linear_to_rec709, REC709_CUTOFF)],
    )
    def test_power_segment_never_receives_zero_or_subnormals(self, curve, cutoff: float) -> None:
        arr = np.array([-1.0, 0.0, 1e-40, 0.5, 2.0], dtype=np.float32)
        real_power = np.power
        seen_mins: list[float] = []

        def _spy_power(base: np.ndarray, *args, **kwargs) -> np.ndarray:
            seen_mins.append(float(np.min(base)))
            return real_power(base, *args, **kwargs)

        with patch("griptape_nodes_openexr.exr.colour.transfer_functions.np.power", side_effect=_spy_power):
            curve(arr)

        assert seen_mins
        assert seen_mins[0] >= np.float32(cutoff)

    @pytest.mark.parametrize("toe_count", [0, 1, 3, 4, 5, 16, 64])
    @pytest.mark.parametrize("curve", [linear_to_srgb, linear_to_rec709, srgb_to_linear])
    def test_sparse_and_dense_toe_paths_agree_elementwise(self, curve, toe_count: int) -> None:
        arr = np.full((64,), 0.5, dtype=np.float32)
        arr[:toe_count] = np.float32(0.001)
        batch_out = curve(arr)
        scalar_out = np.array([float(curve(np.array(v, dtype=np.float32))) for v in arr], dtype=np.float32)
        np.testing.assert_array_equal(batch_out, scalar_out)
