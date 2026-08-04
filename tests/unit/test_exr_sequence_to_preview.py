"""Unit tests for EXRSequenceToPreview node."""

from __future__ import annotations

import asyncio
import json
import shutil
from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from griptape_nodes_openexr.exr.ocio_helpers import COLOR_MODE_BASIC, COLOR_MODE_OCIO
from griptape_nodes_openexr.exr.tone_mapping import TONE_FILMIC

DATA = Path(__file__).parents[1] / "data"
SINGLE_PART_RGBA = DATA / "single_part_rgba.exr"

_NODE_MODULE = "griptape_nodes_openexr.nodes.exr_sequence_to_preview"
_OCIO_MODULE = "griptape_nodes_openexr.exr.ocio_helpers"


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


def _make_node(ocio_available: bool = False, metadata: dict | None = None):
    from griptape_nodes_openexr.nodes.exr_sequence_to_preview import EXRSequenceToPreview

    detected = MagicMock() if ocio_available else None
    with patch(f"{_OCIO_MODULE}.find_colorspace_transform_request_type", return_value=detected):
        return EXRSequenceToPreview("test_node", metadata=metadata)


def _was_successful(node) -> bool | None:
    return node.parameter_output_values.get("was_successful")


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

    def test_frame_rate_default_is_24(self) -> None:
        node = _make_node()
        assert node.get_parameter_value("frame_rate") == 24.0

    def test_part_index_default_is_zero(self) -> None:
        node = _make_node()
        assert node.get_parameter_value("part_index") == 0

    def test_exposure_default_is_zero(self) -> None:
        node = _make_node()
        assert node.get_parameter_value("exposure") == 0.0

    def test_threads_parameter_exists_with_default_4(self) -> None:
        node = _make_node()
        assert node.get_parameter_value("threads") == 4

    def test_video_output_parameter_exists(self) -> None:
        node = _make_node()
        assert node.get_parameter_by_name("video") is not None

    def test_frame_count_output_parameter_exists(self) -> None:
        node = _make_node()
        assert node.get_parameter_by_name("frame_count") is not None

    def test_frames_processed_output_parameter_exists(self) -> None:
        node = _make_node()
        assert node.get_parameter_by_name("frames_processed") is not None

    def test_format_default_is_mp4(self) -> None:
        node = _make_node()
        assert node.get_parameter_value("format") == "mp4"

    def test_processing_speed_default_is_balanced(self) -> None:
        node = _make_node()
        assert node.get_parameter_value("processing_speed") == "balanced"


# ---------------------------------------------------------------------------
# Color mode visibility — same pattern as DisplayEXRPart
# ---------------------------------------------------------------------------


class TestInitialVisibility:
    def test_no_ocio_starts_in_basic_mode(self) -> None:
        with patch(f"{_OCIO_MODULE}.apply_color_mode_visibility") as mock_vis:
            _make_node(ocio_available=False)
        mock_vis.assert_called_once()
        assert mock_vis.call_args[0][1] is False

    def test_with_ocio_starts_in_ocio_mode(self) -> None:
        with patch(f"{_OCIO_MODULE}.apply_color_mode_visibility") as mock_vis:
            _make_node(ocio_available=True)
        mock_vis.assert_called_once()
        assert mock_vis.call_args[0][1] is True

    def test_reload_with_saved_basic_uses_basic_even_when_ocio_available(self) -> None:
        with patch(f"{_OCIO_MODULE}.apply_color_mode_visibility") as mock_vis:
            _make_node(ocio_available=True, metadata={"_color_mode": COLOR_MODE_BASIC})
        mock_vis.assert_called_once()
        assert mock_vis.call_args[0][1] is False

    def test_reload_with_saved_ocio_uses_ocio_even_when_ocio_unavailable(self) -> None:
        with patch(f"{_OCIO_MODULE}.apply_color_mode_visibility") as mock_vis:
            _make_node(ocio_available=False, metadata={"_color_mode": COLOR_MODE_OCIO})
        mock_vis.assert_called_once()
        assert mock_vis.call_args[0][1] is True


