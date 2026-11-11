"""EXRSequenceToPreview run end to end: real EXR frames, real pixel pipeline, real ffmpeg.

The unit tests mock the pixel functions and subprocess, so they show the node
builds the right command but not that the video holds the frames it should.
These read the encoded file back with ffprobe and ffmpeg.
"""

from __future__ import annotations

import asyncio
import json
import shutil
import subprocess
from contextlib import nullcontext
from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np
import OpenEXR
import pytest
from griptape_nodes.retained_mode.events.os_events import DeleteFileRequest
from griptape_nodes.retained_mode.griptape_nodes import GriptapeNodes
from static_ffmpeg import run  # type: ignore[import-untyped]

from griptape_nodes_openexr.exr.ocio_helpers import COLOR_MODE_BASIC
from griptape_nodes_openexr.exr.tone_mapping import TONE_REC709
from griptape_nodes_openexr.nodes.exr_sequence_to_preview import (
    MISSING_FRAMES_BLACK,
    MISSING_FRAMES_SKIP,
    EXRSequenceToPreview,
)

_NODE_MODULE = "griptape_nodes_openexr.nodes.exr_sequence_to_preview"
_OCIO_MODULE = "griptape_nodes_openexr.exr.ocio_helpers"
_FRAMES = 5
_SIZE = 16


@pytest.fixture(scope="module")
def ffmpeg_tools() -> tuple[str, str]:
    # static_ffmpeg is a runtime dependency, so a failed fetch fails the test
    # rather than skipping it: a skip here would pass CI with nothing checked.
    ffmpeg, ffprobe = run.get_or_fetch_platform_executables_else_raise()
    return ffmpeg, ffprobe


def _write_frames(directory: Path, count: int, value: float = 0.18) -> list[Path]:
    """Write `count` flat-grey RGB EXR frames."""
    header = {"compression": OpenEXR.ZIP_COMPRESSION, "type": OpenEXR.scanlineimage}
    plane = np.full((_SIZE, _SIZE), value, dtype=np.float32)
    paths = []
    for i in range(count):
        path = directory / f"frame.{i:04d}.exr"
        OpenEXR.File(header, {"R": plane, "G": plane, "B": plane}).write(str(path))
        paths.append(path)
    return paths


def _sequence(paths: list[Path]) -> dict:
    entries = [{"number": i, "padded_number": f"{i:04d}", "path": str(p)} for i, p in enumerate(paths)]
    return {
        "entries": entries,
        "first": 0,
        "last": len(paths) - 1,
        "discovered_first": 0,
        "discovered_last": len(paths) - 1,
        "padding": 4,
        "pattern": "frame.####.exr",
        "directory": str(paths[0].parent),
        "policy": "skip",
        "dropped_negative_number_count": 0,
        "present_numbers": list(range(len(paths))),
        "missing_numbers": [],
    }


def _run_node(
    paths: list[Path],
    tmp_path: Path,
    *,
    exposure: float = 0.0,
    missing_frames: str = MISSING_FRAMES_SKIP,
    ffmpeg_path: str | None = None,
) -> tuple[EXRSequenceToPreview, Path]:
    """Run the node with the engine's file plumbing and OCIO detection stubbed out.

    With `ffmpeg_path`, that executable stands in for ffmpeg.

    Returns:
        The node and where its video was saved.
    """
    with patch(f"{_OCIO_MODULE}.find_colorspace_transform_request_type", return_value=None):
        node = EXRSequenceToPreview("preview")

    saved_video = tmp_path / "saved.mp4"

    def _write_bytes(data: bytes) -> MagicMock:
        saved_video.write_bytes(data)
        saved = MagicMock()
        saved.location = str(saved_video)
        return saved

    node._output_file = MagicMock()
    node._output_file.build_file.return_value.write_bytes.side_effect = _write_bytes

    node.set_parameter_value("sequence", _sequence(paths))
    node.set_parameter_value("color_mode", COLOR_MODE_BASIC)
    node.set_parameter_value("tone_mapping", TONE_REC709)
    node.set_parameter_value("exposure", exposure)
    node.set_parameter_value("missing_frames", missing_frames)
    node.set_parameter_value("processing_speed", "fast")

    scratch = MagicMock()
    scratch.resolve.return_value = tmp_path / "scratch" / "frame_0000.png"

    def _handle_request(request: object) -> MagicMock:
        if isinstance(request, DeleteFileRequest) and request.path:
            shutil.rmtree(request.path, ignore_errors=True)
        return MagicMock()

    with (
        patch(f"{_NODE_MODULE}.ProjectFileDestination") as destination,
        patch.object(GriptapeNodes, "handle_request", side_effect=_handle_request),
        patch(
            f"{_NODE_MODULE}.run.get_or_fetch_platform_executables_else_raise",
            return_value=(ffmpeg_path, None),
        )
        if ffmpeg_path
        else nullcontext(),
    ):
        destination.from_situation.return_value = scratch
        asyncio.run(node.aprocess())

    return node, saved_video


