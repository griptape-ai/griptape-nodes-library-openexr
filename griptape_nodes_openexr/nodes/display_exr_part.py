"""DisplayEXRPart node — render an EXR part to an 8-bit sRGB image."""

from __future__ import annotations

import io
import logging
from typing import TYPE_CHECKING, Any

import numpy as np
from griptape.artifacts import ImageUrlArtifact
from griptape_nodes.exe_types.core_types import NodeMessageResult, Parameter, ParameterMode
from griptape_nodes.exe_types.node_types import SuccessFailureNode
from griptape_nodes.exe_types.param_components.project_file_parameter import ProjectFileParameter
from griptape_nodes.exe_types.param_types.parameter_button import ParameterButton
from griptape_nodes.exe_types.param_types.parameter_float import ParameterFloat
from griptape_nodes.exe_types.param_types.parameter_string import ParameterString
from griptape_nodes.traits.options import Options
from PIL import Image

from griptape_nodes_openexr.exr.channel_selection import select_alpha_channel, select_display_channels
from griptape_nodes_openexr.exr.exr_header_artifact import EXRPartArtifact
from griptape_nodes_openexr.exr.exr_io import load_exr_channels
from griptape_nodes_openexr.exr.ocio_helpers import (
    COLOR_MODE_BASIC,
    COLOR_MODE_OCIO,
    ColorParamsProtocol,
    apply_color_management,
    apply_color_mode_visibility,
    find_colorspace_transform_request_type,
)
from griptape_nodes_openexr.exr.tone_mapping import (
    TONE_FILMIC,
    TONE_LINEAR,
    apply_exposure,
    to_uint8_srgb,
)
from griptape_nodes_openexr.exr.viewer_launcher import handle_viewer_button_click

if TYPE_CHECKING:
    from griptape_nodes.traits.button import Button, ButtonDetailsMessagePayload

logger = logging.getLogger("griptape_nodes")

_EV_MIN = -10.0
_EV_MAX = 10.0


