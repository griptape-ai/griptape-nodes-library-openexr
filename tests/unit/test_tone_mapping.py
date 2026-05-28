"""Unit tests for exposure and tone mapping utilities."""

from __future__ import annotations

import numpy as np
import pytest

from griptape_nodes_openexr.exr.tone_mapping import apply_exposure, apply_filmic, to_uint8_srgb


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


class TestApplyFilmic:
    def test_zero_maps_to_zero(self) -> None:
        arr = np.array([0.0], dtype=np.float32)
        result = apply_filmic(arr)
        np.testing.assert_allclose(result, [0.0], atol=1e-6)

    def test_output_range_0_to_1(self) -> None:
        arr = np.linspace(0.0, 100.0, 1000).astype(np.float32)
        result = apply_filmic(arr)
        assert result.min() >= 0.0
        assert result.max() <= 1.0

    def test_monotonically_increasing(self) -> None:
        arr = np.linspace(0.0, 10.0, 500).astype(np.float32)
        result = apply_filmic(arr)
        assert np.all(np.diff(result) >= 0)

    def test_mid_gray_compressed(self) -> None:
        # 0.18 (photographic middle grey) should map below 0.5 (compressed highlights)
        arr = np.array([0.18], dtype=np.float32)
        result = apply_filmic(arr)
        assert result[0] < 0.5

    def test_high_value_saturates_to_one(self) -> None:
        # The filmic curve asymptote exceeds 1.0; np.clip brings it to exactly 1.0
        arr = np.array([100.0], dtype=np.float32)
        result = apply_filmic(arr)
        assert result[0] == pytest.approx(1.0)

    def test_preserves_shape(self) -> None:
        arr = np.ones((4, 8, 3), dtype=np.float32)
        result = apply_filmic(arr)
        assert result.shape == (4, 8, 3)

    def test_output_dtype_float32(self) -> None:
        arr = np.array([0.5], dtype=np.float32)
        result = apply_filmic(arr)
        assert result.dtype == np.float32


class TestToUint8Srgb:
    def test_zero_maps_to_0(self) -> None:
        arr = np.zeros((1, 1, 3), dtype=np.float32)
        result = to_uint8_srgb(arr)
        assert result[0, 0, 0] == 0

    def test_one_maps_to_255(self) -> None:
        arr = np.ones((1, 1, 3), dtype=np.float32)
        result = to_uint8_srgb(arr)
        assert result[0, 0, 0] == 255

    def test_half_maps_near_128(self) -> None:
        arr = np.full((1, 1, 3), 0.5, dtype=np.float32)
        result = to_uint8_srgb(arr)
        assert 127 <= result[0, 0, 0] <= 128

    def test_above_one_clamped_to_255(self) -> None:
        arr = np.array([[[2.0, 3.0, 100.0]]], dtype=np.float32)
        result = to_uint8_srgb(arr)
        np.testing.assert_array_equal(result[0, 0], [255, 255, 255])

    def test_below_zero_clamped_to_0(self) -> None:
        arr = np.array([[[-1.0, -0.5, -0.1]]], dtype=np.float32)
        result = to_uint8_srgb(arr)
        np.testing.assert_array_equal(result[0, 0], [0, 0, 0])

    def test_output_dtype_uint8(self) -> None:
        arr = np.ones((4, 8, 3), dtype=np.float32)
        result = to_uint8_srgb(arr)
        assert result.dtype == np.uint8

    def test_preserves_spatial_shape(self) -> None:
        arr = np.random.rand(16, 32, 3).astype(np.float32)
        result = to_uint8_srgb(arr)
        assert result.shape == (16, 32, 3)
