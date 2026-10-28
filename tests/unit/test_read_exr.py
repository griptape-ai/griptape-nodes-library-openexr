"""Unit tests for the ReadEXR node."""

from __future__ import annotations

import asyncio
import threading
import time
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
from griptape_nodes_openexr.nodes.read_exr import _DEFAULT_FRAME_MAX, _DEFAULT_FRAME_MIN

DATA = Path(__file__).parents[1] / "data"
SINGLE_PART_RGBA = DATA / "single_part_rgba.exr"

_NODE_MODULE = "griptape_nodes_openexr.nodes.read_exr"
# Decode and colour management live in the shared rendering helper, so that is
# where they are patched from.
_RENDER_MODULE = "griptape_nodes_openexr.exr.rendering"
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
        self.exr_data_by_path: dict[str, EXRData] = {}
        self.pixels = _make_pixels()
        self.sequence: dict | None = None
        self.connected_params: set[str] = set()
        self.preview_succeeds = True
        self.scan_calls: list[str] = []
        self.load_calls: list[tuple[str, int, list[str]]] = []
        self.scan_threads: list[int] = []
        self.load_threads: list[int] = []
        self.preview_threads: list[int] = []
        self.scan_delay = 0.0
        self.scan_delay_by_path: dict[str, float] = {}
        self.preview_requests: list[GetPreviewForArtifactRequest] = []
        self.build_file_calls: list[dict[str, Any]] = []
        self.written: list[Path] = []

    # -- stubs standing in for the EXR reader --

    def scan_exr_header(self, file_path: str, *, header_only: bool = True) -> EXRData:  # noqa: ARG002
        self.scan_calls.append(file_path)
        self.scan_threads.append(threading.get_ident())
        delay = self.scan_delay_by_path.get(file_path, self.scan_delay)
        if delay:
            time.sleep(delay)
        return self.exr_data_by_path.get(file_path, self.exr_data)

    def load_exr_channels(self, file_path: str, part_index: int, channel_names: list[str]) -> dict[str, np.ndarray]:
        self.load_calls.append((file_path, part_index, list(channel_names)))
        self.load_threads.append(threading.get_ident())
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
            self.preview_threads.append(threading.get_ident())
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
        patch(f"{_RENDER_MODULE}.load_exr_channels", side_effect=bench.load_exr_channels),
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


def _passthrough_ocio(rgb, color_params, tone_mapping):  # noqa: ANN001, ANN202, ARG001
    """Stand-in for apply_color_management whose output depends on the artifact's display."""
    return rgb, f"ocio:{getattr(color_params, 'display', '?')}"


def _layer_options(node) -> list[str]:
    from griptape_nodes.traits.options import Options

    return [trait.choices for trait in node._layer_param.find_elements_by_type(Options)][0]


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


class TestChannelOrder:
    def test_channels_are_emitted_in_rgba_order(self, harness: _Harness, tmp_path: Path) -> None:
        # EXR stores channels alphabetically. #52 fixed LoadEXR to list them R, G, B, A;
        # ReadEXR's channels output is the same listing and follows the same rule.
        harness.exr_data = _make_exr_data(_make_part("", ("A", "B", "G", "R")))
        harness.pixels = _make_pixels(("A", "B", "G", "R"))
        node = _make_node()
        node.set_parameter_value("path", str(_single_file(tmp_path)))
        asyncio.run(node.aprocess())

        assert _was_successful(node) is True, _result_details(node)
        emitted = [channel.channel.name for channel in node.parameter_output_values["channels"]]
        assert emitted == ["R", "G", "B", "A"]

    def test_layers_are_grouped_in_the_channels_output(self, harness: _Harness, tmp_path: Path) -> None:
        names = ("A", "B", "G", "R", "depth.Z")
        harness.exr_data = _make_exr_data(_make_part("", names))
        harness.pixels = _make_pixels(names)
        node = _make_node()
        node.set_parameter_value("path", str(_single_file(tmp_path)))
        asyncio.run(node.aprocess())

        emitted = [channel.channel.name for channel in node.parameter_output_values["channels"]]
        assert emitted == ["R", "G", "B", "A", "depth.Z"]


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


