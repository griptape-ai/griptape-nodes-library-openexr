"""DisplayEXRPart node — render an EXR part to an 8-bit sRGB image."""

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
from griptape_nodes.node_library.library_registry import LibraryRegistry
from griptape_nodes.retained_mode.griptape_nodes import GriptapeNodes
from griptape_nodes.traits.options import Options
from PIL import Image

from griptape_nodes_openexr.exr.channel_selection import select_alpha_channel, select_display_channels
from griptape_nodes_openexr.exr.exr_header_artifact import EXRPartArtifact
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


def _find_colorspace_transform_request_type() -> type | None:
    """Discover ColorspaceTransformRequest from any loaded library — no hard import."""
    try:
        for lib_name in LibraryRegistry.list_libraries():
            library = LibraryRegistry.get_library(lib_name)
            for req_type in library.get_registered_request_handler_types():  # type: ignore[attr-defined]
                if req_type.__name__ == "ColorspaceTransformRequest":
                    logger.info("DisplayEXRPart: found ColorspaceTransformRequest handler in library %r", lib_name)
                    return req_type
        logger.info("DisplayEXRPart: ColorspaceTransformRequest handler not found in any loaded library")
    except Exception:
        logger.info("DisplayEXRPart: error scanning LibraryRegistry for ColorspaceTransformRequest", exc_info=True)
    return None


def _apply_color_management(
    rgb: np.ndarray,
    source_colorspace: str,
    display: str,
    view: str,
    tone_mapping: str,
) -> tuple[np.ndarray, str]:
    """Try the OCIO service; fall back to local tone mapping.

    Returns (output_pixels, mode_label). When OCIO succeeds the label is
    'ocio:<source>→<display>/<view>'; on fallback it is the tone_mapping value.
    """
    if source_colorspace and display and view:
        logger.info(
            "DisplayEXRPart: attempting OCIO transform — source=%r display=%r view=%r",
            source_colorspace,
            display,
            view,
        )
        req_type = _find_colorspace_transform_request_type()
        if req_type is not None:
            req = req_type(
                pixels=rgb,
                source_colorspace=source_colorspace,
                display=display,
                view=view,
            )
            try:
                result = GriptapeNodes.handle_request(req)
                if result.succeeded():
                    logger.info(
                        "DisplayEXRPart: OCIO transform succeeded (%s→%s/%s)",
                        source_colorspace,
                        display,
                        view,
                    )
                    return result.pixels, f"ocio:{source_colorspace}→{display}/{view}"  # type: ignore[attr-defined]
                logger.warning(
                    "DisplayEXRPart: OCIO transform failed — %s; falling back to %r",
                    result.result_details,
                    tone_mapping,
                )
            except TypeError:
                # The framework raises TypeError ("No manager found") when no handler is
                # registered for the request type. All other handler exceptions are caught
                # internally by the framework and returned as a failed ResultPayload.
                logger.info(
                    "DisplayEXRPart: ColorspaceTransformRequest handler not registered (TypeError); falling back to %r",
                    tone_mapping,
                )
        else:
            logger.info(
                "DisplayEXRPart: no OCIO handler found; falling back to %r tone mapping",
                tone_mapping,
            )
    else:
        logger.info(
            "DisplayEXRPart: OCIO params incomplete (source=%r display=%r view=%r); using %r tone mapping",
            source_colorspace,
            display,
            view,
            tone_mapping,
        )
    return apply_tone_mapping(rgb, tone_mapping), tone_mapping


