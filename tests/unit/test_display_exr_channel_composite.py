"""Unit tests for DisplayEXRChannel's A-over-B background compositing."""

from __future__ import annotations

import asyncio
import io
import warnings
from typing import Any
from unittest.mock import MagicMock, patch

import numpy as np
import pytest
from griptape.artifacts import ImageArtifact
from griptape_nodes.retained_mode.events.config_events import GetConfigValueResultFailure
from griptape_nodes.retained_mode.griptape_nodes import GriptapeNodes
from PIL import Image

from griptape_nodes_openexr.exr.colour.ocio_helpers import COLOR_MODE_OCIO
from griptape_nodes_openexr.exr.colour.transfer_functions import linear_to_srgb, srgb_to_linear
from griptape_nodes_openexr.exr.display_window import WINDOW_MODE_DATA, WindowFraming
from griptape_nodes_openexr.exr.exr_header_artifact import EXRChannelArtifact
from griptape_nodes_openexr.exr.exr_io import ChannelSource
from griptape_nodes_openexr.exr.exr_types import EXRChannelInfo, PixelType, WindowCoordinates
from griptape_nodes_openexr.nodes.display_exr_channel import _composite, _render_channels

_NODE_MODULE = "griptape_nodes_openexr.nodes.display_exr_channel"

H, W = 2, 3


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

    @pytest.mark.parametrize("tone", ["ACEScg to sRGB", "ACES2065-1 to sRGB"])
    def test_infinite_foreground_at_nonzero_alpha_renders_white_under_aces(self, tone: str) -> None:
        with warnings.catch_warnings():
            warnings.simplefilter("error", RuntimeWarning)
            out = _render(np.inf, 0.5, _background((10, 128, 240)), tone_mapping=tone)
        np.testing.assert_array_equal(out[0, 0], [255, 255, 255])

    def test_nan_alpha_preserves_background_without_warnings(self) -> None:
        with warnings.catch_warnings():
            warnings.simplefilter("error", RuntimeWarning)
            out = _render(1.0, np.nan, _background((10, 128, 240)))
        np.testing.assert_array_equal(out[0, 0], [10, 128, 240])

    def test_nan_foreground_channel_preserves_background_contribution(self) -> None:
        fg = np.array([[[np.nan, 1.0, 1.0]]], dtype=np.float32)
        bg = np.array([[[0.5, 0.5, 0.5]]], dtype=np.float32)
        alpha = np.array([[0.5]], dtype=np.float32)
        with warnings.catch_warnings():
            warnings.simplefilter("error", RuntimeWarning)
            blended = _composite(fg, alpha, bg)
        np.testing.assert_allclose(blended[0, 0], [0.25, 0.75, 0.75], rtol=1e-6)

    @pytest.mark.parametrize("tone", ["ACEScg to sRGB", "ACES2065-1 to sRGB"])
    def test_coloured_background_keeps_its_colour_under_an_aces_transform(self, tone: str) -> None:
        # The background is sRGB, not ACES, so the transform's gamut conversion must not
        # recolour it: it is moved into ACES primaries first and comes back unchanged.
        with patch.object(np.linalg, "inv", side_effect=AssertionError("inv must be precomputed")):
            out = _render(1.0, 0.0, _background((200, 60, 40)), tone_mapping=tone)
        np.testing.assert_allclose(out[0, 0].astype(int), [200, 60, 40], atol=1)

    def test_background_goes_through_the_selected_display_transform(self) -> None:
        # sRGB 128 decodes to linear 0.216; 'raw' writes that linear value unencoded.
        out = _render(1.0, 0.0, _background((128, 128, 128)), tone_mapping="raw")
        assert np.all(out == 55)

    def test_background_decode_uses_lookup_table_without_runtime_power(self) -> None:
        with patch(
            "griptape_nodes_openexr.exr.colour.transfer_functions.np.power",
            side_effect=AssertionError("no runtime pow"),
        ):
            out = _render(1.0, 0.0, _background((128, 128, 128)), tone_mapping="raw")
        assert np.all(out == 55)

    def test_bright_scene_values_brighten_soft_edges(self) -> None:
        # Scene values above 1.0 still contribute before the display transform clips them.
        out = _render(4.0, 0.25, _background((0, 0, 0)))
        assert out[0, 0, 0] == 255