class TestPartLayerCoupling:
    """Regression: the layer dropdown has to follow part_name, not stay on part 0."""

    @staticmethod
    def _two_part() -> EXRData:
        return _make_exr_data(
            _make_part("beauty", ("R", "G", "B")),
            _make_part("aovs", ("diffuse.R", "diffuse.G", "diffuse.B", "spec.R", "spec.G", "spec.B")),
        )

    def _node_on_two_part(self, harness: _Harness, tmp_path: Path):
        harness.exr_data = self._two_part()
        harness.pixels = _make_pixels(
            ("R", "G", "B", "diffuse.R", "diffuse.G", "diffuse.B", "spec.R", "spec.G", "spec.B")
        )
        node = _make_node()
        node.set_parameter_value("path", str(_single_file(tmp_path)))
        return node

    def test_layer_choices_refresh_when_part_changes(self, harness: _Harness, tmp_path: Path) -> None:
        node = self._node_on_two_part(harness, tmp_path)
        node.set_parameter_value("part_name", "1: aovs")
        assert _layer_options(node) == ["diffuse", "spec"]

    def test_layer_dropdown_unhides_when_the_new_part_has_layers(self, harness: _Harness, tmp_path: Path) -> None:
        node = self._node_on_two_part(harness, tmp_path)
        # Part 0 has a single (root) layer, so the dropdown starts hidden.
        assert _param(node, "layer").ui_options.get("hide") is True
        node.set_parameter_value("part_name", "1: aovs")
        assert _param(node, "layer").ui_options.get("hide") is not True

    def test_a_layer_of_the_new_part_is_selectable(self, harness: _Harness, tmp_path: Path) -> None:
        node = self._node_on_two_part(harness, tmp_path)
        node.set_parameter_value("part_name", "1: aovs")
        node.set_parameter_value("layer", "spec")
        # Without refreshed choices the Options converter rewrites this to part 0's layer.
        assert node.get_parameter_value("layer") == "spec"

    def test_the_selected_layer_is_the_one_decoded(self, harness: _Harness, tmp_path: Path) -> None:
        node = self._node_on_two_part(harness, tmp_path)
        node.set_parameter_value("part_name", "1: aovs")
        node.set_parameter_value("layer", "spec")
        asyncio.run(node.aprocess())
        assert _was_successful(node) is True, _result_details(node)
        assert harness.load_calls[-1][2] == ["spec.R", "spec.G", "spec.B"]

    def test_layer_resets_to_the_new_parts_default(self, harness: _Harness, tmp_path: Path) -> None:
        node = self._node_on_two_part(harness, tmp_path)
        node.set_parameter_value("part_name", "1: aovs")
        # (root) does not exist on part 1, so it must fall back rather than persist.
        assert node.get_parameter_value("layer") in ("diffuse", "spec")

    def test_changing_part_does_not_rescan(self, harness: _Harness, tmp_path: Path) -> None:
        node = self._node_on_two_part(harness, tmp_path)
        scans = len(harness.scan_calls)
        node.set_parameter_value("part_name", "1: aovs")
        assert len(harness.scan_calls) == scans


class TestRestoreBeforeScan:
    """Regression: a saved value must survive being replayed before any scan has run."""

    def test_setting_part_name_before_a_scan_does_not_raise(self, harness: _Harness) -> None:
        node = _make_node()
        node.set_parameter_value("part_name", "1: aovs", initial_setup=True)

    def test_setting_layer_before_a_scan_does_not_raise(self, harness: _Harness) -> None:
        node = _make_node()
        node.set_parameter_value("layer", "spec", initial_setup=True)

    def test_a_restored_part_name_survives_the_scan(self, harness: _Harness, tmp_path: Path) -> None:
        harness.exr_data = _make_exr_data(_make_part("beauty", ("R", "G", "B")), _make_part("aovs", ("Z",)))
        harness.pixels = _make_pixels(("R", "G", "B", "Z"))
        node = _make_node()
        # Workflow load order: values replayed with hooks suppressed, then execution scans.
        node.set_parameter_value("part_name", "1: aovs", initial_setup=True)
        node.set_parameter_value("path", str(_single_file(tmp_path)), initial_setup=True)
        asyncio.run(node.aprocess())

        assert _was_successful(node) is True, _result_details(node)
        assert node.get_parameter_value("part_name") == "1: aovs"
        assert node.parameter_output_values["part"].name == "aovs"


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

    def test_a_rescanned_sequence_with_more_frames_is_not_served_from_the_stale_cache(
        self, harness: _Harness, tmp_path: Path
    ) -> None:
        # Same pattern and directory as an earlier scan, but upstream has since picked
        # up more rendered frames — the cache key must not be the bare pattern alone.
        paths = _frames(tmp_path, range(1001, 1006))
        node = _make_node()
        node.set_parameter_value("path", _make_sequence(paths[:3], start=1001))
        node.set_parameter_value("frame", 1005)
        asyncio.run(node.aprocess())
        assert node.parameter_output_values["last_frame"] == 1003

        node.set_parameter_value("path", _make_sequence(paths, start=1001))
        asyncio.run(node.aprocess())
        assert _was_successful(node) is True, _result_details(node)
        assert node.parameter_output_values["last_frame"] == 1005
        assert node.parameter_output_values["part"].frame_number == 1005


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
        # The sequence scan is reused; only the new frame's own header is read.
        assert harness.scan_calls[scans_after_first:] == [str(paths[2])]
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


