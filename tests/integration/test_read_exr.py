"""Integration tests for the ReadEXR node against real EXR fixtures.

Unit coverage lives in tests/unit/test_read_exr.py and stubs the EXR reader.
These exercise the real decode path, where the pixels the node renders are the
only way to tell that the right part and layer were read.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import numpy as np
import pytest
from griptape.artifacts import ImageUrlArtifact
from griptape_nodes.common.sequences import Sequence
from griptape_nodes.retained_mode.engine import Engine
from griptape_nodes.retained_mode.events.artifact_events import (
    GetPreviewForArtifactRequest,
    GetPreviewForArtifactResultFailure,
)
from griptape_nodes.retained_mode.events.connection_events import (
    ListConnectionsForNodeResultSuccess,
)
from griptape_nodes.retained_mode.events.os_events import (
    ScanSequencesRequest,
    ScanSequencesResultSuccess,
)
from griptape_nodes.retained_mode.griptape_nodes import GriptapeNodes
from PIL import Image

from griptape_nodes_openexr.exr.channel_selection import select_display_channels
from griptape_nodes_openexr.exr.exr_header_artifact import EXRChannelArtifact, EXRPartArtifact
from griptape_nodes_openexr.exr.exr_io import scan_exr_header, write_exr_channels
from griptape_nodes_openexr.exr.ocio_helpers import COLOR_MODE_BASIC, COLOR_MODE_OCIO
from griptape_nodes_openexr.exr.tone_mapping import TONE_LINEAR

DATA = Path(__file__).parents[1] / "data"
SINGLE_PART_RGBA = DATA / "single_part_rgba.exr"
SINGLE_PART_AOVS = DATA / "single_part_aovs.exr"
MULTI_PART = DATA / "multi_part.exr"

_NODE_MODULE = "griptape_nodes_openexr.nodes.read_exr"
# Decode and colour management live in the shared rendering helper, so that is
# where they are patched from.
_RENDER_MODULE = "griptape_nodes_openexr.exr.rendering"
_OCIO_MODULE = "griptape_nodes_openexr.exr.ocio_helpers"


def _sequence_dict(paths: list[Path], *, start: int) -> dict:
    """A Sequence-shaped dict pointing several frame slots at real fixtures on disk.

    There are no multi-frame EXR fixtures, so a sequence is faked by reusing the
    single-file fixtures across frame numbers. The node only ever opens
    `entry.path`, so this exercises the real decode per frame.
    """
    numbers = [start + i for i in range(len(paths))]
    entries = [{"number": n, "padded_number": f"{n:04d}", "path": str(p)} for n, p in zip(numbers, paths, strict=True)]
    return {
        "entries": entries,
        "first": numbers[0],
        "last": numbers[-1],
        "discovered_first": numbers[0],
        "discovered_last": numbers[-1],
        "padding": 4,
        "pattern": "frame.####.exr",
        "directory": str(paths[0].parent),
        "policy": "skip",
        "dropped_negative_number_count": 0,
        "present_numbers": numbers,
        "missing_numbers": [],
    }


class _Engine:
    """Answers the handful of requests ReadEXR makes, writing output to tmp_path."""

    def __init__(self, tmp_path: Path) -> None:
        self.tmp_path = tmp_path
        self.written: list[Path] = []

    def handle_request(self, request: Any) -> Any:
        if isinstance(request, ScanSequencesRequest):
            sequence = Sequence.model_validate(_sequence_dict([Path(request.path)], start=1))
            return ScanSequencesResultSuccess(
                sequences=[sequence], has_entries=True, directory_had_matching_files=True, result_details=""
            )
        if isinstance(request, GetPreviewForArtifactRequest):
            # The engine's preview machinery is the engine's test; here it only has
            # to degrade cleanly.
            return GetPreviewForArtifactResultFailure(result_details="no provider registered in tests")
        return ListConnectionsForNodeResultSuccess(incoming_connections=[], outgoing_connections=[], result_details="")

    def project_file_parameter(self, **_kwargs: Any) -> MagicMock:
        component = MagicMock()

        def _build_file(**extra_vars: Any) -> MagicMock:
            index = extra_vars.get("_index")
            suffix = f".{index}" if index is not None else ""
            path = self.tmp_path / f"read_exr{suffix}.png"
            destination = MagicMock()

            def _write_bytes(content: bytes) -> MagicMock:
                path.write_bytes(content)
                self.written.append(path)
                return MagicMock(location=str(path))

            destination.write_bytes.side_effect = _write_bytes
            return destination

        component.build_file.side_effect = _build_file
        return component


@pytest.fixture
def engine(tmp_path: Path):
    bench = _Engine(tmp_path)
    with (
        patch(f"{_NODE_MODULE}.ProjectFileParameter", side_effect=bench.project_file_parameter),
        patch.object(GriptapeNodes, "handle_request", side_effect=bench.handle_request),
        patch.object(
            Engine, "handle_request", autospec=True, side_effect=lambda _self, request: bench.handle_request(request)
        ),
    ):
        yield bench


def _make_node():
    from griptape_nodes_openexr.nodes.read_exr import ReadEXR

    with patch(f"{_OCIO_MODULE}.find_colorspace_transform_request_type", return_value=None):
        return ReadEXR("test_read_exr")


def _was_successful(node) -> bool | None:
    return node.parameter_output_values.get("was_successful")


def _result_details(node) -> str:
    return node.parameter_output_values.get("result_details", "") or ""


def _rendered(node) -> np.ndarray:
    artifact = node.parameter_output_values["image"]
    assert isinstance(artifact, ImageUrlArtifact)
    with Image.open(artifact.value) as image:
        return np.array(image)


async def _run(node) -> None:
    await node.aprocess()


class TestSinglePartFile:
    def test_one_node_yields_image_part_and_channels(self, engine: _Engine) -> None:
        import asyncio

        node = _make_node()
        node.set_parameter_value("path", str(SINGLE_PART_RGBA))
        asyncio.run(_run(node))

        assert _was_successful(node) is True, _result_details(node)
        assert isinstance(node.parameter_output_values["image"], ImageUrlArtifact)
        assert isinstance(node.parameter_output_values["part"], EXRPartArtifact)
        channels = node.parameter_output_values["channels"]
        assert {channel.channel.name for channel in channels} == {"R", "G", "B", "A"}
        assert all(isinstance(channel, EXRChannelArtifact) for channel in channels)

    def test_rendered_image_is_a_readable_png(self, engine: _Engine) -> None:
        import asyncio

        node = _make_node()
        node.set_parameter_value("path", str(SINGLE_PART_RGBA))
        asyncio.run(_run(node))

        pixels = _rendered(node)
        # single_part_rgba carries an alpha channel, so the render is RGBA.
        assert pixels.shape == (64, 64, 4)
        assert pixels.dtype == np.uint8

    def test_header_scalars_match_the_file(self, engine: _Engine) -> None:
        import asyncio

        expected = scan_exr_header(str(SINGLE_PART_RGBA))
        node = _make_node()
        node.set_parameter_value("path", str(SINGLE_PART_RGBA))
        asyncio.run(_run(node))

        assert node.parameter_output_values["image_width"] == expected.parts[0].width
        assert node.parameter_output_values["image_height"] == expected.parts[0].height
        assert node.parameter_output_values["part_count"] == len(expected.parts)
        assert node.parameter_output_values["channel_count"] == len(expected.parts[0].channels)


class TestMultiPartFile:
    def test_part_dropdown_lists_the_real_part_names(self, engine: _Engine) -> None:
        node = _make_node()
        node.set_parameter_value("path", str(MULTI_PART))
        assert node._part_choices() == ["0: rgba", "1: depth", "2: normal"]

    def test_selecting_depth_renders_that_part(self, engine: _Engine) -> None:
        import asyncio

        node = _make_node()
        node.set_parameter_value("path", str(MULTI_PART))
        node.set_parameter_value("part_name", "1: depth")
        asyncio.run(_run(node))

        assert _was_successful(node) is True, _result_details(node)
        part = node.parameter_output_values["part"]
        assert part.name == "depth"
        assert part.part_index == 1
        # depth is a single Z channel, broadcast to grey, so no alpha plane.
        assert _rendered(node).shape == (64, 64, 3)

    def test_depth_and_rgba_render_differently(self, engine: _Engine) -> None:
        import asyncio

        node = _make_node()
        node.set_parameter_value("path", str(MULTI_PART))
        asyncio.run(_run(node))
        rgba = _rendered(node)[..., :3]

        node.set_parameter_value("part_name", "1: depth")
        asyncio.run(_run(node))
        depth = _rendered(node)

        assert not np.array_equal(rgba, depth)


class TestLayers:
    def test_layer_dropdown_lists_the_aov_prefixes(self, engine: _Engine) -> None:
        node = _make_node()
        node.set_parameter_value("path", str(SINGLE_PART_AOVS))
        # Order follows the channel order the file reports, which OpenEXR sorts.
        assert node._layer_choices() == ["beauty", "depth", "diffuse", "normal"]

    def test_default_layer_matches_select_display_channels(self, engine: _Engine) -> None:
        node = _make_node()
        node.set_parameter_value("path", str(SINGLE_PART_AOVS))

        channel_names = [channel.name for channel in scan_exr_header(str(SINGLE_PART_AOVS)).parts[0].channels]
        selected = select_display_channels(channel_names)
        assert selected is not None
        assert node.get_parameter_value("layer") == selected[0].rpartition(".")[0]

    def test_choosing_a_layer_changes_the_render(self, engine: _Engine) -> None:
        import asyncio

        node = _make_node()
        node.set_parameter_value("path", str(SINGLE_PART_AOVS))
        asyncio.run(_run(node))
        beauty = _rendered(node)[..., :3]

        node.set_parameter_value("layer", "diffuse")
        asyncio.run(_run(node))
        diffuse = _rendered(node)[..., :3]

        assert not np.array_equal(beauty, diffuse)


class TestColorManagement:
    def test_exposure_brightens_the_render(self, engine: _Engine) -> None:
        import asyncio

        node = _make_node()
        node.set_parameter_value("path", str(SINGLE_PART_RGBA))
        node.set_parameter_value("tone_mapping", TONE_LINEAR)
        asyncio.run(_run(node))
        base = _rendered(node)[..., :3].astype(np.int32)

        node.set_parameter_value("exposure", 2.0)
        asyncio.run(_run(node))
        brighter = _rendered(node)[..., :3].astype(np.int32)

        assert brighter.sum() > base.sum()

    def test_tone_mapping_changes_the_render(self, engine: _Engine) -> None:
        import asyncio

        node = _make_node()
        node.set_parameter_value("path", str(SINGLE_PART_RGBA))
        asyncio.run(_run(node))
        filmic = _rendered(node)

        node.set_parameter_value("tone_mapping", TONE_LINEAR)
        asyncio.run(_run(node))
        linear = _rendered(node)

        assert not np.array_equal(filmic, linear)

    def test_ocio_mode_without_color_params_fails_loudly(self, engine: _Engine) -> None:
        import asyncio

        node = _make_node()
        node.set_parameter_value("path", str(SINGLE_PART_RGBA))
        node.set_parameter_value("color_mode", COLOR_MODE_OCIO)
        asyncio.run(_run(node))

        assert _was_successful(node) is False
        assert "color_params" in _result_details(node)
        assert node.parameter_output_values["image"] is None

    def test_basic_mode_is_the_default_without_ocio(self, engine: _Engine) -> None:
        node = _make_node()
        assert node.get_parameter_value("color_mode") == COLOR_MODE_BASIC


class TestViewerButton:
    def test_fails_before_the_node_has_run(self, engine: _Engine) -> None:
        node = _make_node()
        result = node._on_open_viewer_click(MagicMock(), MagicMock())
        assert result is not None
        assert result.success is False

    def test_succeeds_after_a_run(self, engine: _Engine) -> None:
        import asyncio

        node = _make_node()
        node.set_parameter_value("path", str(SINGLE_PART_RGBA))
        asyncio.run(_run(node))

        with patch(f"{_NODE_MODULE}.handle_viewer_button_click", return_value=None) as launch:
            node._on_open_viewer_click(MagicMock(), MagicMock())
        launch.assert_called_once_with(node.name, str(SINGLE_PART_RGBA))


def _write_frames(tmp_path: Path, numbers: range) -> list[Path]:
    """Write a real EXR per frame, each a flat grey whose value encodes its frame.

    Same layout throughout. The per-frame grey level is what makes "the right
    frame was decoded" visible in the output pixels.
    """
    paths = []
    for offset, number in enumerate(numbers):
        value = np.full((8, 8), 0.1 * (offset + 1), dtype=np.float32)
        path = tmp_path / f"frame.{number:04d}.exr"
        write_exr_channels(str(path), {"R": value, "G": value, "B": value}, pixel_type="float")
        paths.append(path)
    return paths


class TestSequenceOverRealFiles:
    def test_named_frame_decodes_that_entry(self, engine: _Engine, tmp_path: Path) -> None:
        import asyncio

        paths = _write_frames(tmp_path, range(1001, 1004))
        node = _make_node()
        node.set_parameter_value("path", _sequence_dict(paths, start=1001))

        node.set_parameter_value("frame", 1001)
        asyncio.run(_run(node))
        assert _was_successful(node) is True, _result_details(node)
        first = _rendered(node)[..., :3].astype(np.int32)

        node.set_parameter_value("frame", 1003)
        asyncio.run(_run(node))
        assert _was_successful(node) is True, _result_details(node)
        third = _rendered(node)[..., :3].astype(np.int32)

        part = node.parameter_output_values["part"]
        assert part.frame_number == 1003
        assert part.file_path == str(paths[2])
        # Each frame is a brighter grey than the last.
        assert third.sum() > first.sum()

    def test_a_frame_with_its_own_data_window_is_read_at_its_own_size(self, engine: _Engine, tmp_path: Path) -> None:
        import asyncio

        # CG renders auto-crop the data window per frame, so frames of one sequence
        # differ in size. With alpha selected, rendering against frame 1's header
        # would reshape this frame's alpha to the wrong size.
        paths = []
        for number, (height, width) in zip((1001, 1002), ((8, 8), (4, 6)), strict=True):
            value = np.full((height, width), 0.5, dtype=np.float32)
            path = tmp_path / f"frame.{number:04d}.exr"
            write_exr_channels(str(path), dict.fromkeys("RGBA", value), pixel_type="float")
            paths.append(path)
        node = _make_node()
        node.set_parameter_value("path", _sequence_dict(paths, start=1001))
        node.set_parameter_value("frame", 1002)
        asyncio.run(_run(node))

        assert _was_successful(node) is True, _result_details(node)
        assert _rendered(node).shape == (4, 6, 4)
        assert node.parameter_output_values["image_width"] == 6
        assert node.parameter_output_values["image_height"] == 4
        part = node.parameter_output_values["part"]
        assert (part.width, part.height) == (6, 4)
        assert part.file_path == str(paths[1])

    def test_bounds_outputs_describe_the_sequence(self, engine: _Engine, tmp_path: Path) -> None:
        import asyncio

        paths = _write_frames(tmp_path, range(1001, 1004))
        node = _make_node()
        node.set_parameter_value("path", _sequence_dict(paths, start=1001))
        asyncio.run(_run(node))

        assert node.parameter_output_values["first_frame"] == 1001
        assert node.parameter_output_values["last_frame"] == 1003
        assert node.parameter_output_values["frame_count"] == 3

    def test_output_filename_carries_the_frame_number(self, engine: _Engine, tmp_path: Path) -> None:
        import asyncio

        paths = _write_frames(tmp_path, range(1001, 1004))
        node = _make_node()
        node.set_parameter_value("path", _sequence_dict(paths, start=1001))
        node.set_parameter_value("frame", 1002)
        asyncio.run(_run(node))

        assert engine.written[-1].name == "read_exr.1002.png"
