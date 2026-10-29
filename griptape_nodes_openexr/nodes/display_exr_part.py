"""DisplayEXRPart node — render an EXR part to an 8-bit sRGB image."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from griptape.artifacts import ImageUrlArtifact
from griptape_nodes.exe_types.core_types import NodeMessageResult, Parameter, ParameterMode
from griptape_nodes.exe_types.node_types import SuccessFailureNode
from griptape_nodes.exe_types.param_components.project_file_parameter import ProjectFileParameter
from griptape_nodes.exe_types.param_types.parameter_button import ParameterButton
from griptape_nodes.exe_types.param_types.parameter_float import ParameterFloat

from griptape_nodes_openexr.exr.channel_selection import select_alpha_channel, select_display_channels
from griptape_nodes_openexr.exr.exr_header_artifact import EXRPartArtifact
from griptape_nodes_openexr.exr.ocio_helpers import (
    COLOR_MODE_BASIC,
    COLOR_MODE_OCIO,
    ColorParamsProtocol,
    add_color_mode_parameters,
    handle_color_mode_change,
)
from griptape_nodes_openexr.exr.rendering import render_part_png
from griptape_nodes_openexr.exr.tone_mapping import EV_MAX, EV_MIN, TONE_DEFAULT
from griptape_nodes_openexr.exr.viewer_launcher import handle_viewer_button_click

if TYPE_CHECKING:
    from griptape_nodes.traits.button import Button, ButtonDetailsMessagePayload

logger = logging.getLogger("griptape_nodes")


class DisplayEXRPart(SuccessFailureNode):
    """Display an EXR part as an 8-bit sRGB PNG.

    Accepts an EXRPartArtifact, auto-selects RGB channels, applies exposure
    and colour management, and outputs an ImageUrlArtifact saved via the
    project's save_node_output situation.

    In 'basic' mode, a local display transform (sRGB, rec709, rec1886 or raw) is applied.
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
            min_val=EV_MIN,
            max_val=EV_MAX,
            step=0.1,
            slider=True,
            tooltip="Exposure in EV stops applied before tone mapping or OCIO transform",
            allowed_modes={ParameterMode.INPUT, ParameterMode.PROPERTY},
        )
        self.add_parameter(self._exposure_param)

        self._color_mode_param, self._tone_mapping_param, self._color_params_param = add_color_mode_parameters(
            self,
            color_mode_tooltip="Color management mode. 'basic' uses local tone mapping; 'ocio' uses the connected OCIOColorParamsArtifact.",
            color_params_tooltip="OCIO color parameters artifact (source colorspace, display, view). Required when color_mode is 'ocio'.",
        )

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

    def after_value_set(self, parameter: Parameter, value: Any) -> None:
        if parameter.name == self._color_mode_param.name:
            handle_color_mode_change(self, value, self._tone_mapping_param.name, self._color_params_param.name)

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

        ev = float(self.get_parameter_value(self._exposure_param.name) or 0.0)
        tone_mapping = str(self.get_parameter_value(self._tone_mapping_param.name) or TONE_DEFAULT)
        color_mode = str(self.get_parameter_value(self._color_mode_param.name) or COLOR_MODE_BASIC)

        color_params: ColorParamsProtocol | None = None
        if color_mode == COLOR_MODE_OCIO:
            color_params = self.get_parameter_value(self._color_params_param.name)  # type: ignore[assignment]
            if color_params is None:
                self._on_fail_handler("OCIO mode requires a connected color_params artifact.")
                return

        try:
            png_bytes, tone_mode = render_part_png(
                file_path=part.file_path,
                part_index=part.part_index,
                selected=selected,
                alpha_channel=alpha_channel,
                ev=ev,
                color_params=color_params,
                tone_mapping=tone_mapping,
            )
        except (ValueError, RuntimeError) as e:
            self._on_fail_handler(str(e))
            return

        try:
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
