"""EXRSequenceToPreview node — encode an EXR frame sequence to an MP4 preview."""

from __future__ import annotations

import logging
import subprocess
import uuid
from pathlib import Path
from typing import Any

from griptape.artifacts import VideoUrlArtifact
from griptape_nodes.common.sequences import SequenceEntry
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
from griptape_nodes.utils.async_utils import to_thread
from PIL import Image

# static_ffmpeg is dynamically installed by the library loader at runtime
from static_ffmpeg import run  # type: ignore[import-untyped]

from griptape_nodes_openexr.exr import render_limits, sequence_canvas
from griptape_nodes_openexr.exr.channel_selection import select_display_channels
from griptape_nodes_openexr.exr.display_window import (
    WINDOW_MODE_DATA,
    WINDOW_MODE_DEFAULT,
    add_window_mode_parameter,
    apply_window_mode,
    read_framing,
    select_window_channels,
    validate_window_mode,
    window_size,
)
from griptape_nodes_openexr.exr.exr_io import build_rgb_array, load_exr_channels
from griptape_nodes_openexr.exr.exr_types import WindowCoordinates
from griptape_nodes_openexr.exr.ocio_helpers import (
    COLOR_MODE_OCIO,
    ColorParamsProtocol,
    add_color_mode_parameters,
    apply_color_management,
    handle_color_mode_change,
    read_tone_mapping,
)
from griptape_nodes_openexr.exr.sequence_helpers import (
    resolve_sequence_path,
    run_over_sequence,
    sequence_path_parameter,
)
from griptape_nodes_openexr.exr.tone_mapping import (
    EV_MAX,
    EV_MIN,
    TONE_CHOICES,
    TONE_RAW,
    TONE_REC709,
    TONE_REC1886,
    TONE_SRGB,
    apply_exposure,
    display_encoding,
    to_uint8,
)

logger = logging.getLogger("griptape_nodes")

_DEFAULT_FRAME_RATE = 24.0
_DEFAULT_THREADS = 4
_LOG_INTERVAL_PCT = 10
_PIX_FMT = "yuv420p"
_MOVFLAGS = "+faststart+write_colr"
_FRAME_PATTERN = "frame_%04d.png"

# Transfer characteristics per display transform. A video carries no ICC
# profile, so these tags are its only colour signalling.
#
# They are passed through -x264-params rather than ffmpeg's own -color_trc:
# the bundled static_ffmpeg build accepts -color_trc without error but drops
# it, leaving color_transfer=unknown in the output, while -colorspace does
# apply. Setting all three through x264 keeps them consistent and verifiable.
# An unrecognised value is ignored rather than honoured, hence the explicit
# fallback rather than passing an arbitrary transform name through.
_ENCODING_TRC_VALUES = {
    TONE_SRGB.lower(): "iec61966-2-1",
    TONE_REC709.lower(): "bt709",
    # No exact tag exists for gamma 2.4; bt709 is the closest available.
    TONE_REC1886.lower(): "bt709",
    TONE_RAW.lower(): "linear",
}
# Keyed by every choice. An ACES choice writes Rec.709 primaries with its base
# choice's encoding, so it carries that choice's tag.
_FFMPEG_TRC_VALUES = {
    tone.lower(): _ENCODING_TRC_VALUES[encoding]
    for tone in TONE_CHOICES
    if (encoding := display_encoding(tone)) is not None
}
_DEFAULT_TRC = "bt709"


def color_trc_for(tone_mapping: str) -> str:
    """x264 transfer= value for a display transform.

    Args:
        tone_mapping: A display transform name (case-insensitive).

    Returns:
        An x264 transfer characteristic value (passed via -x264-params),
        falling back to bt709 for an unrecognised name.
    """
    encoding = display_encoding(tone_mapping)
    return _DEFAULT_TRC if encoding is None else _ENCODING_TRC_VALUES[encoding]