class DisplayEXRPart(SuccessFailureNode):
    """Display an EXR part as an 8-bit sRGB PNG.

    Accepts an EXRPartArtifact, auto-selects RGB channels, applies exposure
    and colour management, and outputs an ImageUrlArtifact saved via the
    project's save_node_output situation.

    In 'basic' mode, local filmic or linear tone mapping is applied.
    In 'ocio' mode, a connected OCIOColorParamsArtifact drives an OCIO
    display-view transform. OCIO failures are always surfaced loudly.

    The default color_mode is set to 'ocio' when the OpenColorIO library is
    detected at node instantiation time, otherwise 'basic'.
    """

    def __init__(self, name: str, metadata: dict[Any, Any] | None = None) -> None:
        super().__init__(name, metadata)

        self._current_exr_path: str | None = None

        self._part_param = Parameter(
            name="part",
            input_types=["EXRPartArtifact"],
            type="EXRPartArtifact",
            tooltip="EXR part to display. Assumes scene-linear HDR data.",
            allowed_modes={ParameterMode.INPUT},
        )
        self.add_parameter(self._part_param)

        self._exposure_param = ParameterFloat(
            name="exposure",
            default_value=0.0,
            min_val=_EV_MIN,
            max_val=_EV_MAX,
            step=0.1,
            slider=True,
            tooltip="Exposure in EV stops applied before tone mapping or OCIO transform",
            allowed_modes={ParameterMode.INPUT, ParameterMode.PROPERTY},
        )
        self.add_parameter(self._exposure_param)

        _default_mode = COLOR_MODE_OCIO if find_colorspace_transform_request_type() is not None else COLOR_MODE_BASIC

        self._color_mode_param = ParameterString(
            name="color_mode",
            default_value=_default_mode,
            tooltip="Color management mode. 'basic' uses local tone mapping; 'ocio' uses the connected OCIOColorParamsArtifact.",
            allowed_modes={ParameterMode.INPUT, ParameterMode.PROPERTY},
        )
        self._color_mode_param.add_trait(Options(choices=[COLOR_MODE_BASIC, COLOR_MODE_OCIO]))
        self.add_parameter(self._color_mode_param)

        _ocio_active = _default_mode == COLOR_MODE_OCIO

        self._tone_mapping_param = ParameterString(
            name="tone_mapping",
            default_value=TONE_FILMIC,
            tooltip="Local tone mapping when color_mode is 'basic'. 'filmic' applies Narkowicz 2015 curve; 'linear' clamps to [0, 1].",
            allowed_modes={ParameterMode.INPUT, ParameterMode.PROPERTY},
            ui_options={"hide": _ocio_active},
        )
        self._tone_mapping_param.add_trait(Options(choices=[TONE_FILMIC, TONE_LINEAR]))
        self.add_parameter(self._tone_mapping_param)

        self._color_params_param = Parameter(
            name="color_params",
            input_types=["OCIOColorParamsArtifact"],
            type="OCIOColorParamsArtifact",
            tooltip="OCIO color parameters artifact (source colorspace, display, view). Required when color_mode is 'ocio'.",
            allowed_modes={ParameterMode.INPUT},
            ui_options={"hide": not _ocio_active},
        )
        self.add_parameter(self._color_params_param)

        self._image_param = Parameter(
            name="image",
            type="ImageUrlArtifact",
            output_type="ImageUrlArtifact",
            tooltip="8-bit sRGB PNG for in-canvas display",
            allowed_modes={ParameterMode.OUTPUT},
            ui_options={"expander": True, "pulse_on_run": True},
        )
        self.add_parameter(self._image_param)

        self._output_file = ProjectFileParameter(
            node=self,
            name="output_file",
            default_filename="display_exr.png",
        )
        self._output_file.add_parameter()

        self._open_viewer_param = ParameterButton(
            name="open_in_viewer",
            label="Open in external viewer",
            tooltip="Open the source EXR file in the configured external viewer (or OS default)",
            on_click=self._on_open_viewer_click,
        )
        self.add_parameter(self._open_viewer_param)

        self._create_status_parameters(
            result_details_tooltip="Details about the display render result",
            result_details_placeholder="Render details will appear here.",
            parameter_group_initially_collapsed=True,
        )

        # after_value_set is not called by the framework when restoring saved values,
        # so read the persisted mode from metadata to apply the correct initial visibility.
        _restored_mode = self.metadata.get("_color_mode", _default_mode)
        apply_color_mode_visibility(
            self,
            _restored_mode == COLOR_MODE_OCIO,
            self._tone_mapping_param.name,
            self._color_params_param.name,
        )

    def after_value_set(self, parameter: Parameter, value: Any) -> None:
        if parameter.name == self._color_mode_param.name:
            self.metadata["_color_mode"] = value
            apply_color_mode_visibility(
                self,
                value == COLOR_MODE_OCIO,
                self._tone_mapping_param.name,
                self._color_params_param.name,
            )

    def _on_open_viewer_click(
        self,
        button: Button,
        button_details: ButtonDetailsMessagePayload,  # noqa: ARG002
    ) -> NodeMessageResult | None:
        return handle_viewer_button_click(self.name, self._current_exr_path)

    def _on_fail_handler(self, details: str) -> None:
        self._current_exr_path = None
        self.parameter_output_values[self._image_param.name] = None
        self._set_status_results(was_successful=False, result_details=details)

    async def aprocess(self) -> None:
        self._clear_execution_status()
        self._current_exr_path = None
        self.parameter_output_values[self._image_param.name] = None

        part: EXRPartArtifact | None = self.get_parameter_value(self._part_param.name)
        if part is None:
            self._set_status_results(was_successful=False, result_details="No part provided")
            return

        self._current_exr_path = part.file_path

        channel_names = [ch.name for ch in part.channels]
        selected = select_display_channels(channel_names)
        if selected is None:
            self._on_fail_handler("No channels found in part")
            return

        alpha_channel = select_alpha_channel(channel_names, selected)
        channels_to_load = selected + ([alpha_channel] if alpha_channel else [])

        try:
            pixels = load_exr_channels(part.file_path, part.part_index, channels_to_load)
        except (ValueError, RuntimeError) as e:
            self._on_fail_handler(f"Failed to load channels: {e}")
            return

        try:
            rgb = self._build_rgb(pixels, selected, part.width, part.height)
        except Exception as e:
            self._on_fail_handler(f"Failed to build RGB: {e}")
            return

        ev = float(self.get_parameter_value(self._exposure_param.name) or 0.0)
        tone_mapping = str(self.get_parameter_value(self._tone_mapping_param.name) or TONE_FILMIC)
        color_mode = str(self.get_parameter_value(self._color_mode_param.name) or COLOR_MODE_BASIC)

        color_params: ColorParamsProtocol | None = None
        if color_mode == COLOR_MODE_OCIO:
            color_params = self.get_parameter_value(self._color_params_param.name)  # type: ignore[assignment]
            if color_params is None:
                self._on_fail_handler("OCIO mode requires a connected color_params artifact.")
                return

        rgb = apply_exposure(rgb, ev)
        try:
            rgb, tone_mode = apply_color_management(rgb, color_params, tone_mapping)
        except ValueError as e:
            self._on_fail_handler(str(e))
            return
        uint8_rgb = to_uint8_srgb(rgb)

        if alpha_channel:
            alpha_plane = np.clip(pixels[alpha_channel].reshape(part.height, part.width), 0.0, 1.0)
            alpha_uint8 = (alpha_plane * 255.0 + 0.5).astype(np.uint8)[..., np.newaxis]
            uint8 = np.concatenate([uint8_rgb, alpha_uint8], axis=-1)
        else:
            uint8 = uint8_rgb

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
        label = part.name or f"part {part.part_index}"
        alpha_info = f", alpha: {alpha_channel}" if alpha_channel else ""
        details = f"Rendered '{label}' — {part.width}×{part.height}, channels: {selected}{alpha_info}, EV={ev:+.1f}, {tone_mode}"
        self._set_status_results(was_successful=True, result_details=details)
        logger.info("DisplayEXRPart '%s': %s", self.name, details)

    @staticmethod
    def _build_rgb(
        pixels: dict[str, np.ndarray],
        selected: list[str],
        width: int,
        height: int,
    ) -> np.ndarray:
        """Stack selected channels into a (H, W, 3) float32 array."""
        if len(selected) == 1:
            ch = pixels[selected[0]].reshape(height, width)
            return np.stack([ch, ch, ch], axis=-1)

        planes = [pixels[name].reshape(height, width) for name in selected]
        return np.stack(planes, axis=-1)


def _ndarray_to_png(uint8: np.ndarray) -> bytes:
    """Encode a (H, W, 3) or (H, W, 4) uint8 array as PNG bytes."""
    if uint8.ndim != 3 or uint8.shape[2] not in (3, 4):  # noqa: PLR2004
        msg = f"expected (H, W, 3|4) uint8 array, got shape {uint8.shape}"
        raise ValueError(msg)
    mode = "RGBA" if uint8.shape[2] == 4 else "RGB"  # noqa: PLR2004
    buf = io.BytesIO()
    Image.fromarray(uint8, mode=mode).save(buf, format="PNG")
    return buf.getvalue()