class TestAfterValueSetColorMode:
    def test_switching_to_basic_persists_metadata(self) -> None:
        node = _make_node()
        node.after_value_set(node._color_mode_param, COLOR_MODE_BASIC)
        assert node.metadata["_color_mode"] == COLOR_MODE_BASIC

    def test_switching_to_ocio_persists_metadata(self) -> None:
        node = _make_node()
        node.after_value_set(node._color_mode_param, COLOR_MODE_OCIO)
        assert node.metadata["_color_mode"] == COLOR_MODE_OCIO

    def test_switching_to_ocio_calls_visibility_with_true(self) -> None:
        node = _make_node()
        with patch(f"{_OCIO_MODULE}.apply_color_mode_visibility") as mock_vis:
            node.after_value_set(node._color_mode_param, COLOR_MODE_OCIO)
        mock_vis.assert_called_once_with(node, True, node._tone_mapping_param.name, node._color_params_param.name)

    def test_switching_to_basic_calls_visibility_with_false(self) -> None:
        node = _make_node()
        with patch(f"{_OCIO_MODULE}.apply_color_mode_visibility") as mock_vis:
            node.after_value_set(node._color_mode_param, COLOR_MODE_BASIC)
        mock_vis.assert_called_once_with(node, False, node._tone_mapping_param.name, node._color_params_param.name)

    def test_other_parameter_does_not_call_visibility(self) -> None:
        node = _make_node()
        with patch(f"{_OCIO_MODULE}.apply_color_mode_visibility") as mock_vis:
            node.after_value_set(node._exposure_param, 1.0)
        mock_vis.assert_not_called()


# ---------------------------------------------------------------------------
# aprocess — no input
# ---------------------------------------------------------------------------


class TestAProcessNoInput:
    def test_fails_gracefully_when_no_sequence(self) -> None:
        node = _make_node()
        asyncio.run(node.aprocess())
        assert _was_successful(node) is False


# ---------------------------------------------------------------------------
# aprocess — full pipeline (mocked ffmpeg + pixel loading)
# ---------------------------------------------------------------------------


def _make_1x1_rgba_pixels() -> dict[str, np.ndarray]:
    """Return a minimal channel dict for a 1×1 RGBA frame."""
    return {
        "R": np.array([[0.18]], dtype=np.float32),
        "G": np.array([[0.18]], dtype=np.float32),
        "B": np.array([[0.18]], dtype=np.float32),
        "A": np.array([[1.0]], dtype=np.float32),
    }


