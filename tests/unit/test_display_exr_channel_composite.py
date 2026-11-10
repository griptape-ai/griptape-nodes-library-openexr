"""Unit tests for DisplayEXRChannel's A-over-B background compositing."""

from __future__ import annotations

import asyncio
import io
from typing import Any
from unittest.mock import MagicMock, patch

import numpy as np
import pytest
from griptape.artifacts import ImageArtifact
from PIL import Image

from griptape_nodes_openexr.exr.ocio_helpers import COLOR_MODE_OCIO
from griptape_nodes_openexr.exr.tone_mapping import linear_to_srgb, srgb_to_linear
from griptape_nodes_openexr.nodes.display_exr_channel import _composite

_NODE_MODULE = "griptape_nodes_openexr.nodes.display_exr_channel"

H, W = 2, 3


def _plane(value: float) -> np.ndarray:
    return np.full((H, W), value, dtype=np.float32)


def _background(rgb: tuple[int, int, int]) -> ImageArtifact:
    buf = io.BytesIO()
    Image.new("RGB", (W, H), rgb).save(buf, format="PNG")
    return ImageArtifact(buf.getvalue(), format="png", width=W, height=H)


def _channel(name: str) -> MagicMock:
    artifact = MagicMock()
    artifact.file_path = "/tmp/fake.exr"
    artifact.part_index = 0
    artifact.channel.name = name
    return artifact


def _render(
    rgb_value: float, alpha: float, background: ImageArtifact, color_params: Any = None, tone_mapping: str = "sRGB"
) -> np.ndarray:
    """Run DisplayEXRChannel and return the array it encodes to PNG. Basic sRGB mode unless color_params is given."""
    with patch(f"{_NODE_MODULE}.ProjectFileParameter", return_value=MagicMock()):
        from griptape_nodes_openexr.nodes.display_exr_channel import DisplayEXRChannel

        node = DisplayEXRChannel("test_composite")

    planes = {"R": _plane(rgb_value), "G": _plane(rgb_value), "B": _plane(rgb_value), "A": _plane(alpha)}
    for slot in "RGBA":
        node.set_parameter_value(f"channel_{slot.lower()}", _channel(slot))
    node.set_parameter_value("background", background)
    node.set_parameter_value("tone_mapping", tone_mapping)
    if color_params is not None:
        node.set_parameter_value("color_mode", COLOR_MODE_OCIO)
        node.set_parameter_value("color_params", color_params)

    captured: list[np.ndarray] = []

    def _capture(arr: np.ndarray, icc_profile: bytes | None = None) -> bytes:  # noqa: ARG001
        captured.append(arr)
        return b""

    with (
        patch(
            "griptape_nodes_openexr.exr.exr_io._load_parts_channels",
            side_effect=lambda _p, requests: {
                i: {name: planes[name] for name in names} for i, names in requests.items()
            },
        ),
        patch(f"{_NODE_MODULE}.ndarray_to_png", side_effect=_capture),
        patch(f"{_NODE_MODULE}.ImageUrlArtifact"),
    ):
        asyncio.run(node.aprocess())

    assert captured, node.get_parameter_value("result_details")
    return captured[0]


