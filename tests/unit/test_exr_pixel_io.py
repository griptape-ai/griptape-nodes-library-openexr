"""Unit tests for exr_pixel_io — no real EXR files required."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import numpy as np
import pytest
from PIL import Image

from griptape_nodes_openexr.exr.exr_pixel_io import (
    _chromaticities_to_colorspace,
    apply_exposure,
    apply_gamma,
    normalize_pixels,
    to_pil_gray,
    to_pil_rgb,
    tone_map,
)


# ---------------------------------------------------------------------------
# Chromaticity label mapping
# ---------------------------------------------------------------------------


class FakeChromaticities:
    def __init__(self, rx, ry, gx, gy, bx, by):
        self.red_x = rx
        self.red_y = ry
        self.green_x = gx
        self.green_y = gy
        self.blue_x = bx
        self.blue_y = by


def test_chromaticities_rec709_returns_lin_srgb():
    chroma = FakeChromaticities(0.640, 0.330, 0.300, 0.600, 0.150, 0.060)
    assert _chromaticities_to_colorspace(chroma) == "lin_srgb"


def test_chromaticities_ap1_returns_acescg():
    chroma = FakeChromaticities(0.680, 0.320, 0.265, 0.690, 0.150, 0.060)
    assert _chromaticities_to_colorspace(chroma) == "ACEScg"


def test_chromaticities_unknown_returns_none():
    chroma = FakeChromaticities(0.100, 0.200, 0.300, 0.400, 0.500, 0.600)
    assert _chromaticities_to_colorspace(chroma) is None


def test_chromaticities_none_returns_none():
    assert _chromaticities_to_colorspace(None) is None


def test_chromaticities_within_tolerance():
    # Slightly off Rec.709 primaries but within _PRIMARY_TOLERANCE (0.01)
    chroma = FakeChromaticities(0.641, 0.331, 0.301, 0.601, 0.151, 0.061)
    assert _chromaticities_to_colorspace(chroma) == "lin_srgb"


# ---------------------------------------------------------------------------
# tone_map
# ---------------------------------------------------------------------------


def test_tone_map_simple_zero():
    pixels = np.zeros((4, 4, 3), dtype=np.float32)
    result = tone_map(pixels, "simple")
    assert np.allclose(result, 0.0)


def test_tone_map_simple_one():
    pixels = np.ones((2, 2, 1), dtype=np.float32)
    result = tone_map(pixels, "simple")
    assert np.allclose(result, 0.5)


def test_tone_map_simple_clips_negative():
    pixels = np.full((2, 2, 1), -5.0, dtype=np.float32)
    result = tone_map(pixels, "simple")
    assert np.all(result == 0.0)


def test_tone_map_reinhard_produces_valid_range():
    rng = np.random.default_rng(42)
    pixels = rng.uniform(0.0, 100.0, (8, 8, 3)).astype(np.float32)
    result = tone_map(pixels, "reinhard")
    assert result.min() >= 0.0
    assert result.max() <= 1.0


def test_tone_map_filmic_produces_valid_range():
    rng = np.random.default_rng(42)
    pixels = rng.uniform(0.0, 100.0, (8, 8, 3)).astype(np.float32)
    result = tone_map(pixels, "filmic")
    assert result.min() >= 0.0
    assert result.max() <= 1.0


def test_tone_map_unknown_method_falls_back_to_simple():
    pixels = np.ones((2, 2, 1), dtype=np.float32)
    result = tone_map(pixels, "nonexistent")
    assert np.allclose(result, tone_map(pixels, "simple"))


# ---------------------------------------------------------------------------
# apply_exposure
# ---------------------------------------------------------------------------


def test_apply_exposure_zero_is_noop():
    pixels = np.array([1.0, 2.0, 4.0], dtype=np.float32)
    result = apply_exposure(pixels, 0.0)
    assert np.allclose(result, pixels)


def test_apply_exposure_one_stop_doubles():
    pixels = np.array([1.0, 2.0], dtype=np.float32)
    result = apply_exposure(pixels, 1.0)
    assert np.allclose(result, [2.0, 4.0])


def test_apply_exposure_minus_one_halves():
    pixels = np.array([2.0, 4.0], dtype=np.float32)
    result = apply_exposure(pixels, -1.0)
    assert np.allclose(result, [1.0, 2.0])


# ---------------------------------------------------------------------------
# apply_gamma
# ---------------------------------------------------------------------------


def test_apply_gamma_one_is_noop():
    pixels = np.array([0.5, 0.25], dtype=np.float32)
    result = apply_gamma(pixels, 1.0)
    assert np.allclose(result, pixels)


def test_apply_gamma_2_2():
    pixels = np.array([1.0], dtype=np.float32)
    result = apply_gamma(pixels, 2.2)
    assert np.allclose(result, [1.0])


def test_apply_gamma_clamps_negative():
    pixels = np.array([-1.0, 0.5], dtype=np.float32)
    result = apply_gamma(pixels, 2.2)
    assert result[0] == 0.0
    assert result[1] > 0.0


# ---------------------------------------------------------------------------
# normalize_pixels
# ---------------------------------------------------------------------------


def test_normalize_pixels_basic():
    pixels = np.array([0.0, 5.0, 10.0], dtype=np.float32)
    result = normalize_pixels(pixels)
    assert np.allclose(result, [0.0, 0.5, 1.0])


def test_normalize_pixels_constant_returns_zeros():
    pixels = np.full((4,), 7.0, dtype=np.float32)
    result = normalize_pixels(pixels)
    assert np.all(result == 0.0)


def test_normalize_pixels_negative_range():
    pixels = np.array([-2.0, 0.0, 2.0], dtype=np.float32)
    result = normalize_pixels(pixels)
    assert np.allclose(result, [0.0, 0.5, 1.0])


# ---------------------------------------------------------------------------
# to_pil_rgb
# ---------------------------------------------------------------------------


def test_to_pil_rgb_basic_shape():
    pixels = np.ones((100, 200, 3), dtype=np.float32) * 0.5
    img = to_pil_rgb(pixels, max_width=200, max_height=100)
    assert img.mode == "RGB"
    assert img.size[0] <= 200
    assert img.size[1] <= 100


def test_to_pil_rgb_thumbnails_down():
    pixels = np.ones((1000, 2000, 3), dtype=np.float32) * 0.5
    img = to_pil_rgb(pixels, max_width=128, max_height=128)
    assert img.size[0] <= 128
    assert img.size[1] <= 128


def test_to_pil_rgb_single_channel_broadcasts():
    pixels = np.full((10, 10, 1), 0.5, dtype=np.float32)
    img = to_pil_rgb(pixels)
    assert img.mode == "RGB"
    r, g, b = img.split()
    assert list(r.getdata()) == list(g.getdata()) == list(b.getdata())


def test_to_pil_rgb_clamps_over_one():
    pixels = np.full((4, 4, 3), 5.0, dtype=np.float32)
    img = to_pil_rgb(pixels, apply_srgb=False)
    # All values should be 255 after clip
    assert all(v == 255 for v in img.getdata()[0])


# ---------------------------------------------------------------------------
# to_pil_gray
# ---------------------------------------------------------------------------


def test_to_pil_gray_basic():
    pixels = np.ones((100, 100), dtype=np.float32) * 0.5
    img = to_pil_gray(pixels, max_width=64, max_height=64)
    assert img.mode == "L"
    assert img.size[0] <= 64


def test_to_pil_gray_from_hwc():
    pixels = np.ones((50, 50, 1), dtype=np.float32) * 0.25
    img = to_pil_gray(pixels)
    assert img.mode == "L"
