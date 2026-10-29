"""Unit tests for exposure and tone mapping utilities."""

from __future__ import annotations

import numpy as np
import pytest

from griptape_nodes_openexr.exr.tone_mapping import (
    REC709_CUTOFF,
    SRGB_LINEAR_CUTOFF,
    TONE_CHOICES,
    TONE_DEFAULT,
    apply_exposure,
    apply_tone_mapping,
    linear_to_rec709,
    linear_to_rec1886,
    linear_to_srgb,
    to_uint8,
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
    """BT.1886, a pure 2.4 power function. Nuke calls this view 'rec1886'."""

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
        # Unlike sRGB and BT.709 this is a pure power curve all the way down.
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
        assert "sRGB" in message
        assert "raw" in message

    def test_filmic_is_retired_and_names_alternatives(self) -> None:
        # Dropped to match Nuke's default viewer, which offers only the
        # encodings. Real tone mapping belongs in ocio mode.
        arr = np.zeros((1, 1, 3), dtype=np.float32)
        with pytest.raises(ValueError, match="no longer supported") as excinfo:
            apply_tone_mapping(arr, "filmic")
        message = str(excinfo.value)
        assert "sRGB" in message
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
        assert TONE_DEFAULT == "sRGB"
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
