"""DisplayEXR node — tone-mapped composite preview of a single EXR part or layer.

Two-phase design:
- after_value_set: populate metadata outputs and dynamic layer group immediately
  when a part or layer artifact is connected, with no pixel I/O.
- aprocess: load pixels, tone map, write PNG, publish ImageUrlArtifact.

Accepts both EXRPartHeaderArtifact and EXRLayerArtifact as input:
- Part: renders the layer selected by the layer_name property (or default composite).
- Layer: renders that layer directly; layer_name property is hidden.

Deep EXRs (deepscanline / deeptiled) are flattened automatically via
ImageBufAlgo.flatten() before tone-mapping.
"""

from __future__ import annotations

import logging
from io import BytesIO
from typing import Any

from griptape.artifacts import ImageUrlArtifact
from griptape_nodes.exe_types.core_types import Parameter, ParameterGroup, ParameterMode
from griptape_nodes.exe_types.node_types import SuccessFailureNode
from griptape_nodes.exe_types.param_components.project_file_parameter import ProjectFileParameter
from griptape_nodes.exe_types.param_types.parameter_float import ParameterFloat
from griptape_nodes.exe_types.param_types.parameter_image import ParameterImage
from griptape_nodes.exe_types.param_types.parameter_int import ParameterInt
from griptape_nodes.exe_types.param_types.parameter_string import ParameterString
from griptape_nodes.traits.options import Options

from griptape_nodes_openexr.artifact_providers.exr_preview_generators import resolve_layer_channels
from griptape_nodes_openexr.exr.exr_header_artifact import EXRLayerArtifact, EXRPartHeaderArtifact
from griptape_nodes_openexr.exr.exr_io import scan_exr_header
from griptape_nodes_openexr.exr.exr_pixel_io import (
    apply_exposure,
    detect_colorspace,
    load_layer_pixels,
    to_pil_rgb,
    tone_map,
)
from griptape_nodes_openexr.exr.exr_types import EXRChannelInfo, parse_channel_name
from griptape_nodes_openexr.exr.strategies.registry import get_strategy

logger = logging.getLogger("griptape_nodes")

_TONE_MAPPING_CHOICES = ["simple", "reinhard", "filmic"]
_DEFAULT_TONE_MAPPING = "simple"
_DEFAULT_LAYER_LABEL = "default"
_LAYER_PREFIX = "layer_"
_MAX_PREVIEW_DIM = 2048


