"""DisplayEXRChannel node — combine 1–4 channel artifacts into a display image."""

from __future__ import annotations

import io
import logging
from typing import Any

import numpy as np
from griptape.artifacts import ImageUrlArtifact
from griptape_nodes.exe_types.core_types import Parameter, ParameterMode
from griptape_nodes.exe_types.node_types import SuccessFailureNode
from griptape_nodes.exe_types.param_components.project_file_parameter import ProjectFileParameter
from griptape_nodes.exe_types.param_types.parameter_float import ParameterFloat
from griptape_nodes.exe_types.param_types.parameter_string import ParameterString
from griptape_nodes.traits.options import Options
from PIL import Image

from griptape_nodes_openexr.exr.exr_header_artifact import EXRChannelArtifact
from griptape_nodes_openexr.exr.exr_io import load_exr_channels
from griptape_nodes_openexr.exr.tone_mapping import (
    TONE_FILMIC,
    TONE_LINEAR,
    apply_exposure,
    apply_tone_mapping,
    to_uint8_srgb,
)

logger = logging.getLogger("griptape_nodes")

_EV_MIN = -10.0
_EV_MAX = 10.0


class DisplayEXRChannel(SuccessFailureNode):
    """Combine 1–4 EXR channel artifacts into an 8-bit sRGB (or RGBA) PNG.

    Each of the R, G, B, A input slots accepts an optional EXRChannelArtifact.
    Channels may originate from different EXR files or different parts.
    At least one RGB slot must be connected; the alpha slot is always optional.

    Single RGB channel → placed in its colour plane (R-only → red, etc.). Missing RGB slots → zero-filled plane.
    Connected alpha slot → RGBA output PNG.
    """

    def __init__(self, name: str, metadata: dict[Any, Any] | None = None) -> None:
        super().__init__(name, metadata)

        self._channel_r_param = Parameter(
            name="channel_r",
            input_types=["EXRChannelArtifact"],
            type="EXRChannelArtifact",
            tooltip="EXR channel to map to the red plane. Optional.",
            allowed_modes={ParameterMode.INPUT},
        )
        self.add_parameter(self._channel_r_param)

        self._channel_g_param = Parameter(
            name="channel_g",
            input_types=["EXRChannelArtifact"],
            type="EXRChannelArtifact",
            tooltip="EXR channel to map to the green plane. Optional.",
            allowed_modes={ParameterMode.INPUT},
        )
        self.add_parameter(self._channel_g_param)

        self._channel_b_param = Parameter(
            name="channel_b",
            input_types=["EXRChannelArtifact"],
            type="EXRChannelArtifact",
            tooltip="EXR channel to map to the blue plane. Optional.",
            allowed_modes={ParameterMode.INPUT},
        )
        self.add_parameter(self._channel_b_param)

        self._channel_a_param = Parameter(
            name="channel_a",
            input_types=["EXRChannelArtifact"],
            type="EXRChannelArtifact",
            tooltip="EXR channel to use as alpha. Optional; when connected, output is RGBA.",
            allowed_modes={ParameterMode.INPUT},
        )
        self.add_parameter(self._channel_a_param)

        self._exposure_param = ParameterFloat(
            name="exposure",
            default_value=0.0,
            min_val=_EV_MIN,
            max_val=_EV_MAX,
            step=0.1,
            slider=True,
            tooltip="Exposure in EV stops applied before tone mapping",
            allowed_modes={ParameterMode.INPUT, ParameterMode.PROPERTY},
        )
        self.add_parameter(self._exposure_param)

        self._tone_mapping_param = ParameterString(
            name="tone_mapping",
            default_value=TONE_FILMIC,
            tooltip="Tone mapping mode. 'filmic' applies Narkowicz 2015 curve; 'linear' clamps to [0, 1].",
            allowed_modes={ParameterMode.INPUT, ParameterMode.PROPERTY},
        )
        self._tone_mapping_param.add_trait(Options(choices=[TONE_FILMIC, TONE_LINEAR]))
        self.add_parameter(self._tone_mapping_param)

        self._background_param = Parameter(
            name="background",
            input_types=["ImageArtifact", "ImageUrlArtifact", "str"],
            type="ImageArtifact",
            tooltip="Background image for alpha compositing (A-over-B). Ignored when no alpha channel is connected.",
            allowed_modes={ParameterMode.INPUT},
        )
        self.add_parameter(self._background_param)

        self._image_param = Parameter(
            name="image",
            type="ImageUrlArtifact",
            output_type="ImageUrlArtifact",
            tooltip="8-bit sRGB (or RGBA) PNG for in-canvas display",
            allowed_modes={ParameterMode.OUTPUT},
            ui_options={"expander": True, "pulse_on_run": True},
        )
        self.add_parameter(self._image_param)

        self._output_file = ProjectFileParameter(
            node=self,
            name="output_file",
            default_filename="display_exr_channel.png",
        )
        self._output_file.add_parameter()

        self._create_status_parameters(
            result_details_tooltip="Details about the display render result",
            result_details_placeholder="Render details will appear here.",
            parameter_group_initially_collapsed=True,
        )

    def _on_fail_handler(self, details: str) -> None:
        self.parameter_output_values[self._image_param.name] = None
        self._set_status_results(was_successful=False, result_details=details)

    async def aprocess(self) -> None:
        self._clear_execution_status()

        channel_r: EXRChannelArtifact | None = self.get_parameter_value(self._channel_r_param.name)
        channel_g: EXRChannelArtifact | None = self.get_parameter_value(self._channel_g_param.name)
        channel_b: EXRChannelArtifact | None = self.get_parameter_value(self._channel_b_param.name)
        channel_a: EXRChannelArtifact | None = self.get_parameter_value(self._channel_a_param.name)
        background = self.get_parameter_value(self._background_param.name)

        rgb_slots: dict[str, EXRChannelArtifact] = {
            slot: artifact
            for slot, artifact in {"R": channel_r, "G": channel_g, "B": channel_b}.items()
            if artifact is not None
        }
        if not rgb_slots:
            self._on_fail_handler("At least one RGB channel slot must be connected")
            return

        pixels: dict[str, np.ndarray] = {}
        for slot, artifact in rgb_slots.items():
            try:
                loaded = load_exr_channels(artifact.file_path, artifact.part_index, [artifact.channel.name])
            except (ValueError, RuntimeError) as e:
                self._on_fail_handler(f"Failed to load {slot} channel: {e}")
                return
            pixels[slot] = loaded[artifact.channel.name]

        alpha_plane: np.ndarray | None = None
        if channel_a is not None:
            try:
                loaded_a = load_exr_channels(channel_a.file_path, channel_a.part_index, [channel_a.channel.name])
            except (ValueError, RuntimeError) as e:
                self._on_fail_handler(f"Failed to load A channel: {e}")
                return
            alpha_plane = loaded_a[channel_a.channel.name]

        all_arrays = list(pixels.values()) + ([alpha_plane] if alpha_plane is not None else [])
        shapes = [arr.shape for arr in all_arrays]
        if len(set(shapes)) > 1:
            shape_detail = {slot: arr.shape for slot, arr in pixels.items()}
            if alpha_plane is not None:
                shape_detail["A"] = alpha_plane.shape
            self._on_fail_handler(f"Channel dimensions do not match: {shape_detail}")
            return

        height, width = shapes[0]

        try:
            rgb = _build_rgb(pixels, height, width)
        except Exception as e:
            self._on_fail_handler(f"Failed to build RGB: {e}")
            return

        ev = float(self.get_parameter_value(self._exposure_param.name) or 0.0)
        tone_mapping = str(self.get_parameter_value(self._tone_mapping_param.name) or TONE_FILMIC)

        rgb = apply_exposure(rgb, ev)
        try:
            rgb = apply_tone_mapping(rgb, tone_mapping)
        except ValueError as e:
            self._on_fail_handler(str(e))
            return
        uint8_rgb = to_uint8_srgb(rgb)

        alpha_f32 = alpha_plane.reshape(height, width) if alpha_plane is not None else None

        bg_rgb_f32: np.ndarray | None = None
        if background is not None:
            if alpha_f32 is None:
                logger.warning("DisplayEXRChannel '%s': background ignored — no alpha channel connected", self.name)
            else:
                try:
                    bg_rgb_f32 = _load_background_rgb(background, width, height)
                except RuntimeError as e:
                    self._on_fail_handler(str(e))
                    return

        uint8 = _composite(uint8_rgb, alpha_f32, bg_rgb_f32)

        try:
            png_bytes = _ndarray_to_png(uint8)
            dest = self._output_file.build_file()
            saved = dest.write_bytes(png_bytes)
            artifact = ImageUrlArtifact(saved.location)
        except Exception as e:
            self._on_fail_handler(f"Failed to save image: {e}")
            return

        self.parameter_output_values[self._image_param.name] = artifact
        self.publish_update_to_parameter(self._image_param.name, artifact)

        tone_mode = tone_mapping
        slot_info = ", ".join(f"{slot}={rgb_slots[slot].channel.name}" for slot in sorted(rgb_slots))
        alpha_info = f", A={channel_a.channel.name}" if channel_a else ""
        bg_info = ", bg=composite" if bg_rgb_f32 is not None else ""
        details = f"Rendered {width}×{height}, channels: [{slot_info}{alpha_info}]{bg_info}, EV={ev:+.1f}, {tone_mode}"
        self._set_status_results(was_successful=True, result_details=details)
        logger.info("DisplayEXRChannel '%s': %s", self.name, details)


