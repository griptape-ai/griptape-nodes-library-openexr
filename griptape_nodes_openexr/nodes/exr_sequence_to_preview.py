"""EXRSequenceToPreview node — encode an EXR frame sequence to an MP4 preview."""

from __future__ import annotations

import asyncio
import logging
import subprocess
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from griptape.artifacts import VideoUrlArtifact
from griptape_nodes.common.sequences import Sequence
from griptape_nodes.exe_types.core_types import Parameter, ParameterGroup, ParameterMode
from griptape_nodes.exe_types.node_types import SuccessFailureNode
from griptape_nodes.exe_types.param_components.project_file_parameter import ProjectFileParameter
from griptape_nodes.exe_types.param_types.parameter_float import ParameterFloat
from griptape_nodes.exe_types.param_types.parameter_int import ParameterInt
from griptape_nodes.exe_types.param_types.parameter_string import ParameterString
from griptape_nodes.exe_types.param_types.parameter_video import ParameterVideo
from griptape_nodes.files.project_file import ProjectFileDestination
from griptape_nodes.retained_mode.events.os_events import DeleteFileRequest, DeleteFileResultFailure
from griptape_nodes.retained_mode.griptape_nodes import GriptapeNodes
from griptape_nodes.traits.options import Options
from PIL import Image

# static_ffmpeg is dynamically installed by the library loader at runtime
from static_ffmpeg import run  # type: ignore[import-untyped]

from griptape_nodes_openexr.exr.channel_selection import select_display_channels
from griptape_nodes_openexr.exr.exr_io import build_rgb_array, load_exr_channels
from griptape_nodes_openexr.exr.ocio_helpers import (
    COLOR_MODE_OCIO,
    ColorParamsProtocol,
    add_color_mode_parameters,
    apply_color_management,
    handle_color_mode_change,
)
from griptape_nodes_openexr.exr.tone_mapping import (
    EV_MAX,
    EV_MIN,
    apply_exposure,
    to_uint8_srgb,
)

logger = logging.getLogger("griptape_nodes")

_DEFAULT_FRAME_RATE = 24.0
_DEFAULT_THREADS = 4
_LOG_INTERVAL_PCT = 10
_PIX_FMT = "yuv420p"
_SPEED_FAST = "fast"
_SPEED_BALANCED = "balanced"
_SPEED_QUALITY = "quality"


