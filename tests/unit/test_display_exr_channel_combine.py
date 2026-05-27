"""Unit tests for _build_rgb and alpha compositing helpers in display_exr_channel."""

from __future__ import annotations

import numpy as np
import pytest

from griptape_nodes_openexr.nodes.display_exr_channel import _build_rgb, _compose_alpha

H, W = 4, 6


def _plane(value: float) -> np.ndarray:
    return np.full((H, W), value, dtype=np.float32)


# ---------------------------------------------------------------------------
# _build_rgb — single channel → grayscale
# ---------------------------------------------------------------------------


class TestBuildRgbSingleChannel:
    def test_r_slot_produces_grayscale(self) -> None:
        rgb = _build_rgb({"R": _plane(0.5)}, H, W)
        assert rgb.shape == (H, W, 3)
        np.testing.assert_array_equal(rgb[..., 0], rgb[..., 1])
        np.testing.assert_array_equal(rgb[..., 1], rgb[..., 2])

    def test_g_slot_produces_grayscale(self) -> None:
        rgb = _build_rgb({"G": _plane(0.3)}, H, W)
        assert rgb.shape == (H, W, 3)
        np.testing.assert_array_equal(rgb[..., 0], rgb[..., 1])

    def test_b_slot_produces_grayscale(self) -> None:
        rgb = _build_rgb({"B": _plane(0.8)}, H, W)
        assert rgb.shape == (H, W, 3)
        np.testing.assert_array_equal(rgb[..., 0], rgb[..., 2])

    def test_grayscale_values_match_input(self) -> None:
        rgb = _build_rgb({"R": _plane(0.7)}, H, W)
        np.testing.assert_array_almost_equal(rgb[..., 0], 0.7)


# ---------------------------------------------------------------------------
# _build_rgb — two channels → zero-fill missing slot
# ---------------------------------------------------------------------------


class TestBuildRgbTwoChannels:
    def test_rg_present_b_is_zero(self) -> None:
        rgb = _build_rgb({"R": _plane(1.0), "G": _plane(0.5)}, H, W)
        assert rgb.shape == (H, W, 3)
        np.testing.assert_array_equal(rgb[..., 2], np.zeros((H, W)))

    def test_rb_present_g_is_zero(self) -> None:
        rgb = _build_rgb({"R": _plane(1.0), "B": _plane(0.5)}, H, W)
        np.testing.assert_array_equal(rgb[..., 1], np.zeros((H, W)))

    def test_gb_present_r_is_zero(self) -> None:
        rgb = _build_rgb({"G": _plane(0.4), "B": _plane(0.6)}, H, W)
        np.testing.assert_array_equal(rgb[..., 0], np.zeros((H, W)))

    def test_channel_values_placed_correctly(self) -> None:
        rgb = _build_rgb({"R": _plane(0.2), "G": _plane(0.4)}, H, W)
        np.testing.assert_array_almost_equal(rgb[..., 0], 0.2)
        np.testing.assert_array_almost_equal(rgb[..., 1], 0.4)


# ---------------------------------------------------------------------------
# _build_rgb — three channels → full colour
# ---------------------------------------------------------------------------


class TestBuildRgbThreeChannels:
    def test_shape(self) -> None:
        rgb = _build_rgb({"R": _plane(0.1), "G": _plane(0.5), "B": _plane(0.9)}, H, W)
        assert rgb.shape == (H, W, 3)

    def test_planes_match_inputs(self) -> None:
        rgb = _build_rgb({"R": _plane(0.1), "G": _plane(0.5), "B": _plane(0.9)}, H, W)
        np.testing.assert_array_almost_equal(rgb[..., 0], 0.1)
        np.testing.assert_array_almost_equal(rgb[..., 1], 0.5)
        np.testing.assert_array_almost_equal(rgb[..., 2], 0.9)

    def test_dtype_float32(self) -> None:
        rgb = _build_rgb({"R": _plane(0.0), "G": _plane(0.0), "B": _plane(0.0)}, H, W)
        assert rgb.dtype == np.float32


# ---------------------------------------------------------------------------
# _compose_alpha — adding an alpha plane to uint8 RGB
# ---------------------------------------------------------------------------


class TestComposeAlpha:
    def _uint8_rgb(self) -> np.ndarray:
        return np.zeros((H, W, 3), dtype=np.uint8)

    def test_no_alpha_returns_rgb(self) -> None:
        rgb = self._uint8_rgb()
        result = _compose_alpha(rgb, alpha_plane=None)
        assert result.shape == (H, W, 3)

    def test_alpha_returns_rgba(self) -> None:
        rgb = self._uint8_rgb()
        alpha = _plane(1.0)
        result = _compose_alpha(rgb, alpha_plane=alpha)
        assert result.shape == (H, W, 4)

    def test_alpha_values_clamped_and_scaled(self) -> None:
        rgb = self._uint8_rgb()
        alpha = _plane(0.5)
        result = _compose_alpha(rgb, alpha_plane=alpha)
        # 0.5 * 255 + 0.5 rounds to 128
        assert result[0, 0, 3] == pytest.approx(128, abs=1)

    def test_alpha_over_one_clamped_to_255(self) -> None:
        rgb = self._uint8_rgb()
        alpha = _plane(2.0)
        result = _compose_alpha(rgb, alpha_plane=alpha)
        assert result[0, 0, 3] == 255

    def test_alpha_below_zero_clamped_to_0(self) -> None:
        rgb = self._uint8_rgb()
        alpha = _plane(-1.0)
        result = _compose_alpha(rgb, alpha_plane=alpha)
        assert result[0, 0, 3] == 0

    def test_result_dtype_uint8(self) -> None:
        rgb = self._uint8_rgb()
        alpha = _plane(1.0)
        result = _compose_alpha(rgb, alpha_plane=alpha)
        assert result.dtype == np.uint8