def video_colour_args(tone_mapping: str, *, ocio: bool) -> list[str]:
    """Build ffmpeg filter and x264 flags for YUV matrix conversion and colour tags.

    Both modes convert RGB frames to TV-range BT.709 YUV and tag the matrix in
    both the H.264 stream and the container colr box (via frame setparams),
    so players decode YUV with the same matrix used to encode it. Basic mode
    also tags Rec.709 primaries and the selected transfer curve; OCIO mode
    leaves primaries and transfer unspecified because the config's display
    space is unknown here.
    """
    if ocio:
        return [
            "-vf",
            "scale=out_color_matrix=bt709:out_range=tv,setparams=range=tv:colorspace=bt709",
            "-x264-params",
            "colormatrix=bt709",
        ]
    trc = color_trc_for(tone_mapping)
    return [
        "-vf",
        f"scale=out_color_matrix=bt709:out_range=tv,setparams=range=tv:colorspace=bt709:color_primaries=bt709:color_trc={trc}",
        "-x264-params",
        f"colorprim=bt709:transfer={trc}:colormatrix=bt709",
    ]


_SPEED_FAST = "fast"
_SPEED_BALANCED = "balanced"
_SPEED_QUALITY = "quality"

MISSING_FRAMES_SKIP = "skip"
MISSING_FRAMES_ERROR = "error"
MISSING_FRAMES_BLACK = "black"