class TestEventLoopNotBlocked:
    """Regression: EXR decode and the blocking preview request must stay off the loop."""

    @pytest.mark.asyncio
    async def test_aprocess_decode_runs_off_the_event_loop(self, harness: _Harness, tmp_path: Path) -> None:
        loop_thread = threading.get_ident()
        node = _make_node()
        node.set_parameter_value("path", str(_single_file(tmp_path)))
        await node.aprocess()

        assert _was_successful(node) is True, _result_details(node)
        assert harness.load_threads, "load_exr_channels was never called"
        assert loop_thread not in harness.load_threads, "channel decode ran on the event loop thread"

    @pytest.mark.asyncio
    async def test_aprocess_scan_runs_off_the_event_loop(self, harness: _Harness, tmp_path: Path) -> None:
        loop_thread = threading.get_ident()
        node = _make_node()
        # initial_setup skips after_value_set, so aprocess does the scan itself.
        node.set_parameter_value("path", str(_single_file(tmp_path)), initial_setup=True)
        await node.aprocess()

        assert harness.scan_threads, "scan_exr_header was never called"
        assert loop_thread not in harness.scan_threads, "header scan ran on the event loop thread"

    @pytest.mark.asyncio
    async def test_value_set_scan_and_preview_run_off_the_event_loop(self, harness: _Harness, tmp_path: Path) -> None:
        loop_thread = threading.get_ident()
        node = _make_node()
        node.set_parameter_value("path", str(_single_file(tmp_path)))
        await node.wait_for_pending_scan()

        assert harness.scan_threads, "scan_exr_header was never called"
        assert loop_thread not in harness.scan_threads, "header scan ran on the event loop thread"
        assert harness.preview_threads, "no preview was requested"
        assert loop_thread not in harness.preview_threads, "preview request blocked the event loop thread"

    @pytest.mark.asyncio
    async def test_value_set_scan_is_adopted_on_the_event_loop(self, harness: _Harness, tmp_path: Path) -> None:
        # Only the blocking calls go to a worker. Node state is written on the loop,
        # where it cannot interleave with a part_name or path change.
        loop_thread = threading.get_ident()
        node = _make_node()
        adopt_threads: list[int] = []
        original = node._adopt_scan

        def _recording_adopt(*args: Any) -> None:
            adopt_threads.append(threading.get_ident())
            original(*args)

        with patch.object(node, "_adopt_scan", side_effect=_recording_adopt):
            node.set_parameter_value("path", str(_single_file(tmp_path)))
            await node.wait_for_pending_scan()

        assert adopt_threads == [loop_thread]

    @pytest.mark.asyncio
    async def test_value_set_returns_before_the_scan_finishes(self, harness: _Harness, tmp_path: Path) -> None:
        # The point of going off-loop: the setter hands control back rather than
        # holding it for the length of a 4K decode.
        harness.scan_delay = 0.25
        node = _make_node()

        start = time.perf_counter()
        node.set_parameter_value("path", str(_single_file(tmp_path)))
        elapsed = time.perf_counter() - start

        assert elapsed < harness.scan_delay / 2, f"set_parameter_value blocked for {elapsed:.3f}s"
        await node.wait_for_pending_scan()
        assert harness.scan_calls

    def test_value_set_without_a_running_loop_still_scans(self, harness: _Harness, tmp_path: Path) -> None:
        # Bootstrap and worker threads set values with no loop running; the scan has
        # to happen inline there rather than being dropped.
        node = _make_node()
        node.set_parameter_value("path", str(_single_file(tmp_path)))
        assert harness.scan_calls
        assert node._part_choices() == ["0: part"]