def _frame_count(ffprobe: str, video: Path) -> int:
    result = subprocess.run(  # noqa: S603
        [ffprobe, "-v", "error", "-count_frames", "-select_streams", "v:0", "-show_streams", "-of", "json", str(video)],
        check=True,
        capture_output=True,
        text=True,
        timeout=60,
    )
    return int(json.loads(result.stdout)["streams"][0]["nb_read_frames"])


def _mean_luma(ffmpeg: str, video: Path) -> float:
    """Decode the first frame to 8-bit greyscale and average it."""
    result = subprocess.run(  # noqa: S603
        [ffmpeg, "-v", "error", "-i", str(video), "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "gray", "-"],
        check=True,
        capture_output=True,
        timeout=60,
    )
    return float(np.frombuffer(result.stdout, dtype=np.uint8).mean())


def _was_successful(node: EXRSequenceToPreview) -> bool | None:
    return node.parameter_output_values.get("was_successful")


def test_video_holds_one_frame_per_exr(tmp_path: Path, ffmpeg_tools: tuple[str, str]) -> None:
    _, ffprobe = ffmpeg_tools
    paths = _write_frames(tmp_path, _FRAMES)

    node, video = _run_node(paths, tmp_path)

    assert _was_successful(node), node.parameter_output_values.get("result_details")
    assert _frame_count(ffprobe, video) == _FRAMES


def test_frames_carry_the_exr_pixels(tmp_path: Path, ffmpeg_tools: tuple[str, str]) -> None:
    # 18% grey through Rec.709 lands well clear of black and white, so a
    # frame written with the wrong pixels falls outside this window.
    ffmpeg, _ = ffmpeg_tools
    node, video = _run_node(_write_frames(tmp_path, 1), tmp_path)

    assert _was_successful(node)
    assert 80 < _mean_luma(ffmpeg, video) < 140


def test_exposure_brightens_the_video(tmp_path: Path, ffmpeg_tools: tuple[str, str]) -> None:
    ffmpeg, _ = ffmpeg_tools
    paths = _write_frames(tmp_path, 1)
    lumas = {}
    for ev in (0.0, 2.0):
        run_dir = tmp_path / f"ev{ev}"
        run_dir.mkdir()
        node, video = _run_node(paths, run_dir, exposure=ev)
        assert _was_successful(node)
        lumas[ev] = _mean_luma(ffmpeg, video)

    # +2 EV is four times the light; after the transfer curve that is still
    # far more than encoder noise.
    assert lumas[2.0] > lumas[0.0] + 40


@pytest.mark.parametrize(
    ("missing_frames", "expected_frames"),
    [(MISSING_FRAMES_SKIP, _FRAMES - 1), (MISSING_FRAMES_BLACK, _FRAMES)],
)
def test_unreadable_frame_mid_sequence_keeps_later_frames(
    tmp_path: Path, ffmpeg_tools: tuple[str, str], missing_frames: str, expected_frames: int
) -> None:
    _, ffprobe = ffmpeg_tools
    paths = _write_frames(tmp_path, _FRAMES)
    paths[1].write_bytes(b"not an exr")

    node, video = _run_node(paths, tmp_path, missing_frames=missing_frames)

    assert _was_successful(node), node.parameter_output_values.get("result_details")
    assert _frame_count(ffprobe, video) == expected_frames


def test_ffmpeg_exiting_cleanly_without_output_fails_the_node(tmp_path: Path) -> None:
    # Stands in for an ffmpeg that returns 0 but writes nothing.
    no_op = shutil.which("true")
    if no_op is None:
        pytest.skip("no `true` executable on this platform")

    node, video = _run_node(_write_frames(tmp_path, 1), tmp_path, ffmpeg_path=no_op)

    assert _was_successful(node) is False
    assert "did not produce an output file" in node.parameter_output_values["result_details"]
    assert not video.exists()