class EXRSequenceToPreview(SuccessFailureNode):
    """Convert an EXR frame sequence to an MP4 preview with optional color management.

    Accepts a Sequence artifact (from ScanSequenceNode), converts each frame from
    HDR float32 through exposure + tone mapping or OCIO to 8-bit sRGB PNGs, then
    encodes them with ffmpeg into an MP4. EXR→PNG conversion is parallelised across
    a configurable thread pool.
    """

    def __init__(self, name: str, metadata: dict[Any, Any] | None = None) -> None:
        super().__init__(name, metadata)

        self._sequence_param = sequence_path_parameter("sequence")
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

        self._window_mode_param = add_window_mode_parameter(self)
        self._odd_dimensions_param = ParameterString(
            name="odd_dimensions",
            default_value=sequence_canvas.ODD_PAD,
            tooltip=(
                f"{sequence_canvas.ODD_PAD} adds at most one black column/right and row/bottom. "
                f"{sequence_canvas.ODD_REQUIRE} rejects odd encoder dimensions."
            ),
            allowed_modes={ParameterMode.INPUT, ParameterMode.PROPERTY},
        )
        self._odd_dimensions_param.add_trait(Options(choices=sequence_canvas.ODD_CHOICES, allow_custom=True))
        self.add_parameter(self._odd_dimensions_param)
        self._frame_sizes_param = ParameterString(
            name="frame_sizes",
            default_value=sequence_canvas.SIZES_REQUIRE,
            tooltip=(
                f"{sequence_canvas.SIZES_REQUIRE} rejects differing rendered dimensions. {sequence_canvas.SIZES_PAD} preserves pixels "
                "at the top-left and fills right/bottom with black, without resizing. This aligns rendered images, "
                "not EXR origins. A larger canvas needs more memory; openexr.max_render_pixels limits its size."
            ),
            allowed_modes={ParameterMode.INPUT, ParameterMode.PROPERTY},
        )
        self._frame_sizes_param.add_trait(Options(choices=sequence_canvas.SIZES_CHOICES, allow_custom=True))
        self.add_parameter(self._frame_sizes_param)

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

        # No embed_color_profile: an MP4 cannot carry an ICC profile, so this
        # node signals colour through the stream tags written below instead.
        (
            self._color_mode_param,
            self._tone_mapping_param,
            self._color_params_param,
            _,
        ) = add_color_mode_parameters(self, include_embed_profile=False)

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

        self._missing_frames_param = ParameterString(
            name="missing_frames",
            default_value=MISSING_FRAMES_SKIP,
            tooltip=(
                "What to do when a frame fails to convert. 'skip' leaves it out, so the video is "
                "shorter than the sequence; 'error' fails the node and lists the failed frames; "
                "'black' puts a black frame in its place, keeping the video's timing."
            ),
            allowed_modes={ParameterMode.INPUT, ParameterMode.PROPERTY},
        )
        self._missing_frames_param.add_trait(
            Options(choices=[MISSING_FRAMES_SKIP, MISSING_FRAMES_ERROR, MISSING_FRAMES_BLACK])
        )
        self.add_parameter(self._missing_frames_param)

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
            handle_color_mode_change(
                self,
                value,
                self._tone_mapping_param.name,
                self._color_params_param.name,
                None,
            )

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
        resolved = await to_thread(resolve_sequence_path, raw)
        if isinstance(resolved, str):
            self._on_fail(resolved)
            return
        entries = resolved.entries

        total = len(entries)
        self.parameter_output_values[self._frame_count_param.name] = total
        self.publish_update_to_parameter(self._frame_count_param.name, total)
        self.parameter_output_values[self._frames_processed_param.name] = 0
        self.publish_update_to_parameter(self._frames_processed_param.name, 0)

        # --- Collect processing parameters ---
        ev = float(self.get_parameter_value(self._exposure_param.name))
        part_index = int(self.get_parameter_value(self._part_index_param.name))
        frame_rate = float(self.get_parameter_value(self._frame_rate_param.name))
        color_mode = str(self.get_parameter_value(self._color_mode_param.name))
        tone_mapping = read_tone_mapping(self, self._tone_mapping_param)
        threads = int(self.get_parameter_value(self._threads_param.name))
        fmt = str(self.get_parameter_value(self._format_param.name))
        missing_frames = str(self.get_parameter_value(self._missing_frames_param.name))
        odd_dimensions = str(self.get_parameter_value(self._odd_dimensions_param.name))
        frame_sizes = str(self.get_parameter_value(self._frame_sizes_param.name))
        try:
            window_mode = validate_window_mode(str(self.get_parameter_value(self._window_mode_param.name)))
            sequence_canvas.validate_policies(odd_dimensions, frame_sizes)
            max_pixels = render_limits.get_render_limit()
        except (ValueError, RuntimeError) as e:
            self._on_fail(str(e))
            return

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

        try:
            log_step = max(1, total * _LOG_INTERVAL_PCT // 100)

            limit_errors: list[str] = []

            def _convert(i: int, entry: SequenceEntry) -> Path:
                try:
                    return _convert_frame(
                        entry.path,
                        i,
                        temp_path,
                        part_index,
                        ev,
                        color_params,
                        tone_mapping,
                        window_mode,
                        max_pixels=max_pixels,
                    )
                except render_limits.RenderLimitError as e:
                    limit_errors.append(f"Frame '{entry.path}': {e}")
                    raise

            def _on_frame_done(frames_done: int) -> None:
                self.parameter_output_values[self._frames_processed_param.name] = frames_done
                if frames_done % log_step == 0 or frames_done == total:
                    self.publish_update_to_parameter(self._frames_processed_param.name, frames_done)
                    pct = frames_done * 100 // total
                    logger.info("%s: converted %d/%d frames (%d%%)", self.name, frames_done, total, pct)

            results, errors = await run_over_sequence(
                entries, threads=threads, work=_convert, on_completed=_on_frame_done
            )

            if limit_errors:
                self._on_fail(limit_errors[0])
                return

            if errors:
                logger.warning("%s: %d frame(s) failed conversion: %s", self.name, len(errors), "; ".join(errors))

            if errors and missing_frames == MISSING_FRAMES_ERROR:
                self._on_fail(f"{len(errors)} of {total} frame(s) failed conversion: {'; '.join(errors)}")
                return

            # Every frame failing leaves an empty temp dir, and ffmpeg then
            # reports only "Error opening input files", burying the real
            # cause in the log. Report the first frame error instead.
            if len(errors) == total:
                self._on_fail(f"All {total} frame(s) failed conversion. First error: {errors[0]}")
                return

            # ffmpeg's image2 demuxer stops at the first gap in the numbering,
            # so a failed frame mid-sequence would silently drop every frame
            # after it. Number the frames handed to ffmpeg without gaps.
            try:
                prepared = sequence_canvas.prepare_frames(
                    results,
                    [entry.path for entry in entries],
                    temp_path,
                    odd_dimensions,
                    frame_sizes,
                    missing_frames == MISSING_FRAMES_BLACK,
                    max_pixels,
                )
                encoded = _number_contiguously(prepared.frames, temp_path)
            except (OSError, ValueError) as e:
                self._on_fail(f"Failed to stage frames for ffmpeg: {e}")
                return

            # --- ffmpeg encode ---
            try:
                ffmpeg_path, _ = run.get_or_fetch_platform_executables_else_raise()
            except Exception as e:
                self._on_fail(f"ffmpeg not found: {e}")
                return

            input_pattern = str(temp_path / _FRAME_PATTERN)
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
            ]
            cmd += [
                *video_colour_args(tone_mapping, ocio=color_params is not None),
                "-pix_fmt",
                _PIX_FMT,
                "-movflags",
                _MOVFLAGS,
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

        if errors and missing_frames == MISSING_FRAMES_BLACK:
            details = (
                f"Encoded {total} frames at {frame_rate} fps → {fmt}; "
                f"{len(errors)} frame(s) failed and were replaced with black. First error: {errors[0]}"
            )
        elif errors:
            details = (
                f"Encoded {encoded} of {total} frames at {frame_rate} fps → {fmt}; "
                f"{len(errors)} frame(s) failed and were skipped. First error: {errors[0]}"
            )
        else:
            details = f"Encoded {total} frames at {frame_rate} fps → {fmt}"
        details += (
            f"; {prepared.size[0]}x{prepared.size[1]}, {frame_sizes}, {odd_dimensions}; "
            f"{prepared.padded_frames} frame(s) padded"
        )
        self._set_status_results(was_successful=True, result_details=details)
        logger.info("%s: %s", self.name, details)


def _convert_frame(  # noqa: PLR0913, PLR0917
    exr_path: str,
    frame_index: int,
    temp_dir: Path,
    part_index: int,
    ev: float,
    color_params: ColorParamsProtocol | None,
    tone_mapping: str,
    window_mode: str = WINDOW_MODE_DEFAULT,
    *,
    max_pixels: int = render_limits.MAX_RENDER_PIXELS_DEFAULT,
) -> Path:
    """Load one EXR frame, apply colour management, and write a PNG to temp_dir.

    Runs on a thread-pool worker. Raises on any failure so the caller can
    collect errors without aborting the whole run. Each frame is framed by its
    own header's windows.

    Returns:
        The written PNG, named for frame_index. Pass the frames for ffmpeg
        through `_number_contiguously` before handing it the directory.
    """
    window_mode = validate_window_mode(window_mode)
    pixels = load_exr_channels(exr_path, part_index)

    channel_names = list(pixels.keys())
    selected = select_display_channels(channel_names)
    if selected is None:
        msg = f"no displayable channels in '{exr_path}'"
        raise ValueError(msg)

    # The data window is what was decoded, so only the other modes need the header.
    framing = None
    if window_mode != WINDOW_MODE_DATA:
        framing = read_framing(window_mode, exr_path, part_index)
        # Restrict validation and processing to the channels actually being rendered.
        pixels, framing = select_window_channels({name: pixels[name] for name in selected}, framing)

    if framing is not None:
        width, height = window_size(framing)
        render_limits.check_canvas(WindowCoordinates(0, 0, width - 1, height - 1), max_pixels)
    height, width = pixels[selected[0]].shape
    render_limits.check_canvas(WindowCoordinates(0, 0, width - 1, height - 1), max_pixels)
    rgb = build_rgb_array(pixels, selected)
    rgb = apply_exposure(rgb, ev)
    rgb, _ = apply_color_management(rgb, color_params, tone_mapping)
    uint8_rgb = to_uint8(rgb)

    if framing is not None:
        uint8_rgb = apply_window_mode(uint8_rgb, framing)

    out_path = temp_dir / f"converted_{frame_index:04d}.png"
    Image.fromarray(uint8_rgb, "RGB").save(out_path)
    return out_path


def _number_contiguously(frames: list[Path], temp_dir: Path) -> int:
    """Rename frames, in order, to the gap-free `_FRAME_PATTERN` ffmpeg reads.

    Returns:
        The number of frames renamed, which is the number ffmpeg will encode.
    """
    for i, frame in enumerate(frames):
        frame.rename(temp_dir / (_FRAME_PATTERN % i))
    return len(frames)