class TestColorParamsCacheKey:
    """Regression: an OCIO artifact mutated in place must invalidate the render cache."""

    @staticmethod
    def _params(display: str = "sRGB"):
        params = MagicMock()
        params.source_colorspace = "ACEScg"
        params.display = display
        params.view = "ACES"
        params.config_path = None
        return params

    def _node_with_ocio(self, harness: _Harness, tmp_path: Path, params):
        node = _make_node(ocio_available=True)
        node.set_parameter_value("path", str(_single_file(tmp_path)))
        node.set_parameter_value("color_mode", "ocio")
        node.set_parameter_value("color_params", params)
        return node

    def test_mutating_the_artifact_in_place_rerenders(self, harness: _Harness, tmp_path: Path) -> None:
        params = self._params()
        node = self._node_with_ocio(harness, tmp_path, params)
        with patch(f"{_RENDER_MODULE}.apply_color_management", side_effect=_passthrough_ocio) as ocio:
            asyncio.run(node.aprocess())
            assert _was_successful(node) is True, _result_details(node)
            renders = ocio.call_count

            # An upstream node re-emits the same object with a different display.
            params.display = "Rec709"
            asyncio.run(node.aprocess())

            assert ocio.call_count == renders + 1, "cache hit served a stale image"

    def test_an_unchanged_artifact_still_hits_the_cache(self, harness: _Harness, tmp_path: Path) -> None:
        node = self._node_with_ocio(harness, tmp_path, self._params())
        with patch(f"{_RENDER_MODULE}.apply_color_management", side_effect=_passthrough_ocio) as ocio:
            asyncio.run(node.aprocess())
            renders = ocio.call_count
            asyncio.run(node.aprocess())
            assert ocio.call_count == renders


class TestConcurrentPathChanges:
    """Regression: a slow scan finishing late must not clobber a newer path."""

    @pytest.mark.asyncio
    async def test_the_last_path_set_wins(self, harness: _Harness, tmp_path: Path) -> None:
        slow = _single_file(tmp_path)
        fast = tmp_path / "second.exr"
        fast.write_bytes(b"")

        harness.scan_delay_by_path = {str(slow): 0.25}
        node = _make_node()
        node.set_parameter_value("path", str(slow))
        await asyncio.sleep(0.02)
        node.set_parameter_value("path", str(fast))

        await node.wait_for_pending_scan()
        await asyncio.sleep(0.4)

        assert node._scan is not None
        assert node._scan.key == str(fast)

    @pytest.mark.asyncio
    async def test_a_superseded_scan_does_not_render_from_the_old_file(self, harness: _Harness, tmp_path: Path) -> None:
        slow = _single_file(tmp_path)
        fast = tmp_path / "second.exr"
        fast.write_bytes(b"")

        harness.scan_delay_by_path = {str(slow): 0.25}
        node = _make_node()
        node.set_parameter_value("path", str(slow))
        await asyncio.sleep(0.02)
        node.set_parameter_value("path", str(fast))
        await node.wait_for_pending_scan()
        await asyncio.sleep(0.4)
        await node.aprocess()

        assert _was_successful(node) is True, _result_details(node)
        assert node.parameter_output_values["part"].file_path == str(fast)