class TestAProcessPipeline:
    """Full-pipeline tests with mocked I/O and ffmpeg."""

    def _run_with_sequence(self, paths: list[Path], tmp_path: Path, threads: int = 2):
        from griptape_nodes.retained_mode.events.os_events import DeleteFileRequest
        from griptape_nodes.retained_mode.griptape_nodes import GriptapeNodes

        node = _make_node()
        seq_dict = _make_sequence(paths)

        fake_pixels = _make_1x1_rgba_pixels()
        fake_video_bytes = b"FAKE_MP4"
        fake_saved = MagicMock()
        fake_saved.location = "/tmp/preview.mp4"
        fake_dest = MagicMock()
        fake_dest.write_bytes.return_value = fake_saved

        fake_temp_path = tmp_path / "exr-preview"
        mock_situation_dest = MagicMock()
        mock_situation_dest.resolve.return_value = fake_temp_path / "frame_0000.png"

        def _handle_request(request):
            if isinstance(request, DeleteFileRequest) and request.path:
                p = Path(request.path)
                shutil.rmtree(p, ignore_errors=True) if p.is_dir() else p.unlink(missing_ok=True)
            return MagicMock()

        # Patch output_file on the instance
        node._output_file = MagicMock()
        node._output_file.build_file.return_value = fake_dest

        # Patch node parameters
        node.set_parameter_value("sequence", seq_dict)
        node.set_parameter_value("threads", threads)
        node.set_parameter_value("frame_rate", 24.0)
        node.set_parameter_value("color_mode", COLOR_MODE_BASIC)
        node.set_parameter_value("tone_mapping", TONE_FILMIC)
        node.set_parameter_value("exposure", 0.0)
        node.set_parameter_value("format", "mp4")
        node.set_parameter_value("processing_speed", "balanced")

        with (
            patch(f"{_NODE_MODULE}.ProjectFileDestination") as mock_pfd,
            patch.object(GriptapeNodes, "handle_request", side_effect=_handle_request),
            patch(f"{_NODE_MODULE}.load_exr_channels", return_value=fake_pixels),
            patch(f"{_NODE_MODULE}.select_display_channels", return_value=["R", "G", "B"]),
            patch(
                f"{_NODE_MODULE}.apply_color_management",
                return_value=(np.ones((1, 1, 3), dtype=np.float32), TONE_FILMIC),
            ),
            patch(f"{_NODE_MODULE}.subprocess") as mock_subproc,
            patch(f"{_NODE_MODULE}.run") as mock_ffmpeg_run,
        ):
            mock_pfd.from_situation.return_value = mock_situation_dest
            mock_ffmpeg_run.get_or_fetch_platform_executables_else_raise.return_value = (
                "/usr/bin/ffmpeg",
                "/usr/bin/ffprobe",
            )

            # Simulate ffmpeg producing an output file
            def fake_ffmpeg_run(cmd, **kwargs):
                result = MagicMock()
                result.returncode = 0
                # Actually write a fake output file so the node can read it
                output_path = Path(cmd[-1])
                output_path.write_bytes(fake_video_bytes)
                return result

            mock_subproc.run.side_effect = fake_ffmpeg_run
            mock_subproc.TimeoutExpired = __import__("subprocess").TimeoutExpired
            mock_subproc.CalledProcessError = __import__("subprocess").CalledProcessError

            asyncio.run(node.aprocess())

        return node, fake_dest

    def test_succeeds_with_valid_sequence(self, tmp_path: Path) -> None:
        paths = [SINGLE_PART_RGBA, SINGLE_PART_RGBA, SINGLE_PART_RGBA]
        node, _ = self._run_with_sequence(paths, tmp_path)
        assert _was_successful(node) is True

    def test_frame_count_published_correctly(self, tmp_path: Path) -> None:
        paths = [SINGLE_PART_RGBA] * 5
        node, _ = self._run_with_sequence(paths, tmp_path)
        assert node.parameter_output_values.get("frame_count") == 5

    def test_frames_processed_reaches_frame_count(self, tmp_path: Path) -> None:
        paths = [SINGLE_PART_RGBA] * 4
        node, _ = self._run_with_sequence(paths, tmp_path)
        assert node.parameter_output_values.get("frames_processed") == 4

    def test_video_output_is_set(self, tmp_path: Path) -> None:
        from griptape.artifacts.video_url_artifact import VideoUrlArtifact

        paths = [SINGLE_PART_RGBA, SINGLE_PART_RGBA]
        node, _ = self._run_with_sequence(paths, tmp_path)
        video = node.parameter_output_values.get("video")
        assert video is not None
        assert isinstance(video, VideoUrlArtifact)

    def test_ffmpeg_called_with_libx264(self, tmp_path: Path) -> None:
        from griptape_nodes.retained_mode.events.os_events import DeleteFileRequest
        from griptape_nodes.retained_mode.griptape_nodes import GriptapeNodes

        paths = [SINGLE_PART_RGBA, SINGLE_PART_RGBA]
        node = _make_node()
        seq_dict = _make_sequence(paths)
        fake_pixels = _make_1x1_rgba_pixels()
        fake_saved = MagicMock()
        fake_saved.location = "/tmp/preview.mp4"
        fake_dest = MagicMock()
        fake_dest.write_bytes.return_value = fake_saved
        node._output_file = MagicMock()
        node._output_file.build_file.return_value = fake_dest

        fake_temp_path = tmp_path / "exr-preview"
        mock_situation_dest = MagicMock()
        mock_situation_dest.resolve.return_value = fake_temp_path / "frame_0000.png"

        def _handle_request(request):
            if isinstance(request, DeleteFileRequest) and request.path:
                p = Path(request.path)
                shutil.rmtree(p, ignore_errors=True) if p.is_dir() else p.unlink(missing_ok=True)
            return MagicMock()

        node.set_parameter_value("sequence", seq_dict)
        node.set_parameter_value("threads", 1)
        node.set_parameter_value("frame_rate", 24.0)
        node.set_parameter_value("color_mode", COLOR_MODE_BASIC)
        node.set_parameter_value("tone_mapping", TONE_FILMIC)
        node.set_parameter_value("exposure", 0.0)
        node.set_parameter_value("format", "mp4")
        node.set_parameter_value("processing_speed", "balanced")

        captured_cmd = []

        with (
            patch(f"{_NODE_MODULE}.ProjectFileDestination") as mock_pfd,
            patch.object(GriptapeNodes, "handle_request", side_effect=_handle_request),
            patch(f"{_NODE_MODULE}.load_exr_channels", return_value=fake_pixels),
            patch(f"{_NODE_MODULE}.select_display_channels", return_value=["R", "G", "B"]),
            patch(
                f"{_NODE_MODULE}.apply_color_management",
                return_value=(np.ones((1, 1, 3), dtype=np.float32), TONE_FILMIC),
            ),
            patch(f"{_NODE_MODULE}.subprocess") as mock_subproc,
            patch(f"{_NODE_MODULE}.run") as mock_ffmpeg_run,
        ):
            mock_pfd.from_situation.return_value = mock_situation_dest
            mock_ffmpeg_run.get_or_fetch_platform_executables_else_raise.return_value = (
                "/usr/bin/ffmpeg",
                "/usr/bin/ffprobe",
            )

            def capture_cmd(cmd, **kwargs):
                captured_cmd.extend(cmd)
                result = MagicMock()
                result.returncode = 0
                Path(cmd[-1]).write_bytes(b"FAKE")
                return result

            mock_subproc.run.side_effect = capture_cmd
            mock_subproc.TimeoutExpired = __import__("subprocess").TimeoutExpired
            mock_subproc.CalledProcessError = __import__("subprocess").CalledProcessError

            asyncio.run(node.aprocess())

        assert "libx264" in captured_cmd
        assert "yuv420p" in captured_cmd

    @pytest.mark.parametrize(
        ("speed", "expected_preset", "expected_crf"),
        [
            ("fast", "ultrafast", 30),
            ("balanced", "medium", 23),
            ("quality", "slow", 18),
        ],
    )
    def test_processing_speed_settings(self, speed: str, expected_preset: str, expected_crf: int) -> None:
        node = _make_node()
        node.set_parameter_value("processing_speed", speed)
        preset, crf = node._get_processing_speed_settings()
        assert preset == expected_preset
        assert crf == expected_crf

    def test_partial_frame_failure_is_recorded_not_crash(self, tmp_path: Path) -> None:
        from griptape_nodes.retained_mode.events.os_events import DeleteFileRequest
        from griptape_nodes.retained_mode.griptape_nodes import GriptapeNodes

        paths = [SINGLE_PART_RGBA, SINGLE_PART_RGBA, SINGLE_PART_RGBA]
        node = _make_node()
        seq_dict = _make_sequence(paths)
        fake_saved = MagicMock()
        fake_saved.location = "/tmp/preview.mp4"
        fake_dest = MagicMock()
        fake_dest.write_bytes.return_value = fake_saved
        node._output_file = MagicMock()
        node._output_file.build_file.return_value = fake_dest

        fake_temp_path = tmp_path / "exr-preview"
        mock_situation_dest = MagicMock()
        mock_situation_dest.resolve.return_value = fake_temp_path / "frame_0000.png"

        def _handle_request(request):
            if isinstance(request, DeleteFileRequest) and request.path:
                p = Path(request.path)
                shutil.rmtree(p, ignore_errors=True) if p.is_dir() else p.unlink(missing_ok=True)
            return MagicMock()

        node.set_parameter_value("sequence", seq_dict)
        node.set_parameter_value("threads", 1)
        node.set_parameter_value("frame_rate", 24.0)
        node.set_parameter_value("color_mode", COLOR_MODE_BASIC)
        node.set_parameter_value("tone_mapping", TONE_FILMIC)
        node.set_parameter_value("exposure", 0.0)
        node.set_parameter_value("format", "mp4")
        node.set_parameter_value("processing_speed", "balanced")

        call_count = 0

        def fail_on_second_frame(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 2:
                raise ValueError("simulated bad EXR")
            return _make_1x1_rgba_pixels()

        with (
            patch(f"{_NODE_MODULE}.ProjectFileDestination") as mock_pfd,
            patch.object(GriptapeNodes, "handle_request", side_effect=_handle_request),
            patch(f"{_NODE_MODULE}.load_exr_channels", side_effect=fail_on_second_frame),
            patch(f"{_NODE_MODULE}.select_display_channels", return_value=["R", "G", "B"]),
            patch(
                f"{_NODE_MODULE}.apply_color_management",
                return_value=(np.ones((1, 1, 3), dtype=np.float32), TONE_FILMIC),
            ),
            patch(f"{_NODE_MODULE}.subprocess") as mock_subproc,
            patch(f"{_NODE_MODULE}.run") as mock_ffmpeg_run,
        ):
            mock_pfd.from_situation.return_value = mock_situation_dest
            mock_ffmpeg_run.get_or_fetch_platform_executables_else_raise.return_value = (
                "/usr/bin/ffmpeg",
                "/usr/bin/ffprobe",
            )

            def fake_ffmpeg_run(cmd, **kwargs):
                Path(cmd[-1]).write_bytes(b"FAKE_MP4")
                return MagicMock(returncode=0)

            mock_subproc.run.side_effect = fake_ffmpeg_run
            mock_subproc.TimeoutExpired = __import__("subprocess").TimeoutExpired
            mock_subproc.CalledProcessError = __import__("subprocess").CalledProcessError

            asyncio.run(node.aprocess())

        assert _was_successful(node) is True
        details = node.parameter_output_values.get("result_details", "")
        assert "1 frame error" in details


# ---------------------------------------------------------------------------
# aprocess — OCIO mode
# ---------------------------------------------------------------------------


class TestAProcessOCIOMode:
    def test_ocio_mode_without_color_params_fails(self) -> None:
        node = _make_node(ocio_available=True)
        paths = [SINGLE_PART_RGBA]
        seq_dict = _make_sequence(paths)
        node.set_parameter_value("sequence", seq_dict)
        node.set_parameter_value("color_mode", COLOR_MODE_OCIO)
        # color_params not set

        asyncio.run(node.aprocess())
        assert _was_successful(node) is False

    def test_ocio_mode_calls_apply_color_management_with_color_params(self, tmp_path: Path) -> None:
        from griptape_nodes.retained_mode.events.os_events import DeleteFileRequest
        from griptape_nodes.retained_mode.griptape_nodes import GriptapeNodes

        node = _make_node(ocio_available=True)
        paths = [SINGLE_PART_RGBA]
        seq_dict = _make_sequence(paths)
        fake_pixels = _make_1x1_rgba_pixels()
        fake_saved = MagicMock()
        fake_saved.location = "/tmp/preview.mp4"
        fake_dest = MagicMock()
        fake_dest.write_bytes.return_value = fake_saved
        node._output_file = MagicMock()
        node._output_file.build_file.return_value = fake_dest

        fake_temp_path = tmp_path / "exr-preview"
        mock_situation_dest = MagicMock()
        mock_situation_dest.resolve.return_value = fake_temp_path / "frame_0000.png"

        def _handle_request(request):
            if isinstance(request, DeleteFileRequest) and request.path:
                p = Path(request.path)
                shutil.rmtree(p, ignore_errors=True) if p.is_dir() else p.unlink(missing_ok=True)
            return MagicMock()

        fake_color_params = MagicMock()
        fake_color_params.source_colorspace = "ACEScg"
        fake_color_params.display = "sRGB"
        fake_color_params.view = "ACES"
        fake_color_params.config_path = None

        node.set_parameter_value("sequence", seq_dict)
        node.set_parameter_value("color_mode", COLOR_MODE_OCIO)
        node.set_parameter_value("color_params", fake_color_params)
        node.set_parameter_value("threads", 1)
        node.set_parameter_value("frame_rate", 24.0)
        node.set_parameter_value("exposure", 0.0)
        node.set_parameter_value("format", "mp4")
        node.set_parameter_value("processing_speed", "balanced")

        with (
            patch(f"{_NODE_MODULE}.ProjectFileDestination") as mock_pfd,
            patch.object(GriptapeNodes, "handle_request", side_effect=_handle_request),
            patch(f"{_NODE_MODULE}.load_exr_channels", return_value=fake_pixels),
            patch(f"{_NODE_MODULE}.select_display_channels", return_value=["R", "G", "B"]),
            patch(
                f"{_NODE_MODULE}.apply_color_management",
                return_value=(np.ones((1, 1, 3), dtype=np.float32), "ocio:ACEScg→sRGB/ACES"),
            ) as mock_cm,
            patch(f"{_NODE_MODULE}.subprocess") as mock_subproc,
            patch(f"{_NODE_MODULE}.run") as mock_ffmpeg_run,
        ):
            mock_pfd.from_situation.return_value = mock_situation_dest
            mock_ffmpeg_run.get_or_fetch_platform_executables_else_raise.return_value = (
                "/usr/bin/ffmpeg",
                "/usr/bin/ffprobe",
            )

            def fake_run(cmd, **kwargs):
                Path(cmd[-1]).write_bytes(b"FAKE")
                return MagicMock(returncode=0)

            mock_subproc.run.side_effect = fake_run
            mock_subproc.TimeoutExpired = __import__("subprocess").TimeoutExpired
            mock_subproc.CalledProcessError = __import__("subprocess").CalledProcessError

            asyncio.run(node.aprocess())

        # apply_color_management should have been called with the real color_params object
        mock_cm.assert_called()
        call_args = mock_cm.call_args
        assert call_args[0][1] is fake_color_params


# ---------------------------------------------------------------------------
# Temp-dir situation usage
# ---------------------------------------------------------------------------


class TestProcessUsesSituationTempDir:
    """Verify that aprocess uses the save_temp_file situation for scratch files."""

    def _run_minimal(self, tmp_path: Path):
        from griptape_nodes.retained_mode.events.os_events import DeleteFileRequest
        from griptape_nodes.retained_mode.griptape_nodes import GriptapeNodes

        node = _make_node()
        seq_dict = _make_sequence([SINGLE_PART_RGBA])
        fake_pixels = _make_1x1_rgba_pixels()
        fake_saved = MagicMock()
        fake_saved.location = "/tmp/preview.mp4"
        fake_dest = MagicMock()
        fake_dest.write_bytes.return_value = fake_saved
        node._output_file = MagicMock()
        node._output_file.build_file.return_value = fake_dest
        node.set_parameter_value("sequence", seq_dict)
        node.set_parameter_value("threads", 1)
        node.set_parameter_value("frame_rate", 24.0)
        node.set_parameter_value("color_mode", COLOR_MODE_BASIC)
        node.set_parameter_value("tone_mapping", TONE_FILMIC)
        node.set_parameter_value("exposure", 0.0)
        node.set_parameter_value("format", "mp4")
        node.set_parameter_value("processing_speed", "balanced")

        fake_temp_path = tmp_path / "exr-preview"
        mock_situation_dest = MagicMock()
        mock_situation_dest.resolve.return_value = fake_temp_path / "frame_0000.png"

        delete_requests: list[str] = []

        def _handle_request(request):
            if isinstance(request, DeleteFileRequest) and request.path:
                delete_requests.append(request.path)
                p = Path(request.path)
                shutil.rmtree(p, ignore_errors=True) if p.is_dir() else p.unlink(missing_ok=True)
            return MagicMock()

        with (
            patch(f"{_NODE_MODULE}.ProjectFileDestination") as mock_pfd,
            patch.object(GriptapeNodes, "handle_request", side_effect=_handle_request),
            patch(f"{_NODE_MODULE}.load_exr_channels", return_value=fake_pixels),
            patch(f"{_NODE_MODULE}.select_display_channels", return_value=["R", "G", "B"]),
            patch(
                f"{_NODE_MODULE}.apply_color_management",
                return_value=(np.ones((1, 1, 3), dtype=np.float32), TONE_FILMIC),
            ),
            patch(f"{_NODE_MODULE}.subprocess") as mock_subproc,
            patch(f"{_NODE_MODULE}.run") as mock_ffmpeg_run,
        ):
            mock_pfd.from_situation.return_value = mock_situation_dest
            mock_ffmpeg_run.get_or_fetch_platform_executables_else_raise.return_value = (
                "/usr/bin/ffmpeg",
                "/usr/bin/ffprobe",
            )

            def fake_ffmpeg_run(cmd, **kwargs):
                Path(cmd[-1]).write_bytes(b"FAKE_MP4")
                return MagicMock(returncode=0)

            mock_subproc.run.side_effect = fake_ffmpeg_run
            mock_subproc.TimeoutExpired = __import__("subprocess").TimeoutExpired
            mock_subproc.CalledProcessError = __import__("subprocess").CalledProcessError

            asyncio.run(node.aprocess())

        return mock_pfd, delete_requests

    def test_uses_save_temp_file_situation(self, tmp_path: Path) -> None:
        mock_pfd, _ = self._run_minimal(tmp_path)
        mock_pfd.from_situation.assert_called_once()
        call_args = mock_pfd.from_situation.call_args
        positional = call_args.args
        keyword = call_args.kwargs
        situation_used = positional[1] if len(positional) > 1 else keyword.get("situation")
        assert situation_used == "save_temp_file"

    def test_situation_filename_uses_exr_preview_prefix(self, tmp_path: Path) -> None:
        mock_pfd, _ = self._run_minimal(tmp_path)
        call_args = mock_pfd.from_situation.call_args
        positional = call_args.args
        keyword = call_args.kwargs
        filename_used = positional[0] if positional else keyword.get("filename", "")
        assert filename_used.startswith("exr-preview-")
        assert filename_used.endswith("/frame_0000.png")

    def test_temp_files_cleaned_up_via_delete_request(self, tmp_path: Path) -> None:
        _, delete_requests = self._run_minimal(tmp_path)
        assert len(delete_requests) == 1, "Expected exactly one DeleteFileRequest for the temp directory"
        assert Path(delete_requests[0]) == tmp_path / "exr-preview", "DeleteFileRequest should target the temp dir"


# ---------------------------------------------------------------------------
# Manifest registration
# ---------------------------------------------------------------------------


class TestManifestRegistration:
    def test_node_is_registered_in_manifest(self) -> None:
        manifest_path = Path(__file__).parents[2] / "griptape-nodes-library.json"
        assert manifest_path.exists(), f"Manifest not found at {manifest_path}"
        manifest = json.loads(manifest_path.read_text())
        class_names = [n["class_name"] for n in manifest.get("nodes", [])]
        assert "EXRSequenceToPreview" in class_names

    def test_manifest_entry_has_correct_file_path(self) -> None:
        manifest_path = Path(__file__).parents[2] / "griptape-nodes-library.json"
        manifest = json.loads(manifest_path.read_text())
        entry = next(n for n in manifest["nodes"] if n["class_name"] == "EXRSequenceToPreview")
        assert "exr_sequence_to_preview" in entry["file_path"]