def _build_rgb(pixels: dict[str, np.ndarray], height: int, width: int) -> np.ndarray:
    """Stack slot pixel data into a (H, W, 3) float32 RGB array.

    Each slot is placed in its corresponding colour plane; missing slots are zero-filled.
    R-only → red image, G-only → green, B-only → blue.
    """
    zero = np.zeros((height, width), dtype=np.float32)
    r = pixels.get("R", zero).reshape(height, width)
    g = pixels.get("G", zero).reshape(height, width)
    b = pixels.get("B", zero).reshape(height, width)
    return np.stack([r, g, b], axis=-1)


def _load_background_rgb(artifact: Any, width: int, height: int) -> np.ndarray:
    from griptape.artifacts import ImageArtifact, ImageUrlArtifact
    from griptape_nodes.files.file import File, FileLoadError

    try:
        if isinstance(artifact, ImageArtifact):
            img_bytes = artifact.value
        elif isinstance(artifact, ImageUrlArtifact):
            img_bytes = File(artifact.value).read_bytes()
        elif isinstance(artifact, str):
            img_bytes = File(artifact).read_bytes()
        else:
            msg = f"Unsupported background artifact type: {type(artifact)}"
            raise RuntimeError(msg)
        img = Image.open(io.BytesIO(img_bytes)).convert("RGB")
        img = img.resize((width, height), Image.Resampling.LANCZOS)
        return np.asarray(img, dtype=np.float32) / 255.0
    except (RuntimeError, FileLoadError):
        raise
    except Exception as e:
        msg = f"Failed to load background: {e}"
        raise RuntimeError(msg) from e


