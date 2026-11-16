"""Invalid window modes fail before decoding or scheduling sequence frames."""

import asyncio
from pathlib import Path
from unittest.mock import patch

import pytest

from griptape_nodes_openexr.exr import display_window, rendering
from griptape_nodes_openexr.exr.display_window import WindowFraming
from griptape_nodes_openexr.exr.exr_types import WindowCoordinates
from griptape_nodes_openexr.nodes import display_exr_part, exr_sequence_to_preview
from tests.integration import test_read_exr as helpers
from tests.unit import test_exr_sequence_to_preview as sequence_helpers

engine = helpers.engine


@pytest.mark.parametrize("mode", ["typo", ""])
def test_part_renderer_rejects_mode_before_decode(mode: str) -> None:
    window = WindowCoordinates(0, 0, 3, 3)
    with patch.object(rendering, "load_exr_channels") as load, pytest.raises(ValueError, match="Unknown window_mode"):
        rendering.render_part_png(
            file_path="unused.exr",
            part_index=0,
            selected=["R"],
            alpha_channel=None,
            tone_mapping="raw",
            framing=WindowFraming(mode, window, window),
        )
    load.assert_not_called()


def test_sequence_frame_rejects_mode_before_decode(tmp_path: Path) -> None:
    with (
        patch.object(exr_sequence_to_preview, "load_exr_channels") as load,
        pytest.raises(ValueError, match="Unknown window_mode"),
    ):
        exr_sequence_to_preview._convert_frame("unused.exr", 0, tmp_path, 0, 0, None, "raw", "typo")
    load.assert_not_called()


def test_sequence_rejects_mode_before_scheduling_frames(tmp_path: Path) -> None:
    # Given a sequence and an invalid mode accepted as custom text.
    node = sequence_helpers._make_node()
    node.set_parameter_value("sequence", sequence_helpers._make_sequence([tmp_path / "frame.exr"]))
    node.set_parameter_value("window_mode", "typo")
    with patch.object(exr_sequence_to_preview, "run_over_sequence") as schedule:
        # When the sequence runs.
        asyncio.run(node.aprocess())
    # Then the error is reported once without submitting frame work.
    schedule.assert_not_called()
    assert node.parameter_output_values["was_successful"] is False
    assert "Unknown window_mode 'typo'" in node.parameter_output_values["result_details"]


@pytest.mark.parametrize("kind", ["read", "part"])
def test_image_nodes_reject_mode_before_calling_renderer(engine, tmp_path: Path, kind: str) -> None:
    path = helpers._write_windowed(tmp_path / "input.exr", (0, 0, 3, 3), (0, 0, 3, 3))
    if kind == "read":
        from griptape_nodes_openexr.nodes import read_exr

        node = helpers._make_node()
        node.set_parameter_value("path", str(path))
        module = read_exr
    else:
        node = display_exr_part.DisplayEXRPart("invalid_part")
        node.set_parameter_value("part", helpers._part_artifact(path))
        module = display_exr_part
    node.set_parameter_value("window_mode", "typo")
    with patch.object(module, "render_part_png") as render:
        asyncio.run(node.aprocess())
    render.assert_not_called()
    assert node.parameter_output_values["was_successful"] is False
    assert "Unknown window_mode 'typo'" in node.parameter_output_values["result_details"]


@pytest.mark.parametrize("mode", ["Full", "Crop Overscan", "Show Overscan Boundary"])
def test_shared_validator_accepts_final_names(mode: str) -> None:
    assert display_window.validate_window_mode(mode) == mode