class DisplayEXR(SuccessFailureNode):
    """Render a tone-mapped sRGB preview from an EXR part or layer.

    Accepts EXRPartHeaderArtifact (renders selected or default layer) or
    EXRLayerArtifact (renders that layer directly). Deep EXRs are rejected
    with a clear message pointing to a future DeepToFlat node.
    """

    def __init__(self, name: str, metadata: dict[Any, Any] | None = None) -> None:
        super().__init__(name, metadata)

        # --- Input (accepts part or layer) ---

        self._part_param = Parameter(
            name="exr",
            type="EXRPartHeaderArtifact",
            input_types=["EXRPartHeaderArtifact", "EXRLayerArtifact"],
            tooltip="EXR part or layer to display",
            allowed_modes={ParameterMode.INPUT},
        )
        self.add_parameter(self._part_param)

        # --- Rendering controls ---

        self._tone_mapping_param = ParameterString(
            name="tone_mapping",
            default_value=_DEFAULT_TONE_MAPPING,
            tooltip="Tone mapping method for sRGB preview",
            allowed_modes={ParameterMode.INPUT, ParameterMode.PROPERTY},
        )
        self._tone_mapping_param.add_trait(Options(choices=_TONE_MAPPING_CHOICES))
        self.add_parameter(self._tone_mapping_param)

        self._exposure_param = ParameterFloat(
            name="exposure",
            default_value=0.0,
            tooltip="Exposure adjustment in stops (positive = brighter)",
            allowed_modes={ParameterMode.INPUT, ParameterMode.PROPERTY},
            slider=True,
            min_val=-10.0,
            max_val=10.0,
            step=0.1,
        )
        self.add_parameter(self._exposure_param)

        self._layer_name_param = ParameterString(
            name="layer_name",
            default_value="",
            tooltip="Layer to render. Empty string renders the default composite (top-level RGBA or first layer). Hidden when a layer is directly connected.",
            allowed_modes={ParameterMode.INPUT, ParameterMode.PROPERTY},
        )
        self.add_parameter(self._layer_name_param)

        # --- File output ---

        self._output_file = ProjectFileParameter(
            node=self,
            name="output_file",
            default_filename="display_exr.png",
        )
        self._output_file.add_parameter()

        # --- Preview output ---

        self._output_param = ParameterImage(
            name="output",
            display_name="sRGB Preview",
            default_value=None,
            tooltip="Tone-mapped sRGB preview of the EXR part or layer",
            allowed_modes={ParameterMode.OUTPUT},
            ui_options={"pulse_on_run": True},
        )
        self.add_parameter(self._output_param)

        # --- Metadata outputs (collapsed group) ---

        with ParameterGroup(name="EXR Info") as info_group:
            info_group.ui_options = {"collapsed": True}

            self._part_name_param = ParameterString(
                name="part_name",
                display_name="Part Name",
                default_value="",
                tooltip="Part name (empty for unnamed parts)",
                allowed_modes={ParameterMode.OUTPUT},
            )
            self._width_param = ParameterInt(
                name="width",
                default_value=0,
                tooltip="Image width in pixels",
                allowed_modes={ParameterMode.OUTPUT},
            )
            self._height_param = ParameterInt(
                name="height",
                default_value=0,
                tooltip="Image height in pixels",
                allowed_modes={ParameterMode.OUTPUT},
            )
            self._colorspace_param = ParameterString(
                name="detected_colorspace",
                display_name="Detected Colorspace",
                default_value="",
                tooltip="Colorspace detected from oiio:ColorSpace or chromaticities",
                allowed_modes={ParameterMode.OUTPUT},
            )

        self.add_node_element(info_group)

        # --- Dynamic layers group ---

        self._layers_group = ParameterGroup(name="exr_layers")
        self._layers_group.ui_options = {"display_name": "Layers"}
        self.add_node_element(self._layers_group)

        self._create_status_parameters(
            result_details_tooltip="Details about the render result",
            result_details_placeholder="Render a connected EXR to see results here.",
            parameter_group_initially_collapsed=True,
        )

    # --- Lifecycle ---

    def after_value_set(self, parameter: Parameter, value: Any) -> None:
        if parameter.name == "exr" and isinstance(value, (EXRPartHeaderArtifact, EXRLayerArtifact)):
            part, pinned_channels = self._resolve_input(value)
            self._update_metadata(part)
            self._populate_layers(part)
            self._layer_name_param.hide = pinned_channels is not None
        return super().after_value_set(parameter, value)

    async def aprocess(self) -> None:
        self._clear_execution_status()

        value = self.get_parameter_value("exr")
        if not isinstance(value, (EXRPartHeaderArtifact, EXRLayerArtifact)):
            self._set_status_results(was_successful=False, result_details="No EXR part or layer connected")
            return

        try:
            part, pinned_channels = self._resolve_input(value)

            self._update_metadata(part)
            self._populate_layers(part)

            if pinned_channels is not None:
                channels = pinned_channels
            else:
                layer_name_raw = self.get_parameter_value("layer_name") or ""
                layer_name = layer_name_raw if layer_name_raw else None
                strategy = get_strategy("nuke")
                exr_data = scan_exr_header(part.file_path, strategy)
                exr_part = exr_data.parts[part.part_index]
                channels = resolve_layer_channels(exr_part, layer_name, part.file_path)

            tone_mapping_str = self.get_parameter_value("tone_mapping") or _DEFAULT_TONE_MAPPING
            exposure = float(self.get_parameter_value("exposure") or 0.0)

            indices = [ch.channel_index for ch in channels]
            pixels = load_layer_pixels(part.file_path, part.part_index, indices)

            if exposure != 0.0:
                pixels = apply_exposure(pixels, exposure)

            pixels = tone_map(pixels, tone_mapping_str)
            img = to_pil_rgb(pixels, _MAX_PREVIEW_DIM, _MAX_PREVIEW_DIM)

            buf = BytesIO()
            img.save(buf, format="PNG")

            dest = self._output_file.build_file()
            saved = dest.write_bytes(buf.getvalue())
            artifact = ImageUrlArtifact(value=saved.location)
            self.publish_update_to_parameter("output", artifact)
            self.parameter_output_values["output"] = artifact

            is_deep = part.header.storage_type.value.startswith("deep")
            depth_label = "deep, flattened" if is_deep else "flat"
            self._set_status_results(
                was_successful=True,
                result_details=f"Rendered {part.width}×{part.height} ({depth_label})",
            )
        except Exception as exc:
            self._set_status_results(was_successful=False, result_details=str(exc))
            raise

    # --- Private helpers ---

    def _resolve_input(
        self, value: EXRPartHeaderArtifact | EXRLayerArtifact
    ) -> tuple[EXRPartHeaderArtifact, list[EXRChannelInfo] | None]:
        """Extract the part and optional pinned channels from either artifact type.

        Returns:
            (part, None) for EXRPartHeaderArtifact — caller uses layer_name param.
            (part, channels) for EXRLayerArtifact — caller uses channels directly.
        """
        if isinstance(value, EXRLayerArtifact):
            return value.part, list(value.layer.channels)
        return value, None

    def _update_metadata(self, part: EXRPartHeaderArtifact) -> None:
        self.parameter_output_values["part_name"] = part.name
        self.parameter_output_values["width"] = part.width
        self.parameter_output_values["height"] = part.height
        colorspace = detect_colorspace(
            part.file_path, part.part_index, part.header.chromaticities
        )
        self.parameter_output_values["detected_colorspace"] = colorspace

    def _populate_layers(self, part: EXRPartHeaderArtifact) -> None:
        for child in list(self._layers_group.children):
            self._layers_group.remove_child(child)

        for layer in part.layers:
            layer_artifact = EXRLayerArtifact(part=part, layer=layer)
            layer_label = layer.name or _DEFAULT_LAYER_LABEL
            short_names = [parse_channel_name(ch.name).channel_name for ch in layer.channels]
            display = f"{layer_label} ({', '.join(short_names)})"

            param = Parameter(
                name=f"{_LAYER_PREFIX}{layer_label}",
                display_name=display,
                type="str",
                output_type="EXRLayerArtifact",
                default_value=layer_label,
                tooltip=f"Layer '{layer_label}' with channels: {', '.join(short_names)}",
                allowed_modes={ParameterMode.PROPERTY, ParameterMode.OUTPUT},
                settable=False,
                hide_property=True,
            )
            self._layers_group.add_child(param)
            self.parameter_output_values[param.name] = layer_artifact
