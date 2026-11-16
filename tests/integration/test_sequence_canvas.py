"""Sequence policies and real H.264 encoding of uniform staged frames."""

import asyncio
import json
import subprocess
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pytest
from griptape_nodes.retained_mode.events.os_events import DeleteFileResultSuccess, DeletionOutcome
from PIL import Image

from griptape_nodes_openexr.exr import render_limits, sequence_canvas
from griptape_nodes_openexr.exr.display_window import WindowFraming
from griptape_nodes_openexr.exr.exr_types import WindowCoordinates
from griptape_nodes_openexr.nodes import exr_sequence_to_preview as sequence
from tests.unit import test_exr_sequence_to_preview as helpers


@pytest.mark.parametrize(("name", "value"), [("odd_dimensions", "typo"), ("frame_sizes", "typo")])
def test_unknown_policies_fail_before_scheduling(tmp_path: Path, name: str, value: str) -> None:
    node = helpers._make_node()
    node.set_parameter_value("sequence", helpers._make_sequence([tmp_path / "frame.exr"]))
    node.set_parameter_value(name, value)
    with patch.object(sequence, "run_over_sequence") as schedule:
        asyncio.run(node.aprocess())
    schedule.assert_not_called()
    assert f"Unknown {name}" in node.parameter_output_values["result_details"]


def test_render_limit_checked_before_rgb_assembly(tmp_path: Path) -> None:
    planes = {name: np.ones((2, 4), np.float32) for name in "RGB"}
    with (
        patch.object(sequence, "load_exr_channels", return_value=planes),
        patch.object(sequence, "build_rgb_array") as build,
    ):
        with pytest.raises(render_limits.RenderLimitError):
            sequence._convert_frame("unused.exr", 0, tmp_path, 0, 0, None, "raw", max_pixels=7)
    build.assert_not_called()


def test_display_output_limit_precedes_padding_allocation(tmp_path: Path) -> None:
    planes = {name: np.ones((2, 2), np.float32) for name in "RGB"}
    framing = WindowFraming("Crop Overscan", WindowCoordinates(0, 0, 1, 1), WindowCoordinates(0, 0, 99, 99))
    with (
        patch.object(sequence, "load_exr_channels", return_value=planes),
        patch.object(sequence, "read_framing", return_value=framing),
        patch.object(sequence, "build_rgb_array") as build,
    ):
        with pytest.raises(render_limits.RenderLimitError):
            sequence._convert_frame("unused.exr", 0, tmp_path, 0, 0, None, "raw", "Crop Overscan", max_pixels=100)
    build.assert_not_called()


@pytest.mark.parametrize("missing", ["skip", "black", "error"])
def test_mismatching_geometry_is_fatal_regardless_of_missing_policy(tmp_path: Path, missing: str) -> None:
    from unittest.mock import AsyncMock, MagicMock

    node = helpers._make_node()
    node.set_parameter_value("sequence", helpers._make_sequence([tmp_path / "one.exr", tmp_path / "two.exr"]))
    node.set_parameter_value("missing_frames", missing)
    paths = []
    for index, size in enumerate([(4, 2), (2, 4)]):
        path = tmp_path / f"converted{index}.png"
        Image.new("RGB", size).save(path)
        paths.append(path)
    destination = MagicMock()
    destination.resolve.return_value = tmp_path / "scratch" / "frame_0000.png"
    with (
        patch.object(sequence.ProjectFileDestination, "from_situation", return_value=destination),
        patch.object(sequence, "run_over_sequence", new=AsyncMock(return_value=(paths, []))),
        patch.object(render_limits, "get_render_limit", return_value=100),
        patch.object(sequence.GriptapeNodes, "handle_request") as request,
        patch.object(sequence.run, "get_or_fetch_platform_executables_else_raise") as encoder,
    ):
        cleanup_result = DeleteFileResultSuccess(
            deleted_path=str(tmp_path / "scratch"),
            was_directory=True,
            deleted_paths=[],
            outcome=DeletionOutcome.PERMANENTLY_DELETED,
            result_details="removed",
        )
        assert isinstance(cleanup_result, DeleteFileResultSuccess)
        request.return_value = cleanup_result
        asyncio.run(node.aprocess())
    encoder.assert_not_called()
    assert node.parameter_output_values["was_successful"] is False
    assert "frame_sizes requires matching" in node.parameter_output_values["result_details"]


@pytest.mark.parametrize("missing", ["skip", "black", "error"])
def test_oversized_conversion_cannot_be_skipped(tmp_path: Path, missing: str) -> None:
    from unittest.mock import MagicMock

    node = helpers._make_node()
    node.set_parameter_value("sequence", helpers._make_sequence([tmp_path / "large.exr", tmp_path / "good.exr"]))
    node.set_parameter_value("missing_frames", missing)
    destination = MagicMock()
    destination.resolve.return_value = tmp_path / "scratch" / "frame_0000.png"
    cleanup_result = DeleteFileResultSuccess(
        deleted_path=str(tmp_path / "scratch"),
        was_directory=True,
        deleted_paths=[],
        outcome=DeletionOutcome.PERMANENTLY_DELETED,
        result_details="removed",
    )
    assert isinstance(cleanup_result, DeleteFileResultSuccess)
    planes = {name: np.ones((4, 4), np.float32) for name in "RGB"}
    with (
        patch.object(sequence.ProjectFileDestination, "from_situation", return_value=destination),
        patch.object(render_limits, "get_render_limit", return_value=8),
        patch.object(sequence, "load_exr_channels", return_value=planes),
        patch.object(sequence.GriptapeNodes, "handle_request", return_value=cleanup_result),
        patch.object(sequence.run, "get_or_fetch_platform_executables_else_raise") as encoder,
    ):
        asyncio.run(node.aprocess())
    encoder.assert_not_called()
    assert "openexr.max_render_pixels" in node.parameter_output_values["result_details"]
    assert "skipped" not in node.parameter_output_values["result_details"]


@pytest.mark.parametrize("container", ["mp4", "mov"])
def test_bundled_encoder_accepts_padded_sizes(tmp_path: Path, container: str) -> None:
    # Given odd frames with different dimensions.
    paths = []
    for index, size in enumerate([(7, 4), (4, 5)]):
        path = tmp_path / f"input{index}.png"
        Image.new("RGB", size, (100, 50, 25)).save(path)
        paths.append(path)
    prepared = sequence_canvas.prepare_frames(
        paths, ["wide.exr", "tall.exr"], tmp_path, "Pad to Even", "Pad to Largest", False, 100
    )
    sequence._number_contiguously(prepared.frames, tmp_path)
    executable, probe = sequence.run.get_or_fetch_platform_executables_else_raise()
    output = tmp_path / f"output.{container}"
    # When the actual bundled encoder reads uniform even canvases.
    encoded = subprocess.run(
        [
            executable,
            "-loglevel",
            "error",
            "-f",
            "image2",
            "-framerate",
            "24",
            "-i",
            str(tmp_path / sequence._FRAME_PATTERN),
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-y",
            str(output),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert encoded.returncode == 0, encoded.stderr
    probed = subprocess.run(
        [
            probe,
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=width,height,nb_frames",
            "-of",
            "json",
            str(output),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert probed.returncode == 0, probed.stderr
    # Then the stream dimensions and frame count match the explicit policy.
    stream = json.loads(probed.stdout)["streams"][0]
    assert (stream["width"], stream["height"], int(stream["nb_frames"])) == (8, 6, 2)
