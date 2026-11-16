"""Framing preparation keeps RGB, alpha and coverage in one coordinate space."""

from unittest.mock import patch

import numpy as np
import pytest

from griptape_nodes_openexr.exr.display_window import WindowFraming, apply_window_mode
from griptape_nodes_openexr.exr.exr_types import WindowCoordinates
from griptape_nodes_openexr.nodes import display_exr_channel as channel


@pytest.mark.parametrize("mode", ["Full", "Crop Overscan", "Show Overscan Boundary"])
@pytest.mark.parametrize("alpha", [False, True])
@pytest.mark.parametrize("background", [False, True])
def test_preparation_frames_only_for_display_compositing(mode: str, alpha: bool, background: bool) -> None:
    # Given cropped source arrays and a larger intended frame.
    framing = WindowFraming(mode, WindowCoordinates(1, 1, 2, 2), WindowCoordinates(0, 0, 3, 3))
    rgb = np.ones((2, 2, 3), np.float32)
    alpha_plane = np.ones((2, 2), np.float32) if alpha else None
    coverage = np.ones((2, 2), bool)
    # When preparation decides which framing stage is needed.
    prepared = channel._prepare_framing(rgb, alpha_plane, coverage, framing, background)
    # Then pre-framed arrays and remaining geometry agree.
    if mode == "Crop Overscan" and alpha and background:
        assert prepared.rgb.shape == (4, 4, 3)
        assert prepared.framing.data_window == prepared.framing.display_window
        assert prepared.alpha is not None
        np.testing.assert_array_equal(prepared.alpha, prepared.coverage)
        np.testing.assert_array_equal(prepared.rgb[..., 0], prepared.coverage)
        assert apply_window_mode(prepared.rgb, prepared.framing) is prepared.rgb
    else:
        assert prepared.rgb is rgb
        assert prepared.alpha is alpha_plane
        assert prepared.coverage is coverage
        assert prepared.framing is framing


def test_overlay_is_applied_after_colour_processing() -> None:
    framing = WindowFraming("Show Overscan Boundary", WindowCoordinates(0, 0, 3, 3), WindowCoordinates(1, 1, 2, 2))
    events = []

    def colour(rgb, *_):
        events.append("colour")
        return rgb, "raw"

    def frame(image, geometry):
        events.append("frame")
        return apply_window_mode(image, geometry)

    with (
        patch.object(channel, "apply_color_management", side_effect=colour),
        patch.object(channel, "apply_window_mode", side_effect=frame),
    ):
        channel._render_channels(
            np.ones((4, 4, 3), np.float32), None, np.ones((4, 4), bool), framing, None, None, "raw"
        )
    assert events == ["colour", "frame"]


@pytest.mark.parametrize("phase", ["prepare", "finish"])
def test_framing_errors_propagate_from_shared_render_boundary(phase: str) -> None:
    framing = WindowFraming("Full", WindowCoordinates(0, 0, 1, 1), WindowCoordinates(0, 0, 1, 1))
    target = "_prepare_framing" if phase == "prepare" else "apply_window_mode"
    with (
        patch.object(channel, target, side_effect=ValueError("framing failed")),
        pytest.raises(ValueError, match="framing failed"),
    ):
        channel._render_channels(
            np.ones((2, 2, 3), np.float32), None, np.ones((2, 2), bool), framing, None, None, "raw"
        )