class EXRSequenceToPreview(SuccessFailureNode):
    """Convert an EXR frame sequence to an MP4 preview with optional color management.

    Accepts a Sequence artifact (from ScanSequenceNode), converts each frame from
    HDR float32 through exposure + tone mapping or OCIO to 8-bit sRGB PNGs, then
    encodes them with ffmpeg into an MP4. EXR→PNG conversion is parallelised across
    a configurable thread pool.
    """

    def __init__(self, name: str, metadata: dict[Any, Any] | None = None) -> None:
        super().__init__(name, metadata)

        self._sequence_param = Parameter(
            name="sequence",
            input_types=["Sequence"],
            type="Sequence",
            tooltip="EXR frame sequence from ScanSequenceNode.",
            allowed_modes={ParameterMode.INPUT},
        )
        self.add_parameter(self._sequence_param)

        self._frame_rate_param = ParameterFloat(
            name="frame_rate",
            default_value=_DEFAULT_FRAME_RATE,
            tooltip="Output frame rate in fps (VFX convention: 24)",
            allowed_modes={ParameterMode.INPUT, ParameterMode.PROPERTY},
        )
        self.add_parameter(self._frame_rate_param)

        self._part_index_param = ParameterInt(
            name="part_index",
            default_value=0,
            tooltip="Zero-based EXR part index to use when reading pixels",
            allowed_modes={ParameterMode.INPUT, ParameterMode.PROPERTY},
        )
        self.add_parameter(self._part_index_param)

        self._exposure_param = ParameterFloat(
            name="exposure",
            default_value=0.0,
            min_val=EV_MIN,
            max_val=EV_MAX,
            step=0.1,
            slider=True,
            tooltip="Exposure in EV stops applied before tone mapping or OCIO transform",
            allowed_modes={ParameterMode.INPUT, ParameterMode.PROPERTY},
        )
        self.add_parameter(self._exposure_param)

        self._color_mode_param, self._tone_mapping_param, self._color_params_param = add_color_mode_parameters(self)

        # TODO: output path/URL may be derivable from the Artifact Manager in future
        self._format_param = ParameterString(
            name="format",
            default_value="mp4",
            tooltip="Output video container format",
            allowed_modes={ParameterMode.INPUT, ParameterMode.PROPERTY},
        )
        self._format_param.add_trait(Options(choices=["mp4", "mov"]))
        self.add_parameter(self._format_param)

        self._processing_speed_param = ParameterString(
            name="processing_speed",
            default_value=_SPEED_BALANCED,
            tooltip="Encoding speed vs quality trade-off",
            allowed_modes={ParameterMode.INPUT, ParameterMode.PROPERTY},
        )
        # TODO: consider exposing a CUSTOM option to allow direct CRF value input (e.g. CRF 0 for lossless)
        self._processing_speed_param.add_trait(Options(choices=[_SPEED_FAST, _SPEED_BALANCED, _SPEED_QUALITY]))
        self.add_parameter(self._processing_speed_param)

        # --- Advanced group ---
        advanced_group = ParameterGroup(name="Advanced", ui_options={"collapsed": True})
        with advanced_group:
            self._threads_param = ParameterInt(
                name="threads",
                default_value=_DEFAULT_THREADS,
                min_val=1,
                max_val=16,
                tooltip="Number of concurrent EXR→PNG conversion workers (1–16)",
                allowed_modes={ParameterMode.INPUT, ParameterMode.PROPERTY},
            )
        self.add_node_element(advanced_group)

        # --- Outputs ---
        self._video_param = ParameterVideo(
            name="video",
            allowed_modes={ParameterMode.OUTPUT},
            tooltip="Preview video",
            ui_options={"pulse_on_run": True},
        )
        self.add_parameter(self._video_param)

        self._frame_count_param = ParameterInt(
            name="frame_count",
            default_value=0,
            tooltip="Total number of frames in the sequence",
            allowed_modes={ParameterMode.OUTPUT},
        )
        self.add_parameter(self._frame_count_param)

        self._frames_processed_param = ParameterInt(
            name="frames_processed",
            default_value=0,
            tooltip="Number of frames converted so far",
            allowed_modes={ParameterMode.OUTPUT},
        )
        self.add_parameter(self._frames_processed_param)

        self._output_file = ProjectFileParameter(
            node=self,
            name="output_file",
            default_filename="preview.mp4",
        )
        self._output_file.add_parameter()

        self._create_status_parameters(
            result_details_tooltip="Details about the preview encoding result",
            result_details_placeholder="Encoding details will appear here.",
            parameter_group_initially_collapsed=True,
        )

    def after_value_set(self, parameter: Parameter, value: Any) -> None:
        if parameter.name == self._color_mode_param.name:
            handle_color_mode_change(self, value, self._tone_mapping_param.name, self._color_params_param.name)

    def _get_processing_speed_settings(self) -> tuple[str, int]:
        """Return (preset, crf) for the current processing_speed value."""
        speed = self.get_parameter_value("processing_speed")
        if speed == _SPEED_FAST:
            return "ultrafast", 30
        if speed == _SPEED_QUALITY:
            return "slow", 18
        return "medium", 23

    def _on_fail(self, details: str) -> None:
        self.parameter_output_values[self._video_param.name] = None
        self._set_status_results(was_successful=False, result_details=details)

    async def aprocess(self) -> None:
        self._clear_execution_status()
        self.parameter_output_values[self._video_param.name] = None

        # --- Resolve sequence ---
        raw = self.get_parameter_value(self._sequence_param.name)
        if raw is None:
            self._on_fail("No sequence provided — connect a ScanSequenceNode.")
            return

        try:
            sequence = Sequence.model_validate(raw) if isinstance(raw, dict) else raw
        except Exception as e:
            self._on_fail(f"Failed to parse sequence: {e}")
            return

        exr_paths = [entry.path for entry in sequence.entries]
        if not exr_paths:
            self._on_fail("Sequence contains no entries.")
            return

        total = len(exr_paths)
        self.parameter_output_values[self._frame_count_param.name] = total
        self.publish_update_to_parameter(self._frame_count_param.name, total)
        self.parameter_output_values[self._frames_processed_param.name] = 0
        self.publish_update_to_parameter(self._frames_processed_param.name, 0)

        # --- Collect processing parameters ---
        ev = float(self.get_parameter_value(self._exposure_param.name))
        part_index = int(self.get_parameter_value(self._part_index_param.name))
        frame_rate = float(self.get_parameter_value(self._frame_rate_param.name))
        color_mode = str(self.get_parameter_value(self._color_mode_param.name))
        tone_mapping = str(self.get_parameter_value(self._tone_mapping_param.name))
        threads = int(self.get_parameter_value(self._threads_param.name))
        fmt = str(self.get_parameter_value(self._format_param.name))

        color_params: ColorParamsProtocol | None = None
        if color_mode == COLOR_MODE_OCIO:
            color_params = self.get_parameter_value(self._color_params_param.name)  # type: ignore[assignment]
            if color_params is None:
                self._on_fail("OCIO mode requires a connected color_params artifact.")
                return

        logger.info("%s: encoding %d EXR frames at %.1f fps (%d thread(s))", self.name, total, frame_rate, threads)

        dest = ProjectFileDestination.from_situation(f"exr-preview-{uuid.uuid4().hex}/frame_0000.png", "save_temp_file")
        temp_path = Path(dest.resolve()).parent
        temp_path.mkdir(parents=True, exist_ok=True)

        errors: list[str] = []
        try:
            frames_done = 0
            loop = asyncio.get_running_loop()
            log_step = max(1, total * _LOG_INTERVAL_PCT // 100)

            async def _run_frame(i: int, path: str) -> tuple[int, Exception | None]:
                try:
                    await loop.run_in_executor(
                        executor,
                        _convert_frame,
                        path,
                        i,
                        temp_path,
                        part_index,
                        ev,
                        color_params,
                        tone_mapping,
                    )
                    return i, None
                except Exception as e:
                    return i, e

            with ThreadPoolExecutor(max_workers=threads) as executor:
                tasks = [_run_frame(i, path) for i, path in enumerate(exr_paths)]

                for coro in asyncio.as_completed(tasks):
                    idx, err = await coro
                    if err is not None:
                        errors.append(f"frame {idx}: {err}")
                        logger.warning("%s: frame %d failed: %s", self.name, idx, err)

                    frames_done += 1
                    self.parameter_output_values[self._frames_processed_param.name] = frames_done
                    if frames_done % log_step == 0 or frames_done == total:
                        self.publish_update_to_parameter(self._frames_processed_param.name, frames_done)
                        pct = frames_done * 100 // total
                        logger.info("%s: converted %d/%d frames (%d%%)", self.name, frames_done, total, pct)

            if errors:
                logger.warning("%s: %d frame(s) failed conversion: %s", self.name, len(errors), "; ".join(errors))

            # --- ffmpeg encode ---
            try:
                ffmpeg_path, _ = run.get_or_fetch_platform_executables_else_raise()
            except Exception as e:
                self._on_fail(f"ffmpeg not found: {e}")
                return

            input_pattern = str(temp_path / "frame_%04d.png")
            output_path = temp_path / f"output.{fmt}"
            preset, crf = self._get_processing_speed_settings()

            cmd = [
                ffmpeg_path,
                "-f",
                "image2",
                "-framerate",
                str(frame_rate),
                "-i",
                input_pattern,
                "-c:v",
                "libx264",
                "-preset",
                preset,
                "-crf",
                str(crf),
                "-pix_fmt",
                _PIX_FMT,
                "-movflags",
                "+faststart",
                "-an",
                "-y",
                str(output_path),
            ]

            try:
                subprocess.run(cmd, capture_output=True, text=True, check=True, timeout=600)  # noqa: S603
            except subprocess.TimeoutExpired as e:
                self._on_fail(f"ffmpeg timed out: {e}")
                return
            except subprocess.CalledProcessError as e:
                self._on_fail(f"ffmpeg failed: {e.stderr}")
                return

            if not output_path.exists():
                self._on_fail("ffmpeg did not produce an output file.")
                return

            video_bytes = output_path.read_bytes()
        finally:
            result = GriptapeNodes.handle_request(DeleteFileRequest(path=str(temp_path), workspace_only=False))
            if isinstance(result, DeleteFileResultFailure):
                logger.warning("%s: failed to clean up temp dir '%s': %s", self.name, temp_path, result.failure_reason)

        try:
            dest = self._output_file.build_file()
            saved = dest.write_bytes(video_bytes)
            artifact = VideoUrlArtifact(saved.location)
        except Exception as e:
            self._on_fail(f"Failed to save video: {e}")
            return

        self.parameter_output_values[self._video_param.name] = artifact
        self.publish_update_to_parameter(self._video_param.name, artifact)

        error_note = f", {len(errors)} frame error(s)" if errors else ""
        details = f"Encoded {total} frames at {frame_rate} fps → {fmt}{error_note}"
        self._set_status_results(was_successful=True, result_details=details)
        logger.info("%s: %s", self.name, details)


def _convert_frame(
    exr_path: str,
    frame_index: int,
    temp_dir: Path,
    part_index: int,
    ev: float,
    color_params: ColorParamsProtocol | None,
    tone_mapping: str,
) -> None:
    """Load one EXR frame, apply colour management, and write a PNG to temp_dir.

    Runs on a thread-pool worker. Raises on any failure so the caller can
    collect errors without aborting the whole run.
    """
    pixels = load_exr_channels(exr_path, part_index)

    channel_names = list(pixels.keys())
    selected = select_display_channels(channel_names)
    if selected is None:
        msg = f"no displayable channels in '{exr_path}'"
        raise ValueError(msg)

    rgb = build_rgb_array(pixels, selected)
    rgb = apply_exposure(rgb, ev)
    rgb, _ = apply_color_management(rgb, color_params, tone_mapping)
    uint8_rgb = to_uint8_srgb(rgb)

    out_path = temp_dir / f"frame_{frame_index:04d}.png"
    Image.fromarray(uint8_rgb, "RGB").save(out_path)