class TestCompositeUnderOcio:
    def test_background_bypasses_the_ocio_transform(self) -> None:
        # The OCIO transform sees only the EXR pixels; the sRGB background is blended into its output.
        seen: list[np.ndarray] = []

        def _identity(rgb: np.ndarray, _params: Any, _tone: str) -> tuple[np.ndarray, str]:
            seen.append(rgb.copy())
            return rgb, "ocio:test"

        with patch(f"{_NODE_MODULE}.apply_color_management", autospec=True, side_effect=_identity):
            out = _render(1.0, 0.0, _background((10, 128, 240)), color_params=MagicMock())

        np.testing.assert_allclose(seen[0], 1.0)
        np.testing.assert_array_equal(out[0, 0], [10, 128, 240])

    def test_ocio_output_is_clamped_before_blending(self) -> None:
        # OCIO output of 2.0 at 50% alpha over black is 0.5 -> 128 once clamped, not 255.
        def _identity(rgb: np.ndarray, _params: Any, _tone: str) -> tuple[np.ndarray, str]:
            return rgb, "ocio:test"

        with patch(f"{_NODE_MODULE}.apply_color_management", autospec=True, side_effect=_identity):
            out = _render(2.0, 0.5, _background((0, 0, 0)), color_params=MagicMock())

        assert np.all(out == 128)

    def test_stale_retired_tone_mapping_is_ignored_in_ocio_mode(self) -> None:
        def _identity(rgb: np.ndarray, _params: Any, _tone: str) -> tuple[np.ndarray, str]:
            return rgb, "ocio:test"

        with patch(f"{_NODE_MODULE}.apply_color_management", autospec=True, side_effect=_identity):
            out = _render(0.5, 0.0, _background((10, 128, 240)), color_params=MagicMock(), tone_mapping="filmic")

        np.testing.assert_array_equal(out[0, 0], [10, 128, 240])


class TestToneMappingValidatedBeforeDecode:
    @pytest.mark.parametrize(
        ("invalid_tone", "expected_snippet"),
        [
            ("filmic", "no longer supported"),
            ("linear", "no longer supported"),
            ("reinhard", "Unsupported tone mapping"),
        ],
    )
    def test_invalid_tone_mapping_fails_before_loading_channels_or_background(
        self, invalid_tone: str, expected_snippet: str
    ) -> None:
        with patch(f"{_NODE_MODULE}.ProjectFileParameter", return_value=MagicMock()):
            from griptape_nodes_openexr.nodes.display_exr_channel import DisplayEXRChannel

            node = DisplayEXRChannel("test_prevalidate")

        for slot in "RGBA":
            node.set_parameter_value(f"channel_{slot.lower()}", _channel(slot))
        node.set_parameter_value("background", _background((200, 60, 40)))
        node.set_parameter_value("tone_mapping", invalid_tone)

        config_result = GetConfigValueResultFailure(result_details="setting absent")
        assert isinstance(config_result, GetConfigValueResultFailure)
        with (
            patch.object(GriptapeNodes, "handle_request", autospec=True, return_value=config_result),
            patch(f"{_NODE_MODULE}.load_slot_sources", autospec=True) as mock_load_slots,
            patch(f"{_NODE_MODULE}._load_background_rgb", autospec=True) as mock_load_bg,
        ):
            asyncio.run(node.aprocess())

        assert node.parameter_output_values.get("was_successful") is False
        assert expected_snippet in str(node.parameter_output_values.get("result_details", ""))
        mock_load_slots.assert_not_called()
        mock_load_bg.assert_not_called()

    def test_render_channels_rejects_invalid_tone_mapping_before_loading_background(self) -> None:
        rgb = np.ones((H, W, 3), dtype=np.float32)
        alpha = _plane(0.5)
        coverage = np.ones((H, W), dtype=bool)
        window = WindowCoordinates(0, 0, W - 1, H - 1)
        framing = WindowFraming(WINDOW_MODE_DATA, window, window)

        with (
            patch(f"{_NODE_MODULE}._load_background_rgb", autospec=True) as mock_load_bg,
            patch(f"{_NODE_MODULE}.srgb_uint8_to_linear", autospec=True) as mock_srgb_to_linear,
        ):
            background = _background((200, 60, 40))
            with pytest.raises(ValueError, match="no longer supported"):
                _render_channels(rgb, alpha, coverage, framing, background, None, "filmic")

        mock_load_bg.assert_not_called()
        mock_srgb_to_linear.assert_not_called()


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


def _plane(value: float) -> np.ndarray:
    return np.full((H, W), value, dtype=np.float32)


def _background(rgb: tuple[int, int, int]) -> ImageArtifact:
    buf = io.BytesIO()
    Image.new("RGB", (W, H), rgb).save(buf, format="PNG")
    return ImageArtifact(buf.getvalue(), format="png", width=W, height=H)


def _channel(name: str) -> EXRChannelArtifact:
    return EXRChannelArtifact("/tmp/fake.exr", 0, EXRChannelInfo(name, PixelType.HALF, 1, 1))


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

    window = WindowCoordinates(0, 0, W - 1, H - 1)
    config_result = GetConfigValueResultFailure(result_details="setting absent")
    assert isinstance(config_result, GetConfigValueResultFailure)
    with (
        patch.object(GriptapeNodes, "handle_request", return_value=config_result),
        patch(
            f"{_NODE_MODULE}.load_slot_sources",
            autospec=True,
            return_value={slot: ChannelSource(plane, window, window) for slot, plane in planes.items()},
        ),
        patch(f"{_NODE_MODULE}.ndarray_to_png", autospec=True, side_effect=_capture),
        patch(f"{_NODE_MODULE}.ImageUrlArtifact"),
    ):
        asyncio.run(node.aprocess())

    assert captured, node.get_parameter_value("result_details")
    return captured[0]
