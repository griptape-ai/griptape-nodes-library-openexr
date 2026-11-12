"""Processing boundaries for display-window rendering."""

from __future__ import annotations

from io import BytesIO
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pytest
from PIL import Image

from griptape_nodes_openexr.exr import rendering
from griptape_nodes_openexr.exr.display_window import WindowFraming, select_window_channels
from griptape_nodes_openexr.exr.exr_types import WindowCoordinates
from griptape_nodes_openexr.nodes import exr_sequence_to_preview as sequence


def _framing(mode: str, display: tuple[int, int, int, int]) -> WindowFraming:
    return WindowFraming(mode, WindowCoordinates(-2, -1, 5, 4), WindowCoordinates(*display))


def _planes() -> dict[str, np.ndarray]:
    return {name: np.full((6, 8), 0.25, np.float32) for name in "RGBA"}


@pytest.mark.parametrize(
    ("display", "expected_shape", "origin"),
    [((0, 0, 3, 2), (3, 4), (0, 0)), ((2, 2, 9, 7), (3, 4), (2, 2))],
)
def test_selection_returns_views_and_updated_coordinates(
    display: tuple[int, int, int, int], expected_shape: tuple[int, int], origin: tuple[int, int]
) -> None:
    planes = _planes()
    selected, framing = select_window_channels(planes, _framing("display window", display))
    for name, plane in selected.items():
        assert plane.shape == expected_shape
        assert np.shares_memory(plane, planes[name])
    assert (framing.data_window.xmin, framing.data_window.ymin) == origin
    assert framing.display_window == display


@pytest.mark.parametrize("consumer", ["part", "sequence"])
@pytest.mark.parametrize(
    ("mode", "display", "processed_shape"),
    [
        ("display window", (0, 0, 3, 2), (3, 4, 3)),
        ("display window", (2, 2, 9, 7), (3, 4, 3)),
        ("display window", (-4, -3, 7, 6), (6, 8, 3)),
        ("data window", (0, 0, 3, 2), (6, 8, 3)),
        ("data window with display overlay", (0, 0, 3, 2), (6, 8, 3)),
    ],
)
def test_only_retained_pixels_are_processed(
    tmp_path: Path, consumer: str, mode: str, display: tuple[int, int, int, int], processed_shape: tuple[int, ...]
) -> None:
    # Given decoded full-data planes and an instrumented colour transform.
    module = rendering if consumer == "part" else sequence
    exposure_shapes = []
    colour_shapes = []

    def exposure(rgb: np.ndarray, ev: float) -> np.ndarray:  # noqa: ARG001
        exposure_shapes.append(rgb.shape)
        return rgb

    def colour(rgb: np.ndarray, params: object, tone: str) -> tuple[np.ndarray, str]:  # noqa: ARG001
        colour_shapes.append(rgb.shape)
        return rgb + 0.25, "test"

    framing = _framing(mode, display)
    with (
        patch.object(module, "load_exr_channels", return_value=_planes()),
        patch.object(module, "apply_exposure", side_effect=exposure),
        patch.object(module, "apply_color_management", side_effect=colour),
        patch.object(sequence, "read_framing", return_value=framing),
    ):
        # When the renderer executes.
        if consumer == "part":
            png, _ = rendering.render_part_png(
                file_path="unused.exr",
                part_index=0,
                selected=list("RGB"),
                alpha_channel="A",
                tone_mapping="sRGB",
                framing=framing,
            )
        else:
            png = sequence._convert_frame("unused.exr", 0, tmp_path, 0, 0.0, None, "sRGB", mode).read_bytes()

    # Then only the intersection reaches exposure and colour processing.
    assert exposure_shapes == [processed_shape]
    assert colour_shapes == [processed_shape]
    with Image.open(BytesIO(png)) as image:
        pixels = np.array(image)
    if mode == "display window" and display == (-4, -3, 7, 6):
        assert not pixels[:2].any(), "padding must bypass a transform that changes black"
        assert (pixels[2:8, 2:10, :3] == 128).all()


@pytest.mark.parametrize("consumer", ["part", "sequence"])
def test_disjoint_windows_preserve_colour_errors(tmp_path: Path, consumer: str) -> None:
    module = rendering if consumer == "part" else sequence
    framing = _framing("display window", (10, 10, 13, 12))
    with (
        patch.object(module, "load_exr_channels", return_value=_planes()),
        patch.object(sequence, "read_framing", return_value=framing),
        pytest.raises(ValueError, match="Unsupported tone mapping"),
    ):
        if consumer == "part":
            rendering.render_part_png(
                file_path="unused.exr",
                part_index=0,
                selected=list("RGB"),
                alpha_channel="A",
                tone_mapping="invalid",
                framing=framing,
            )
        else:
            sequence._convert_frame("unused.exr", 0, tmp_path, 0, 0.0, None, "invalid", framing.mode)


@pytest.mark.parametrize("consumer", ["part", "sequence"])
def test_subsampled_planes_fail_before_slicing(tmp_path: Path, consumer: str) -> None:
    module = rendering if consumer == "part" else sequence
    framing = _framing("display window", (0, 0, 3, 2))
    planes = {name: plane[::2, ::2] for name, plane in _planes().items()}
    with (
        patch.object(module, "load_exr_channels", return_value=planes),
        patch.object(sequence, "read_framing", return_value=framing),
        pytest.raises(ValueError, match="size of the data window"),
    ):
        if consumer == "part":
            rendering.render_part_png(
                file_path="unused.exr",
                part_index=0,
                selected=list("RGB"),
                alpha_channel="A",
                tone_mapping="sRGB",
                framing=framing,
            )
        else:
            sequence._convert_frame("unused.exr", 0, tmp_path, 0, 0.0, None, "sRGB", framing.mode)