class TestCompositeInLinearLight:
    def test_half_alpha_white_over_black_blends_in_linear_light(self) -> None:
        # Linear 0.5 encodes to sRGB 0.735 -> 188. Blending the encoded values gives 128.
        out = _render(1.0, 0.5, _background((0, 0, 0)))
        assert out.shape == (H, W, 3)
        assert np.all(out == 188)

    def test_opaque_foreground_hides_background(self) -> None:
        out = _render(0.18, 1.0, _background((255, 0, 0)))
        expected = round(float(linear_to_srgb(np.array([0.18], dtype=np.float32))[0]) * 255)
        assert np.all(out == expected)

    def test_transparent_foreground_reproduces_background(self) -> None:
        # sRGB only: the background is decoded and re-encoded with the same curve, so it
        # round-trips. Other display transforms change it; see the 'raw' test below.
        out = _render(1.0, 0.0, _background((10, 128, 240)))
        np.testing.assert_array_equal(out[0, 0], [10, 128, 240])

    def test_infinite_foreground_at_zero_alpha_reproduces_background(self) -> None:
        out = _render(np.inf, 0.0, _background((10, 128, 240)))
        np.testing.assert_array_equal(out[0, 0], [10, 128, 240])

    def test_background_goes_through_the_selected_display_transform(self) -> None:
        # sRGB 128 decodes to linear 0.216; 'raw' writes that linear value unencoded.
        out = _render(1.0, 0.0, _background((128, 128, 128)), tone_mapping="raw")
        assert np.all(out == 55)

    def test_bright_scene_values_brighten_soft_edges(self) -> None:
        # Scene values above 1.0 still contribute before the display transform clips them.
        assert _render(4.0, 0.25, _background((0, 0, 0)))[0, 0, 0] == 255


class TestCompositeUnderOcio:
    def test_background_bypasses_the_ocio_transform(self) -> None:
        # The OCIO transform sees only the EXR pixels; the sRGB background is blended into its output.
        seen: list[np.ndarray] = []

        def _identity(rgb: np.ndarray, _params: Any, _tone: str) -> tuple[np.ndarray, str]:
            seen.append(rgb.copy())
            return rgb, "ocio:test"

        with patch(f"{_NODE_MODULE}.apply_color_management", side_effect=_identity):
            out = _render(1.0, 0.0, _background((10, 128, 240)), color_params=MagicMock())

        np.testing.assert_allclose(seen[0], 1.0)
        np.testing.assert_array_equal(out[0, 0], [10, 128, 240])

    def test_ocio_output_is_clamped_before_blending(self) -> None:
        # OCIO output of 2.0 at 50% alpha over black is 0.5 -> 128 once clamped, not 255.
        def _identity(rgb: np.ndarray, _params: Any, _tone: str) -> tuple[np.ndarray, str]:
            return rgb, "ocio:test"

        with patch(f"{_NODE_MODULE}.apply_color_management", side_effect=_identity):
            out = _render(2.0, 0.5, _background((0, 0, 0)), color_params=MagicMock())

        assert np.all(out == 128)


class TestCompositeHelper:
    def test_lerps_in_input_space(self) -> None:
        fg = np.ones((H, W, 3), dtype=np.float32)
        bg = np.zeros((H, W, 3), dtype=np.float32)
        np.testing.assert_allclose(_composite(fg, _plane(0.25), bg), 0.25)

    def test_alpha_is_clamped(self) -> None:
        fg = np.ones((H, W, 3), dtype=np.float32)
        bg = np.zeros((H, W, 3), dtype=np.float32)
        np.testing.assert_allclose(_composite(fg, _plane(2.0), bg), 1.0)
        np.testing.assert_allclose(_composite(fg, _plane(-1.0), bg), 0.0)


class TestSrgbToLinear:
    @pytest.mark.parametrize("value", [0.0, 0.002, 0.0031308, 0.01, 0.18, 0.5, 1.0])
    def test_inverts_linear_to_srgb(self, value: float) -> None:
        arr = np.array([value], dtype=np.float32)
        np.testing.assert_allclose(srgb_to_linear(linear_to_srgb(arr)), arr, atol=1e-6)

    def test_mid_value(self) -> None:
        assert srgb_to_linear(np.array([0.5], dtype=np.float32))[0] == pytest.approx(0.214041, rel=1e-5)

    def test_out_of_range_clamped(self) -> None:
        np.testing.assert_allclose(srgb_to_linear(np.array([-0.5, 2.0], dtype=np.float32)), [0.0, 1.0], atol=1e-6)

    def test_output_dtype_float32(self) -> None:
        assert srgb_to_linear(np.array([0.5], dtype=np.float32)).dtype == np.float32
