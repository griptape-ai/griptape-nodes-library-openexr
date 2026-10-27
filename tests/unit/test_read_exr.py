"""Unit tests for the ReadEXR node."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import numpy as np
import pytest
from griptape.artifacts import ImageUrlArtifact
from griptape_nodes.exe_types.core_types import Parameter, ParameterMode
from griptape_nodes.retained_mode.engine import Engine
from griptape_nodes.retained_mode.events.artifact_events import (
    GetPreviewForArtifactRequest,
    GetPreviewForArtifactResultFailure,
    GetPreviewForArtifactResultSuccess,
    PreviewGenerationPolicy,
)
from griptape_nodes.retained_mode.events.connection_events import (
    ListConnectionsForNodeRequest,
    ListConnectionsForNodeResultSuccess,
)
from griptape_nodes.retained_mode.events.os_events import (
    ScanSequencesRequest,
    ScanSequencesResultSuccess,
)
from griptape_nodes.retained_mode.griptape_nodes import GriptapeNodes

from griptape_nodes_openexr.exr.channel_selection import ROOT_LAYER
from griptape_nodes_openexr.exr.exr_header_artifact import EXRChannelArtifact, EXRPartArtifact
from griptape_nodes_openexr.exr.exr_types import (
    EXRChannelInfo,
    EXRData,
    EXRHeader,
    EXRPart,
    PixelType,
    StorageType,
    WindowCoordinates,
)

DATA = Path(__file__).parents[1] / "data"
SINGLE_PART_RGBA = DATA / "single_part_rgba.exr"

_NODE_MODULE = "griptape_nodes_openexr.nodes.read_exr"
_OCIO_MODULE = "griptape_nodes_openexr.exr.ocio_helpers"

WIDTH = 16
HEIGHT = 10


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_sequence(paths: list[Path], *, start: int = 0, missing: list[int] | None = None) -> dict:
    """Build a minimal Sequence-shaped dict for wiring into the node.

    `start` offsets the frame numbers from the list index (default 0, so
    number == index) so tests can tell "frame number" and "list position"
    apart, the way a real sequence like 1001-1100 would. `missing` drops those
    frame numbers from `entries` while leaving the sequence bounds spanning
    them, which is what a SKIP-policy scan over a gappy sequence produces.
    """
    numbers = [start + i for i in range(len(paths))]
    pairs = [(n, p) for n, p in zip(numbers, paths, strict=True) if n not in (missing or [])]
    entries = [{"number": n, "padded_number": f"{n:04d}", "path": str(p)} for n, p in pairs]
    return {
        "entries": entries,
        "first": numbers[0] if numbers else 0,
        "last": numbers[-1] if numbers else 0,
        "discovered_first": numbers[0] if numbers else 0,
        "discovered_last": numbers[-1] if numbers else 0,
        "padding": 4,
        "pattern": "frame.####.exr",
        "directory": str(paths[0].parent) if paths else "",
        "policy": "skip",
        "dropped_negative_number_count": 0,
        "present_numbers": [n for n, _ in pairs],
        "missing_numbers": list(missing or []),
    }


def _make_window(xmax: int = WIDTH - 1, ymax: int = HEIGHT - 1) -> WindowCoordinates:
    return WindowCoordinates(xmin=0, ymin=0, xmax=xmax, ymax=ymax)


def _make_header(name: str = "") -> EXRHeader:
    return EXRHeader(
        name=name,
        compression=MagicMock(value="ZIP_COMPRESSION"),
        line_order=MagicMock(value="INCREASING_Y"),
        data_window=_make_window(),
        display_window=_make_window(),
        pixel_aspect_ratio=1.0,
        screen_window_center=(0.0, 0.0),
        screen_window_width=1.0,
        storage_type=StorageType.SCANLINE_IMAGE,
        chunk_count=None,
        tile_description=None,
        chromaticities=None,
        time_code=None,
        owner=None,
        comments=None,
        capture_date=None,
        software=None,
        custom={},
    )


def _make_part(name: str = "", channel_names: tuple[str, ...] = ("R", "G", "B", "A")) -> EXRPart:
    channels = [
        EXRChannelInfo(name=channel, pixel_type=PixelType.HALF, x_sampling=1, y_sampling=1) for channel in channel_names
    ]
    return EXRPart(name=name, channels=channels, header=_make_header(name), width=WIDTH, height=HEIGHT)


def _make_exr_data(*parts: EXRPart) -> EXRData:
    return EXRData(parts=list(parts) if parts else [_make_part()])


def _make_pixels(channel_names: tuple[str, ...] = ("R", "G", "B", "A")) -> dict[str, np.ndarray]:
    return {name: np.full((HEIGHT, WIDTH), 0.18, dtype=np.float32) for name in channel_names}


class _Harness:
    """Wires a ReadEXR node to stub I/O, recording what it asked for."""

    def __init__(self, tmp_path: Path) -> None:
        self.tmp_path = tmp_path
        self.exr_data = _make_exr_data()
        self.pixels = _make_pixels()
        self.sequence: dict | None = None
        self.connected_params: set[str] = set()
        self.preview_succeeds = True
        self.scan_calls: list[str] = []
        self.load_calls: list[tuple[str, int, list[str]]] = []
        self.preview_requests: list[GetPreviewForArtifactRequest] = []
        self.build_file_calls: list[dict[str, Any]] = []
        self.written: list[Path] = []

    # -- stubs standing in for the EXR reader --

    def scan_exr_header(self, file_path: str, *, header_only: bool = True) -> EXRData:  # noqa: ARG002
        self.scan_calls.append(file_path)
        return self.exr_data

    def load_exr_channels(self, file_path: str, part_index: int, channel_names: list[str]) -> dict[str, np.ndarray]:
        self.load_calls.append((file_path, part_index, list(channel_names)))
        return {name: self.pixels[name] for name in channel_names}

    # -- stubs standing in for the engine --

    def handle_request(self, request: Any) -> Any:
        if isinstance(request, ScanSequencesRequest):
            from griptape_nodes.common.sequences import Sequence

            raw = self.sequence if self.sequence is not None else _make_sequence([Path(request.path)], start=1)
            sequence = Sequence.model_validate(raw)
            return ScanSequencesResultSuccess(
                sequences=[sequence],
                has_entries=bool(sequence.entries),
                directory_had_matching_files=True,
                result_details="",
            )
        if isinstance(request, ListConnectionsForNodeRequest):
            incoming: list[Any] = [
                MagicMock(target_parameter_name=name, source_node_name="upstream") for name in self.connected_params
            ]
            return ListConnectionsForNodeResultSuccess(
                incoming_connections=incoming, outgoing_connections=[], result_details=""
            )
        if isinstance(request, GetPreviewForArtifactRequest):
            self.preview_requests.append(request)
            if not self.preview_succeeds:
                return GetPreviewForArtifactResultFailure(result_details="no provider")
            return GetPreviewForArtifactResultSuccess(
                paths_to_preview=str(self.tmp_path / "preview.webp"), result_details=""
            )
        return MagicMock()

    # -- stub standing in for the project file destination --

    def project_file_parameter(self, **_kwargs: Any) -> MagicMock:
        component = MagicMock()

        def _build_file(**extra_vars: Any) -> MagicMock:
            self.build_file_calls.append(extra_vars)
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
def harness(tmp_path: Path):
    bench = _Harness(tmp_path)
    with (
        patch(f"{_NODE_MODULE}.scan_exr_header", side_effect=bench.scan_exr_header),
        patch(f"{_NODE_MODULE}.load_exr_channels", side_effect=bench.load_exr_channels),
        patch(f"{_NODE_MODULE}.ProjectFileParameter", side_effect=bench.project_file_parameter),
        patch.object(GriptapeNodes, "handle_request", side_effect=bench.handle_request),
        # Node machinery (e.g. the connection lookup behind the preview suppression)
        # dispatches through the engine rather than the GriptapeNodes facade.
        patch.object(
            Engine, "handle_request", autospec=True, side_effect=lambda _self, request: bench.handle_request(request)
        ),
    ):
        yield bench


def _make_node(*, ocio_available: bool = False):
    from griptape_nodes_openexr.nodes.read_exr import ReadEXR

    detected = MagicMock() if ocio_available else None
    with patch(f"{_OCIO_MODULE}.find_colorspace_transform_request_type", return_value=detected):
        return ReadEXR("test_read_exr")


def _param(node, name: str) -> Parameter:
    parameter = node.get_parameter_by_name(name)
    assert parameter is not None, name
    return parameter


def _was_successful(node) -> bool | None:
    return node.parameter_output_values.get("was_successful")


def _result_details(node) -> str:
    return node.parameter_output_values.get("result_details", "") or ""


def _frames(tmp_path: Path, numbers: range) -> list[Path]:
    paths = []
    for number in numbers:
        path = tmp_path / f"frame.{number:04d}.exr"
        path.write_bytes(b"")
        paths.append(path)
    return paths


def _single_file(tmp_path: Path) -> Path:
    path = tmp_path / "shot.exr"
    path.write_bytes(b"")
    return path


# ---------------------------------------------------------------------------
# Construction
# ---------------------------------------------------------------------------


class TestNodeInit:
    def test_input_and_output_parameters_exist(self, harness: _Harness) -> None:
        node = _make_node()
        for name in (
            "path",
            "part_name",
            "layer",
            "frame",
            "exposure",
            "color_mode",
            "tone_mapping",
            "color_params",
            "image",
            "part",
            "channels",
            "image_width",
            "image_height",
            "part_count",
            "channel_count",
            "first_frame",
            "last_frame",
            "frame_count",
        ):
            assert node.get_parameter_by_name(name) is not None, name

    def test_part_input_and_part_output_do_not_collide(self, harness: _Harness) -> None:
        node = _make_node()
        assert _param(node, "part").output_type == "EXRPartArtifact"
        assert _param(node, "part_name").type == "str"

    def test_frame_slider_has_soft_limits(self, harness: _Harness) -> None:
        node = _make_node()
        assert node._frame_param.soft_limits is True

    def test_frame_accepts_input_connections(self, harness: _Harness) -> None:
        node = _make_node()
        assert ParameterMode.INPUT in _param(node, "frame").allowed_modes

    def test_selectors_start_hidden(self, harness: _Harness) -> None:
        node = _make_node()
        for name in ("part_name", "layer", "frame"):
            assert _param(node, name).ui_options.get("hide") is True, name


# ---------------------------------------------------------------------------
# Failure paths
# ---------------------------------------------------------------------------


class TestAProcessFailures:
    def test_no_path_fails(self, harness: _Harness) -> None:
        node = _make_node()
        asyncio.run(node.aprocess())
        assert _was_successful(node) is False
        assert "path" in _result_details(node).lower()

    def test_viewer_button_fails_before_run(self, harness: _Harness) -> None:
        node = _make_node()
        result = node._on_open_viewer_click(MagicMock(), MagicMock())
        assert result is not None
        assert result.success is False

    def test_ocio_mode_without_color_params_fails_loudly(self, harness: _Harness, tmp_path: Path) -> None:
        from griptape_nodes_openexr.exr.ocio_helpers import COLOR_MODE_OCIO

        node = _make_node()
        node.set_parameter_value("path", str(_single_file(tmp_path)))
        node.set_parameter_value("color_mode", COLOR_MODE_OCIO)
        asyncio.run(node.aprocess())
        assert _was_successful(node) is False
        assert "color_params" in _result_details(node)


# ---------------------------------------------------------------------------
# Single file
# ---------------------------------------------------------------------------


class TestSingleFile:
    def test_emits_image_part_and_channels(self, harness: _Harness, tmp_path: Path) -> None:
        node = _make_node()
        node.set_parameter_value("path", str(_single_file(tmp_path)))
        asyncio.run(node.aprocess())
        assert _was_successful(node) is True, _result_details(node)
        assert isinstance(node.parameter_output_values["image"], ImageUrlArtifact)
        assert isinstance(node.parameter_output_values["part"], EXRPartArtifact)
        channels = node.parameter_output_values["channels"]
        assert channels and all(isinstance(channel, EXRChannelArtifact) for channel in channels)

    def test_header_scalars_match_the_header(self, harness: _Harness, tmp_path: Path) -> None:
        node = _make_node()
        node.set_parameter_value("path", str(_single_file(tmp_path)))
        asyncio.run(node.aprocess())
        assert node.parameter_output_values["image_width"] == WIDTH
        assert node.parameter_output_values["image_height"] == HEIGHT
        assert node.parameter_output_values["part_count"] == 1
        assert node.parameter_output_values["channel_count"] == 4

    def test_single_file_part_has_no_frame_number(self, harness: _Harness, tmp_path: Path) -> None:
        node = _make_node()
        node.set_parameter_value("path", str(_single_file(tmp_path)))
        asyncio.run(node.aprocess())
        assert node.parameter_output_values["part"].frame_number is None

    def test_single_file_output_filename_is_unstamped(self, harness: _Harness, tmp_path: Path) -> None:
        node = _make_node()
        node.set_parameter_value("path", str(_single_file(tmp_path)))
        asyncio.run(node.aprocess())
        assert harness.build_file_calls[-1].get("_index") is None


# ---------------------------------------------------------------------------
# Dropdowns
# ---------------------------------------------------------------------------


class TestDropdowns:
    def test_multi_part_populates_part_choices(self, harness: _Harness, tmp_path: Path) -> None:
        harness.exr_data = _make_exr_data(
            _make_part("rgba"),
            _make_part("depth", ("Z",)),
            _make_part("normal", ("X", "Y", "Z")),
        )
        node = _make_node()
        node.set_parameter_value("path", str(_single_file(tmp_path)))
        choices = node._part_choices()
        assert choices == ["0: rgba", "1: depth", "2: normal"]
        assert _param(node, "part_name").ui_options.get("hide") is not True

    def test_selected_part_is_the_one_decoded(self, harness: _Harness, tmp_path: Path) -> None:
        harness.exr_data = _make_exr_data(_make_part("rgba"), _make_part("depth", ("Z",)))
        harness.pixels = _make_pixels(("R", "G", "B", "A", "Z"))
        node = _make_node()
        node.set_parameter_value("path", str(_single_file(tmp_path)))
        node.set_parameter_value("part_name", "1: depth")
        asyncio.run(node.aprocess())
        assert _was_successful(node) is True, _result_details(node)
        assert node.parameter_output_values["part"].name == "depth"
        assert harness.load_calls[-1][1] == 1

    def test_layer_choices_come_from_channel_prefixes(self, harness: _Harness, tmp_path: Path) -> None:
        harness.exr_data = _make_exr_data(_make_part("", ("beauty.R", "beauty.G", "beauty.B", "depth.Z")))
        node = _make_node()
        node.set_parameter_value("path", str(_single_file(tmp_path)))
        assert node._layer_choices() == ["beauty", "depth"]

    def test_default_layer_matches_select_display_channels(self, harness: _Harness, tmp_path: Path) -> None:
        harness.exr_data = _make_exr_data(_make_part("", ("beauty.R", "beauty.G", "beauty.B", "depth.Z")))
        node = _make_node()
        node.set_parameter_value("path", str(_single_file(tmp_path)))
        assert node.get_parameter_value("layer") == "beauty"

    def test_single_layer_hides_the_layer_dropdown(self, harness: _Harness, tmp_path: Path) -> None:
        node = _make_node()
        node.set_parameter_value("path", str(_single_file(tmp_path)))
        assert node._layer_choices() == [ROOT_LAYER]
        assert _param(node, "layer").ui_options.get("hide") is True

    def test_chosen_layer_drives_the_decoded_channels(self, harness: _Harness, tmp_path: Path) -> None:
        channel_names = ("beauty.R", "beauty.G", "beauty.B", "diffuse.R", "diffuse.G", "diffuse.B")
        harness.exr_data = _make_exr_data(_make_part("", channel_names))
        harness.pixels = _make_pixels(channel_names)
        node = _make_node()
        node.set_parameter_value("path", str(_single_file(tmp_path)))
        node.set_parameter_value("layer", "diffuse")
        asyncio.run(node.aprocess())
        assert _was_successful(node) is True, _result_details(node)
        assert harness.load_calls[-1][2] == ["diffuse.R", "diffuse.G", "diffuse.B"]


# ---------------------------------------------------------------------------
# Sequences
# ---------------------------------------------------------------------------


class TestSequence:
    def test_bounds_outputs_are_populated(self, harness: _Harness, tmp_path: Path) -> None:
        paths = _frames(tmp_path, range(1001, 1006))
        harness.sequence = _make_sequence(paths, start=1001)
        node = _make_node()
        node.set_parameter_value("path", str(tmp_path / "frame.####.exr"))
        asyncio.run(node.aprocess())
        assert _was_successful(node) is True, _result_details(node)
        assert node.parameter_output_values["first_frame"] == 1001
        assert node.parameter_output_values["last_frame"] == 1005
        assert node.parameter_output_values["frame_count"] == 5

    def test_unset_frame_renders_the_first_entry(self, harness: _Harness, tmp_path: Path) -> None:
        paths = _frames(tmp_path, range(1001, 1006))
        harness.sequence = _make_sequence(paths, start=1001)
        node = _make_node()
        node.set_parameter_value("path", str(tmp_path / "frame.####.exr"))
        asyncio.run(node.aprocess())
        assert node.parameter_output_values["part"].frame_number == 1001
        assert harness.load_calls[-1][0] == str(paths[0])

    def test_named_frame_renders_that_entry(self, harness: _Harness, tmp_path: Path) -> None:
        paths = _frames(tmp_path, range(1001, 1201))
        harness.sequence = _make_sequence(paths, start=1001)
        node = _make_node()
        node.set_parameter_value("path", str(tmp_path / "frame.####.exr"))
        node.set_parameter_value("frame", 1050)
        asyncio.run(node.aprocess())
        assert _was_successful(node) is True, _result_details(node)
        assert node.parameter_output_values["part"].frame_number == 1050
        assert harness.load_calls[-1][0] == str(tmp_path / "frame.1050.exr")

    def test_out_of_range_frame_clamps_and_reports(self, harness: _Harness, tmp_path: Path) -> None:
        paths = _frames(tmp_path, range(1001, 1006))
        harness.sequence = _make_sequence(paths, start=1001)
        node = _make_node()
        node.set_parameter_value("path", str(tmp_path / "frame.####.exr"))
        node.set_parameter_value("frame", 9999)
        asyncio.run(node.aprocess())
        assert _was_successful(node) is True, _result_details(node)
        assert node.parameter_output_values["part"].frame_number == 1005
        assert "9999" in _result_details(node)
        assert "1005" in _result_details(node)

    def test_missing_frame_fails_naming_the_frame(self, harness: _Harness, tmp_path: Path) -> None:
        paths = _frames(tmp_path, range(1001, 1006))
        harness.sequence = _make_sequence(paths, start=1001, missing=[1003])
        node = _make_node()
        node.set_parameter_value("path", str(tmp_path / "frame.####.exr"))
        node.set_parameter_value("frame", 1003)
        asyncio.run(node.aprocess())
        assert _was_successful(node) is False
        assert "1003" in _result_details(node)

    def test_output_filename_carries_the_frame_number(self, harness: _Harness, tmp_path: Path) -> None:
        paths = _frames(tmp_path, range(1001, 1006))
        harness.sequence = _make_sequence(paths, start=1001)
        node = _make_node()
        node.set_parameter_value("path", str(tmp_path / "frame.####.exr"))
        node.set_parameter_value("frame", 1003)
        asyncio.run(node.aprocess())
        assert harness.build_file_calls[-1].get("_index") == 1003
        assert "1003" in node.parameter_output_values["image"].value

    def test_connected_sequence_wins_over_typed_path(self, harness: _Harness, tmp_path: Path) -> None:
        paths = _frames(tmp_path, range(1001, 1004))
        node = _make_node()
        node.set_parameter_value("path", _make_sequence(paths, start=1001))
        asyncio.run(node.aprocess())
        assert _was_successful(node) is True, _result_details(node)
        assert node.parameter_output_values["frame_count"] == 3
        # A Sequence value is used directly; no path scan is issued for it.
        assert harness.load_calls[-1][0] == str(paths[0])


# ---------------------------------------------------------------------------
# Caching and previews
# ---------------------------------------------------------------------------


class TestCaching:
    def test_aprocess_scans_on_a_cold_cache(self, harness: _Harness, tmp_path: Path) -> None:
        # No after_value_set first: this is the workflow-reload and worker case.
        node = _make_node()
        node.set_parameter_value("path", str(_single_file(tmp_path)), initial_setup=True)
        asyncio.run(node.aprocess())
        assert _was_successful(node) is True, _result_details(node)
        assert isinstance(node.parameter_output_values["image"], ImageUrlArtifact)

    def test_scrubbing_frame_reuses_the_scan(self, harness: _Harness, tmp_path: Path) -> None:
        paths = _frames(tmp_path, range(1001, 1006))
        harness.sequence = _make_sequence(paths, start=1001)
        node = _make_node()
        node.set_parameter_value("path", str(tmp_path / "frame.####.exr"))
        node.set_parameter_value("frame", 1002)
        asyncio.run(node.aprocess())
        scans_after_first = len(harness.scan_calls)
        loads_after_first = len(harness.load_calls)

        node.set_parameter_value("frame", 1003)
        asyncio.run(node.aprocess())
        assert len(harness.scan_calls) == scans_after_first
        assert len(harness.load_calls) == loads_after_first + 1

    def test_rerun_with_identical_inputs_does_not_redecode(self, harness: _Harness, tmp_path: Path) -> None:
        node = _make_node()
        node.set_parameter_value("path", str(_single_file(tmp_path)))
        asyncio.run(node.aprocess())
        loads = len(harness.load_calls)
        asyncio.run(node.aprocess())
        assert len(harness.load_calls) == loads

    def test_cache_hit_still_emits_the_outputs(self, harness: _Harness, tmp_path: Path) -> None:
        node = _make_node()
        node.set_parameter_value("path", str(_single_file(tmp_path)))
        asyncio.run(node.aprocess())
        loads = len(harness.load_calls)

        # The engine clears output values whenever it marks a node unresolved, which
        # happens between runs. A cache hit has to re-emit, not just report success.
        node.clear_node()
        asyncio.run(node.aprocess())

        assert len(harness.load_calls) == loads
        assert isinstance(node.parameter_output_values["image"], ImageUrlArtifact)
        assert isinstance(node.parameter_output_values["part"], EXRPartArtifact)
        assert node.parameter_output_values["channels"]
        assert node.parameter_output_values["image_width"] == WIDTH

    def test_changing_exposure_redecodes(self, harness: _Harness, tmp_path: Path) -> None:
        node = _make_node()
        node.set_parameter_value("path", str(_single_file(tmp_path)))
        asyncio.run(node.aprocess())
        loads = len(harness.load_calls)
        node.set_parameter_value("exposure", 2.0)
        asyncio.run(node.aprocess())
        assert len(harness.load_calls) == loads + 1

    def test_a_scan_failure_does_not_break_the_value_set(self, harness: _Harness, tmp_path: Path) -> None:
        with patch(f"{_NODE_MODULE}.scan_exr_header", side_effect=RuntimeError("corrupt header")):
            node = _make_node()
            node.set_parameter_value("path", str(_single_file(tmp_path)))
        assert node.get_parameter_value("path")


class TestPreview:
    def test_preview_requested_on_path_change(self, harness: _Harness, tmp_path: Path) -> None:
        path = _single_file(tmp_path)
        node = _make_node()
        node.set_parameter_value("path", str(path))
        assert len(harness.preview_requests) == 1
        request = harness.preview_requests[0]
        assert request.artifact_provider_name == "EXR"
        assert request.preview_generation_policy is PreviewGenerationPolicy.ONLY_IF_STALE
        assert request.macro_path.parsed_macro.template == str(path)

    def test_no_preview_when_frame_is_connection_driven(self, harness: _Harness, tmp_path: Path) -> None:
        harness.connected_params = {"frame"}
        node = _make_node()
        node.set_parameter_value("path", str(_single_file(tmp_path)))
        assert harness.preview_requests == []

    def test_preview_failure_degrades_to_no_preview(self, harness: _Harness, tmp_path: Path) -> None:
        harness.preview_succeeds = False
        node = _make_node()
        node.set_parameter_value("path", str(_single_file(tmp_path)))
        asyncio.run(node.aprocess())
        assert _was_successful(node) is True, _result_details(node)


class TestConnectionDrivenFrame:
    def test_out_of_range_connection_value_does_not_raise(self, harness: _Harness, tmp_path: Path) -> None:
        paths = _frames(tmp_path, range(1001, 1006))
        harness.sequence = _make_sequence(paths, start=1001)
        node = _make_node()
        node.set_parameter_value("path", str(tmp_path / "frame.####.exr"))
        # The slider range is sized from the scan; a loop can push past it.
        node.set_parameter_value("frame", 99999)
        assert node.get_parameter_value("frame") == 99999
