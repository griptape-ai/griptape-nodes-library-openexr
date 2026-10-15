"""Unit tests for LoadEXRSequence node."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from unittest.mock import MagicMock, patch

from griptape_nodes_openexr.exr.exr_header_artifact import EXRPartArtifact
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
MULTI_PART = DATA / "multi_part.exr"

_NODE_MODULE = "griptape_nodes_openexr.nodes.load_exr_sequence"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_sequence(paths: list[Path]) -> dict:
    """Build a minimal Sequence-shaped dict for wiring into the node."""
    entries = [{"number": i, "padded_number": f"{i:04d}", "path": str(p)} for i, p in enumerate(paths)]
    return {
        "entries": entries,
        "first": 0,
        "last": len(paths) - 1,
        "discovered_first": 0,
        "discovered_last": len(paths) - 1,
        "padding": 4,
        "pattern": "frame.####.exr",
        "directory": str(paths[0].parent) if paths else "",
        "policy": "skip",
        "dropped_negative_number_count": 0,
        "present_numbers": list(range(len(paths))),
        "missing_numbers": [],
    }


def _make_window(xmax: int = 15, ymax: int = 9) -> WindowCoordinates:
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


def _make_exr_data(width: int = 16, height: int = 10, channel_names: tuple[str, ...] = ("R", "G", "B", "A")) -> EXRData:
    channels = [
        EXRChannelInfo(name=name, pixel_type=PixelType.HALF, x_sampling=1, y_sampling=1) for name in channel_names
    ]
    part = EXRPart(name="", channels=channels, header=_make_header(), width=width, height=height)
    return EXRData(parts=[part])


def _make_node(metadata: dict | None = None):
    from griptape_nodes_openexr.nodes.load_exr_sequence import LoadEXRSequence

    return LoadEXRSequence("test_node", metadata=metadata)


def _was_successful(node) -> bool | None:
    return node.parameter_output_values.get("was_successful")


def _result_details(node) -> str:
    return node.parameter_output_values.get("result_details", "") or ""


def _parts(node) -> list[EXRPartArtifact]:
    value = node.parameter_output_values.get("parts")
    assert value is not None
    return value


# ---------------------------------------------------------------------------
# Node instantiation and parameter registration
# ---------------------------------------------------------------------------


class TestNodeInit:
    def test_node_can_be_instantiated(self) -> None:
        node = _make_node()
        assert node is not None

    def test_sequence_parameter_exists(self) -> None:
        node = _make_node()
        assert node.get_parameter_by_name("sequence") is not None

    def test_part_index_default_is_zero(self) -> None:
        node = _make_node()
        assert node.get_parameter_value("part_index") == 0

    def test_threads_parameter_exists_with_default_4(self) -> None:
        node = _make_node()
        assert node.get_parameter_value("threads") == 4

    def test_parts_output_parameter_exists(self) -> None:
        node = _make_node()
        assert node.get_parameter_by_name("parts") is not None

    def test_frame_count_output_parameter_exists(self) -> None:
        node = _make_node()
        assert node.get_parameter_by_name("frame_count") is not None


# ---------------------------------------------------------------------------
# aprocess — no input
# ---------------------------------------------------------------------------


class TestAProcessNoInput:
    def test_fails_gracefully_when_no_sequence(self) -> None:
        node = _make_node()
        asyncio.run(node.aprocess())
        assert _was_successful(node) is False

    def test_fails_gracefully_when_sequence_empty(self) -> None:
        node = _make_node()
        node.set_parameter_value("sequence", _make_sequence([]))
        asyncio.run(node.aprocess())
        assert _was_successful(node) is False


# ---------------------------------------------------------------------------
# aprocess — full pipeline (mocked scan_exr_header, no real file I/O)
# ---------------------------------------------------------------------------


class TestAProcessPipeline:
    def _run_with_sequence(self, paths: list[Path], *, part_index: int = 0, threads: int = 2, scan_side_effect=None):
        node = _make_node()
        node.set_parameter_value("sequence", _make_sequence(paths))
        node.set_parameter_value("part_index", part_index)
        node.set_parameter_value("threads", threads)

        def _default_scan_side_effect(_path, **_kwargs):
            return _make_exr_data()

        effect = scan_side_effect if scan_side_effect is not None else _default_scan_side_effect

        with (
            patch(f"{_NODE_MODULE}.GriptapeNodes") as mock_gn,
            patch(f"{_NODE_MODULE}.scan_exr_header", side_effect=effect) as mock_scan,
        ):
            mock_gn.handle_request.return_value = MagicMock(value=True)
            asyncio.run(node.aprocess())

        return node, mock_scan

    def test_succeeds_with_consistent_sequence(self, tmp_path: Path) -> None:
        paths = [tmp_path / f"frame.{i:04d}.exr" for i in range(3)]
        node, _ = self._run_with_sequence(paths)
        assert _was_successful(node) is True

    def test_frame_count_matches_sequence_length(self, tmp_path: Path) -> None:
        paths = [tmp_path / f"frame.{i:04d}.exr" for i in range(5)]
        node, _ = self._run_with_sequence(paths)
        assert node.parameter_output_values.get("frame_count") == 5

    def test_parts_output_ordered_by_frame_number(self, tmp_path: Path) -> None:
        paths = [tmp_path / f"frame.{i:04d}.exr" for i in range(4)]
        node, _ = self._run_with_sequence(paths)
        parts = _parts(node)
        assert [p.file_path for p in parts] == [str(p) for p in paths]

    def test_parts_are_exr_part_artifacts(self, tmp_path: Path) -> None:
        paths = [tmp_path / f"frame.{i:04d}.exr" for i in range(2)]
        node, _ = self._run_with_sequence(paths)
        parts = _parts(node)
        assert all(isinstance(p, EXRPartArtifact) for p in parts)

    def test_part_index_is_passed_to_each_scan(self, tmp_path: Path) -> None:
        paths = [tmp_path / f"frame.{i:04d}.exr" for i in range(2)]
        node, _ = self._run_with_sequence(paths, part_index=0)
        parts = _parts(node)
        assert all(p.part_index == 0 for p in parts)

    def test_header_only_setting_is_respected(self, tmp_path: Path) -> None:
        from griptape_nodes.retained_mode.events.config_events import GetConfigValueResultSuccess

        paths = [tmp_path / "frame.0000.exr"]
        node = _make_node()
        node.set_parameter_value("sequence", _make_sequence(paths))

        with (
            patch(f"{_NODE_MODULE}.GriptapeNodes") as mock_gn,
            patch(f"{_NODE_MODULE}.scan_exr_header", return_value=_make_exr_data()) as mock_scan,
        ):
            mock_gn.handle_request.return_value = GetConfigValueResultSuccess(value=False, result_details="")
            asyncio.run(node.aprocess())

        assert mock_scan.call_args.kwargs["header_only"] is False

    def test_out_of_range_part_index_fails_the_run(self, tmp_path: Path) -> None:
        paths = [tmp_path / f"frame.{i:04d}.exr" for i in range(2)]
        node, _ = self._run_with_sequence(paths, part_index=5)
        assert _was_successful(node) is False
        assert "part_index 5" in _result_details(node)
        assert node.parameter_output_values.get("parts") is None

    def test_negative_part_index_clamps_to_zero_via_ui(self, tmp_path: Path) -> None:
        """set_parameter_value goes through the param's Clamp(min_val=0) trait."""
        node = _make_node()
        node.set_parameter_value("part_index", -1)
        assert node.get_parameter_value("part_index") == 0

    def test_negative_part_index_is_rejected_at_runtime(self, tmp_path: Path) -> None:
        """A value that bypasses the UI clamp (e.g. a deserialized workflow) is still rejected."""
        paths = [tmp_path / "frame.0000.exr"]
        node = _make_node()
        node.set_parameter_value("sequence", _make_sequence(paths))
        node.set_parameter_value("threads", 2)
        node.parameter_values["part_index"] = -1

        with (
            patch(f"{_NODE_MODULE}.GriptapeNodes") as mock_gn,
            patch(f"{_NODE_MODULE}.scan_exr_header", return_value=_make_exr_data()),
        ):
            mock_gn.handle_request.return_value = MagicMock(value=True)
            asyncio.run(node.aprocess())

        assert _was_successful(node) is False
        assert "part_index -1" in _result_details(node)

    def test_mismatched_resolution_mid_sequence_is_skipped_not_fatal(self, tmp_path: Path) -> None:
        paths = [tmp_path / f"frame.{i:04d}.exr" for i in range(3)]

        def scan_side_effect(path, **_kwargs):
            if str(path) == str(paths[1]):
                return _make_exr_data(width=32, height=10)
            return _make_exr_data()

        node, _ = self._run_with_sequence(paths, scan_side_effect=scan_side_effect)
        assert _was_successful(node) is True
        parts = _parts(node)
        assert [p.file_path for p in parts] == [str(paths[0]), str(paths[2])]
        assert "1 frame error" in _result_details(node)

    def test_mismatched_channels_mid_sequence_is_skipped_not_fatal(self, tmp_path: Path) -> None:
        paths = [tmp_path / f"frame.{i:04d}.exr" for i in range(3)]

        def scan_side_effect(path, **_kwargs):
            if str(path) == str(paths[1]):
                return _make_exr_data(channel_names=("Y",))
            return _make_exr_data()

        node, _ = self._run_with_sequence(paths, scan_side_effect=scan_side_effect)
        assert _was_successful(node) is True
        parts = _parts(node)
        assert [p.file_path for p in parts] == [str(paths[0]), str(paths[2])]
        assert "1 frame error" in _result_details(node)

    def test_scan_exception_is_a_per_frame_error_not_a_crash(self, tmp_path: Path) -> None:
        paths = [tmp_path / f"frame.{i:04d}.exr" for i in range(3)]

        def scan_side_effect(path, **_kwargs):
            if str(path) == str(paths[0]):
                msg = "boom"
                raise RuntimeError(msg)
            return _make_exr_data()

        node, _ = self._run_with_sequence(paths, scan_side_effect=scan_side_effect)
        assert _was_successful(node) is True
        parts = _parts(node)
        assert [p.file_path for p in parts] == [str(paths[1]), str(paths[2])]
        assert "1 frame error" in _result_details(node)

    def test_all_frames_failing_reports_failure(self, tmp_path: Path) -> None:
        paths = [tmp_path / f"frame.{i:04d}.exr" for i in range(3)]

        def scan_side_effect(_path, **_kwargs):
            msg = "unreadable"
            raise RuntimeError(msg)

        node, _ = self._run_with_sequence(paths, scan_side_effect=scan_side_effect)
        assert _was_successful(node) is False
        assert node.parameter_output_values.get("parts") is None
        assert "unreadable" in _result_details(node)

    def test_outlier_first_frame_does_not_reject_the_rest_of_the_sequence(self, tmp_path: Path) -> None:
        paths = [tmp_path / f"frame.{i:04d}.exr" for i in range(4)]

        def scan_side_effect(path, **_kwargs):
            if str(path) == str(paths[0]):
                return _make_exr_data(width=4, height=4)
            return _make_exr_data()

        node, _ = self._run_with_sequence(paths, scan_side_effect=scan_side_effect)
        assert _was_successful(node) is True
        parts = _parts(node)
        assert [p.file_path for p in parts] == [str(paths[1]), str(paths[2]), str(paths[3])]
        assert "1 frame error" in _result_details(node)

    def test_no_majority_layout_fails_the_run(self, tmp_path: Path) -> None:
        paths = [tmp_path / f"frame.{i:04d}.exr" for i in range(2)]

        def scan_side_effect(path, **_kwargs):
            if str(path) == str(paths[0]):
                return _make_exr_data(width=4, height=4)
            return _make_exr_data(width=8, height=8)

        node, _ = self._run_with_sequence(paths, scan_side_effect=scan_side_effect)
        assert _was_successful(node) is False
        assert "no consistent part layout" in _result_details(node)


# ---------------------------------------------------------------------------
# Manifest registration
# ---------------------------------------------------------------------------


class TestManifestRegistration:
    def test_node_is_registered_in_manifest(self) -> None:
        manifest_path = Path(__file__).parents[2] / "griptape-nodes-library.json"
        assert manifest_path.exists(), f"Manifest not found at {manifest_path}"
        manifest = json.loads(manifest_path.read_text())
        class_names = [n["class_name"] for n in manifest.get("nodes", [])]
        assert "LoadEXRSequence" in class_names

    def test_manifest_entry_has_correct_file_path(self) -> None:
        manifest_path = Path(__file__).parents[2] / "griptape-nodes-library.json"
        manifest = json.loads(manifest_path.read_text())
        entry = next(n for n in manifest["nodes"] if n["class_name"] == "LoadEXRSequence")
        assert "load_exr_sequence" in entry["file_path"]