class DisplayEXRPart(SuccessFailureNode):
    """Display an EXR part as an 8-bit sRGB PNG.

    Accepts an EXRPartArtifact, auto-selects RGB channels, applies exposure
    and optional filmic tone mapping, and outputs an ImageUrlArtifact
    saved via the project's save_node_output situation.

    When source_colorspace, display, and view are all set, the OpenColorIO
    library's ColorspaceTransformService is used instead of local tone mapping,
    with automatic fallback to the tone_mapping setting if the service is
    unavailable.
    """

    def __init__(self, name: str, metadata: dict[Any, Any] | None = None) -> None:
        super().__init__(name, metadata)

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

        self._tone_mapping_param = ParameterString(
            name="tone_mapping",
            default_value=TONE_FILMIC,
            tooltip="Fallback tone mapping when OCIO is not configured. 'filmic' applies Narkowicz 2015 curve; 'linear' clamps to [0, 1].",
            allowed_modes={ParameterMode.INPUT, ParameterMode.PROPERTY},
        )
        self._tone_mapping_param.add_trait(Options(choices=[TONE_FILMIC, TONE_LINEAR]))
        self.add_parameter(self._tone_mapping_param)

        self._source_colorspace_param = ParameterString(
            name="source_colorspace",
            default_value="",
            tooltip="OCIO source colourspace (e.g. 'ACEScg'). When this, Display, and View are all set and the OpenColorIO library is loaded, OCIO is used instead of local tone mapping.",
            allowed_modes={ParameterMode.INPUT, ParameterMode.PROPERTY},
        )
        self.add_parameter(self._source_colorspace_param)

        self._display_param = ParameterString(
            name="display",
            default_value="",
            tooltip="OCIO display target (e.g. 'sRGB'). Required for OCIO path.",
            allowed_modes={ParameterMode.INPUT, ParameterMode.PROPERTY},
        )
        self.add_parameter(self._display_param)

        self._view_param = ParameterString(
            name="view",
            default_value="",
            tooltip="OCIO view (e.g. 'ACES'). Required for OCIO path.",
            allowed_modes={ParameterMode.INPUT, ParameterMode.PROPERTY},
        )
        self.add_parameter(self._view_param)

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

        self._create_status_parameters(
            result_details_tooltip="Details about the display render result",
            result_details_placeholder="Render details will appear here.",
            parameter_group_initially_collapsed=True,
        )

    async def aprocess(self) -> None:
        self._clear_execution_status()
        self.parameter_output_values[self._image_param.name] = None

        part: EXRPartArtifact | None = self.get_parameter_value(self._part_param.name)
        if part is None:
            self._set_status_results(was_successful=False, result_details="No part provided")
            return

        channel_names = [ch.name for ch in part.channels]
        selected = select_display_channels(channel_names)
        if selected is None:
            self._set_status_results(was_successful=False, result_details="No channels found in part")
            return

        alpha_channel = select_alpha_channel(channel_names, selected)
        channels_to_load = selected + ([alpha_channel] if alpha_channel else [])

        try:
            pixels = load_exr_channels(part.file_path, part.part_index, channels_to_load)
        except (ValueError, RuntimeError) as e:
            self._set_status_results(was_successful=False, result_details=f"Failed to load channels: {e}")
            return

        try:
            rgb = self._build_rgb(pixels, selected, part.width, part.height)
        except Exception as e:
            self._set_status_results(was_successful=False, result_details=f"Failed to build RGB: {e}")
            return

        ev = float(self.get_parameter_value(self._exposure_param.name) or 0.0)
        tone_mapping = str(self.get_parameter_value(self._tone_mapping_param.name) or TONE_FILMIC)
        source_colorspace = str(self.get_parameter_value(self._source_colorspace_param.name) or "")
        display_cs = str(self.get_parameter_value(self._display_param.name) or "")
        view_cs = str(self.get_parameter_value(self._view_param.name) or "")

        rgb = apply_exposure(rgb, ev)
        try:
            rgb, tone_mode = _apply_color_management(rgb, source_colorspace, display_cs, view_cs, tone_mapping)
        except ValueError as e:
            self._set_status_results(was_successful=False, result_details=str(e))
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
            self._set_status_results(was_successful=False, result_details=f"Failed to save image: {e}")
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
