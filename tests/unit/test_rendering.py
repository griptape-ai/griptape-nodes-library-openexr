"""Unit tests for PNG encoding, including optional ICC profile embedding."""

from __future__ import annotations

from io import BytesIO

import numpy as np
import pytest
from PIL import Image

from griptape_nodes_openexr.exr.icc_profiles import profile_for_tone_mapping
from griptape_nodes_openexr.exr.rendering import ndarray_to_png

_RGB = np.full((4, 4, 3), 128, dtype=np.uint8)
_RGBA = np.full((4, 4, 4), 128, dtype=np.uint8)


def _icc_of(png: bytes) -> bytes | None:
    return Image.open(BytesIO(png)).info.get("icc_profile")


class TestNdarrayToPng:
    @pytest.mark.parametrize("arr", [_RGB, _RGBA], ids=["rgb", "rgba"])
    def test_round_trips_pixels(self, arr: np.ndarray) -> None:
        np.testing.assert_array_equal(np.array(Image.open(BytesIO(ndarray_to_png(arr)))), arr)

    def test_rejects_wrong_channel_count(self) -> None:
        with pytest.raises(ValueError, match="expected"):
            ndarray_to_png(np.zeros((4, 4, 2), dtype=np.uint8))

    def test_rejects_wrong_dimensions(self) -> None:
        with pytest.raises(ValueError, match="expected"):
            ndarray_to_png(np.zeros((4, 4), dtype=np.uint8))


class TestIccEmbedding:
    def test_no_profile_by_default(self) -> None:
        # The untagged path is the default, and viewers read it as sRGB.
        assert _icc_of(ndarray_to_png(_RGB)) is None

    def test_default_output_is_unchanged(self) -> None:
        # Pins that adding the parameter did not alter existing output.
        assert ndarray_to_png(_RGB) == ndarray_to_png(_RGB, icc_profile=None)

    @pytest.mark.parametrize("arr", [_RGB, _RGBA], ids=["rgb", "rgba"])
    def test_profile_round_trips(self, arr: np.ndarray) -> None:
        profile = profile_for_tone_mapping("rec709")
        assert _icc_of(ndarray_to_png(arr, icc_profile=profile)) == profile

    def test_profile_does_not_change_pixels(self) -> None:
        # A profile describes the pixels; it must never alter them.
        profile = profile_for_tone_mapping("rec1886")
        tagged = np.array(Image.open(BytesIO(ndarray_to_png(_RGB, icc_profile=profile))))
        np.testing.assert_array_equal(tagged, np.array(Image.open(BytesIO(ndarray_to_png(_RGB)))))

    def test_tagging_adds_an_iccp_chunk(self) -> None:
        profile = profile_for_tone_mapping("sRGB")
        assert b"iCCP" not in ndarray_to_png(_RGB)
        assert b"iCCP" in ndarray_to_png(_RGB, icc_profile=profile)