class TestFailureClearsOutputs:
    """Regression: a failed read must not leave the previous run's info outputs published."""

    def _node_on_gappy_sequence(self, harness: _Harness, tmp_path: Path):
        paths = _frames(tmp_path, range(1001, 1004))
        harness.sequence = _make_sequence(paths, start=1001, missing=[1002])
        node = _make_node()
        node.set_parameter_value("path", str(tmp_path / "frame.####.exr"))
        return node

    def test_info_outputs_are_cleared_on_failure(self, harness: _Harness, tmp_path: Path) -> None:
        node = self._node_on_gappy_sequence(harness, tmp_path)
        asyncio.run(node.aprocess())
        assert _was_successful(node) is True, _result_details(node)

        node.set_parameter_value("frame", 1002)
        asyncio.run(node.aprocess())

        assert _was_successful(node) is False
        for name in ("image_width", "image_height", "part_count", "channel_count"):
            assert not node.parameter_output_values.get(name), f"{name} kept a stale value"

    def test_sequence_bounds_are_cleared_on_failure(self, harness: _Harness, tmp_path: Path) -> None:
        node = self._node_on_gappy_sequence(harness, tmp_path)
        asyncio.run(node.aprocess())
        node.set_parameter_value("frame", 1002)
        asyncio.run(node.aprocess())

        for name in ("first_frame", "last_frame", "frame_count"):
            assert not node.parameter_output_values.get(name), f"{name} kept a stale value"


class TestFrameRangeReset:
    def test_a_single_file_resets_the_slider_range(self, harness: _Harness, tmp_path: Path) -> None:
        paths = _frames(tmp_path, range(1001, 1006))
        harness.sequence = _make_sequence(paths, start=1001)
        node = _make_node()
        node.set_parameter_value("path", str(tmp_path / "frame.####.exr"))
        assert node._frame_param.min_val == 1001

        harness.sequence = _make_sequence([_single_file(tmp_path)], start=1)
        node.set_parameter_value("path", str(_single_file(tmp_path)))

        # The parameter is hidden for a single file, so this is cosmetic, but leaving
        # the old sequence's range on it is just stale state.
        assert node._frame_param.min_val == _DEFAULT_FRAME_MIN
        assert node._frame_param.max_val == _DEFAULT_FRAME_MAX


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


class TestPerFrameHeader:
    """Regression: each frame is read against its own header, not frame 1's."""

    def _node_on_sequence(self, harness: _Harness, tmp_path: Path):
        paths = _frames(tmp_path, range(1001, 1003))
        harness.sequence = _make_sequence(paths, start=1001)
        node = _make_node()
        node.set_parameter_value("path", str(tmp_path / "frame.####.exr"))
        return node, paths

    def test_a_frame_that_drops_a_part_fails_rather_than_reading_the_next(
        self, harness: _Harness, tmp_path: Path
    ) -> None:
        harness.exr_data = _make_exr_data(_make_part("beauty"), _make_part("diffuse"), _make_part("specular"))
        node, paths = self._node_on_sequence(harness, tmp_path)
        harness.exr_data_by_path[str(paths[1])] = _make_exr_data(_make_part("beauty"), _make_part("specular"))
        node.set_parameter_value("part_name", "1: diffuse")
        node.set_parameter_value("frame", 1002)
        asyncio.run(node.aprocess())

        assert _was_successful(node) is False
        assert "1: diffuse" in _result_details(node)
        assert not harness.load_calls

    def test_a_frame_missing_the_selected_layer_fails(self, harness: _Harness, tmp_path: Path) -> None:
        harness.exr_data = _make_exr_data(_make_part(channel_names=("R", "G", "B", "crypto.R")))
        node, paths = self._node_on_sequence(harness, tmp_path)
        harness.exr_data_by_path[str(paths[1])] = _make_exr_data(_make_part(channel_names=("R", "G", "B")))
        node.set_parameter_value("layer", "crypto")
        node.set_parameter_value("frame", 1002)
        asyncio.run(node.aprocess())

        assert _was_successful(node) is False
        assert "crypto" in _result_details(node)
        assert not harness.load_calls


class TestFrameValueGuard:
    """Regression: a frame value that is not a number must fail the node, not the run."""

    def test_a_non_numeric_frame_fails_cleanly(self, harness: _Harness, tmp_path: Path) -> None:
        # Written straight into parameter_values, which is the one path that skips the
        # ParameterInt converter. That is how the engine materialises hydrated values,
        # so it is the shape a future direct writer would take, not a connection.
        paths = _frames(tmp_path, range(1001, 1006))
        harness.sequence = _make_sequence(paths, start=1001)
        node = _make_node()
        node.set_parameter_value("path", str(tmp_path / "frame.####.exr"))
        node.parameter_values["frame"] = "not-a-frame"

        asyncio.run(node.aprocess())

        assert _was_successful(node) is False
        assert "not-a-frame" in _result_details(node)