def _compose_alpha(uint8_rgb: np.ndarray, alpha_plane: np.ndarray | None) -> np.ndarray:
    """Append an alpha channel to a uint8 RGB array, or return it unchanged.

    When alpha_plane is None the input is returned as-is (RGB).
    When alpha_plane is provided it is clamped to [0, 1], scaled to uint8, and
    appended as a 4th channel (RGBA).
    """
    if alpha_plane is None:
        return uint8_rgb
    alpha_uint8 = (np.clip(alpha_plane, 0.0, 1.0) * 255.0 + 0.5).astype(np.uint8)[..., np.newaxis]
    return np.concatenate([uint8_rgb, alpha_uint8], axis=-1)


def _composite(
    uint8_rgb: np.ndarray,
    alpha_plane: np.ndarray | None,
    bg_rgb_f32: np.ndarray | None,
) -> np.ndarray:
    if alpha_plane is None:
        return uint8_rgb
    if bg_rgb_f32 is None:
        return _compose_alpha(uint8_rgb, alpha_plane)
    a = np.clip(alpha_plane, 0.0, 1.0)[..., np.newaxis]
    fg = uint8_rgb.astype(np.float32) / 255.0
    out = np.clip(fg * a + bg_rgb_f32 * (1.0 - a), 0.0, 1.0)
    return (out * 255.0 + 0.5).astype(np.uint8)


def _ndarray_to_png(uint8: np.ndarray) -> bytes:
    """Encode a (H, W, 3) or (H, W, 4) uint8 array as PNG bytes.

    Caller guarantees exactly 3 (RGB) or 4 (RGBA) channels — _build_rgb always
    produces 3-channel output; alpha is optionally appended as a 4th channel.
    """
    assert uint8.ndim == 3 and uint8.shape[2] in (3, 4), uint8.shape  # noqa: S101
    mode = "RGBA" if uint8.shape[2] == 4 else "RGB"  # noqa: PLR2004
    buf = io.BytesIO()
    Image.fromarray(uint8, mode=mode).save(buf, format="PNG")
    return buf.getvalue()
