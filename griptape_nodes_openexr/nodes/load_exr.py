"""LoadEXR node - parse EXR header metadata without loading pixels.

Architecture: Two-phase scanning.
- Phase 1 (after_value_set on file_path): Scan headers only.
  Populates all metadata outputs and dynamic parameter groups.
- Phase 2 (aprocess): Validate state, set success/failure status.
  No pixel I/O ever occurs in this node.
"""

from __future__ import annotations

import json
import logging
import pathlib
from typing import Any

from griptape_nodes.exe_types.core_types import (
    Parameter,
    ParameterGroup,
    ParameterMode,
)
from griptape_nodes.exe_types.node_types import SuccessFailureNode
from griptape_nodes.exe_types.param_types.parameter_float import ParameterFloat
from griptape_nodes.exe_types.param_types.parameter_int import ParameterInt
from griptape_nodes.exe_types.param_types.parameter_string import ParameterString
from griptape_nodes.traits.file_system_picker import FileSystemPicker

from griptape_nodes_openexr.exr.exr_header_artifact import (
    EXRChannelArtifact,
    EXRPartArtifact,
)
from griptape_nodes_openexr.exr.exr_io import scan_exr_header
from griptape_nodes_openexr.exr.exr_types import EXRData, EXRPart

logger = logging.getLogger("griptape_nodes")

_PART_PREFIX = "part_"
_CHANNEL_PREFIX = "channel_"


class LoadEXR(SuccessFailureNode):
    """Parse an OpenEXR file's header and expose metadata for downstream nodes.

    No pixel data is loaded. Outputs structured artifacts plus scalar
    convenience outputs for common fields (width, height, compression, etc.)
    and dynamic groups for parts and channels.
    """

    def __init__(self, name: str, metadata: dict[Any, Any] | None = None) -> None:
        super().__init__(name, metadata)

        self._cached_exr_data: EXRData | None = None

        # --- File input ---

        self._file_path_param = ParameterString(
            name="file_path",
            default_value="",
            tooltip="Path to the EXR file",
            allowed_modes={ParameterMode.INPUT, ParameterMode.PROPERTY},
        )
        self._file_path_param.add_trait(
            FileSystemPicker(
                allow_files=True,
                allow_directories=False,
                file_extensions=[".exr"],
            )
        )
        self.add_parameter(self._file_path_param)

        # --- EXR Info group (collapsed) ---

        with ParameterGroup(name="EXR Info") as exr_info_group:
            exr_info_group.ui_options = {"collapsed": True}

            self._image_width_param = ParameterInt(
                name="image_width",
                display_name="Image Width",
                default_value=0,
                tooltip="Image width in pixels",
                allowed_modes={ParameterMode.OUTPUT},
            )
            self._image_height_param = ParameterInt(
                name="image_height",
                display_name="Image Height",
                default_value=0,
                tooltip="Image height in pixels",
                allowed_modes={ParameterMode.OUTPUT},
            )
            self._part_count_param = ParameterInt(
                name="part_count",
                display_name="Part Count",
                default_value=0,
                tooltip="Number of parts in the EXR file",
                allowed_modes={ParameterMode.OUTPUT},
            )
            self._channel_count_param = ParameterInt(
                name="channel_count",
                display_name="Channel Count",
                default_value=0,
                tooltip="Total channels across all parts",
                allowed_modes={ParameterMode.OUTPUT},
            )
            self._compression_param = ParameterString(
                name="compression",
                default_value="",
                tooltip="EXR compression type",
                allowed_modes={ParameterMode.OUTPUT},
            )
            self._storage_type_param = ParameterString(
                name="storage_type",
                display_name="Storage Type",
                default_value="",
                tooltip="Storage mode (scanlineimage / tiledimage / deepscanline / deeptiled)",
                allowed_modes={ParameterMode.OUTPUT},
            )
            self._pixel_aspect_ratio_param = ParameterFloat(
                name="pixel_aspect_ratio",
                display_name="Pixel Aspect Ratio",
                default_value=1.0,
                tooltip="Pixel width/height ratio (1.0 = square pixels)",
                allowed_modes={ParameterMode.OUTPUT},
            )
            self._data_window_param = ParameterString(
                name="data_window",
                display_name="Data Window",
                default_value="",
                tooltip="Bounding box of pixel data (xmin,ymin - xmax,ymax)",
                allowed_modes={ParameterMode.OUTPUT},
            )
            self._display_window_param = ParameterString(
                name="display_window",
                display_name="Display Window",
                default_value="",
                tooltip="Display region (xmin,ymin - xmax,ymax)",
                allowed_modes={ParameterMode.OUTPUT},
            )
            self._time_code_param = ParameterString(
                name="time_code",
                display_name="Time Code",
                default_value="",
                tooltip="Editorial timecode (HH:MM:SS:FF), empty if absent",
                allowed_modes={ParameterMode.OUTPUT},
            )
            self._software_param = ParameterString(
                name="software",
                default_value="",
                tooltip="Authoring application, empty if absent",
                allowed_modes={ParameterMode.OUTPUT},
            )
            self._owner_param = ParameterString(
                name="owner",
                default_value="",
                tooltip="Asset owner, empty if absent",
                allowed_modes={ParameterMode.OUTPUT},
            )
            self._chromaticities_param = ParameterString(
                name="chromaticities",
                default_value="",
                tooltip="Colour primaries as JSON {red_x, red_y, ...}, empty if absent",
                allowed_modes={ParameterMode.OUTPUT},
            )
            self._custom_attributes_param = ParameterString(
                name="custom_attributes",
                display_name="Custom Attributes",
                default_value="{}",
                tooltip="Non-standard header attributes as JSON",
                allowed_modes={ParameterMode.OUTPUT},
            )

        self.add_node_element(exr_info_group)

        # --- Structured outputs ---

        self._parts_param = Parameter(
            name="parts",
            display_name="Parts",
            type="list[EXRPartArtifact]",
            output_type="list[EXRPartArtifact]",
            tooltip="Per-part metadata descriptors",
            allowed_modes={ParameterMode.OUTPUT},
            settable=False,
        )
        self.add_parameter(self._parts_param)

        # --- Dynamic groups ---

        self._parts_group = ParameterGroup(name="exr_parts")
        self._parts_group.ui_options = {"display_name": "Parts"}
        self.add_node_element(self._parts_group)

        self._channels_group = ParameterGroup(name="exr_channels")
        self._channels_group.ui_options = {"display_name": "Channels", "collapsed": True}
        self.add_node_element(self._channels_group)

        self._create_status_parameters(
            result_details_tooltip="Details about the EXR header read result",
            result_details_placeholder="Header details will appear here.",
            parameter_group_initially_collapsed=True,
        )

    # --- Lifecycle ---

    def after_value_set(self, parameter: Parameter, value: Any) -> None:
        if parameter is self._file_path_param:
            file_path = str(self.get_parameter_value(self._file_path_param.name) or "")
            self._on_inputs_changed(file_path)

    async def aprocess(self) -> None:
        self._clear_execution_status()

        file_path = self.get_parameter_value(self._file_path_param.name)
        if not file_path:
            self._set_status_results(was_successful=False, result_details="No file path provided")
            return

        if not self._cached_exr_data:
            self._set_status_results(was_successful=False, result_details="No EXR data loaded - check file path")
            return

        exr_data = self._cached_exr_data
        file_path = str(file_path)

        self._populate_scalar_outputs(exr_data)
        self._populate_structured_outputs(file_path, exr_data)

        part = exr_data.parts[0]
        total_channels = sum(len(p.channels) for p in exr_data.parts)
        details = (
            f"Loaded {part.width}×{part.height}, "
            f"{len(exr_data.parts)} part(s), "
            f"{total_channels} channel(s)"
        )
        self._set_status_results(was_successful=True, result_details=details)

    # --- Private: scan and populate ---

    def _clear_static_outputs(self) -> None:
        """Reset all non-dynamic output parameters to empty/zero defaults."""
        self.parameter_output_values[self._image_width_param.name] = 0
        self.parameter_output_values[self._image_height_param.name] = 0
        self.parameter_output_values[self._part_count_param.name] = 0
        self.parameter_output_values[self._channel_count_param.name] = 0
        self.parameter_output_values[self._compression_param.name] = ""
        self.parameter_output_values[self._storage_type_param.name] = ""
        self.parameter_output_values[self._pixel_aspect_ratio_param.name] = 1.0
        self.parameter_output_values[self._data_window_param.name] = ""
        self.parameter_output_values[self._display_window_param.name] = ""
        self.parameter_output_values[self._time_code_param.name] = ""
        self.parameter_output_values[self._software_param.name] = ""
        self.parameter_output_values[self._owner_param.name] = ""
        self.parameter_output_values[self._chromaticities_param.name] = ""
        self.parameter_output_values[self._custom_attributes_param.name] = "{}"
        self.parameter_output_values[self._parts_param.name] = []

    def _on_inputs_changed(self, file_path: str) -> None:
        """Scan EXR header and refresh all outputs. Called on file change."""
        self._remove_dynamic_elements()
        self._cached_exr_data = None
        self._clear_static_outputs()

        if not file_path:
            return

        try:
            exr_data = scan_exr_header(pathlib.Path(file_path))
        except (ValueError, RuntimeError) as e:
            logger.error("LoadEXR '%s': Failed to scan '%s': %s", self.name, file_path, e)
            return

        self._cached_exr_data = exr_data

        self._populate_scalar_outputs(exr_data)
        self._populate_structured_outputs(file_path, exr_data)
        self._populate_parts_group(file_path, exr_data)
        self._populate_channels_group(file_path, exr_data)

    def _populate_scalar_outputs(self, exr_data: EXRData) -> None:
        """Fill the EXR Info group outputs from the first part's header."""
        part = exr_data.parts[0]
        header = part.header

        self.parameter_output_values[self._image_width_param.name] = part.width
        self.parameter_output_values[self._image_height_param.name] = part.height
        self.parameter_output_values[self._part_count_param.name] = len(exr_data.parts)
        self.parameter_output_values[self._channel_count_param.name] = sum(len(p.channels) for p in exr_data.parts)
        self.parameter_output_values[self._compression_param.name] = header.compression.value
        self.parameter_output_values[self._storage_type_param.name] = header.storage_type.value
        self.parameter_output_values[self._pixel_aspect_ratio_param.name] = header.pixel_aspect_ratio

        dw = header.data_window
        self.parameter_output_values[self._data_window_param.name] = f"{dw.xmin},{dw.ymin} - {dw.xmax},{dw.ymax}"
        disp = header.display_window
        self.parameter_output_values[self._display_window_param.name] = (
            f"{disp.xmin},{disp.ymin} - {disp.xmax},{disp.ymax}"
        )

        self.parameter_output_values[self._time_code_param.name] = header.time_code or ""
        self.parameter_output_values[self._software_param.name] = header.software or ""
        self.parameter_output_values[self._owner_param.name] = header.owner or ""

        if header.chromaticities:
            c = header.chromaticities
            chroma_dict = {
                "red_x": c.red_x,
                "red_y": c.red_y,
                "green_x": c.green_x,
                "green_y": c.green_y,
                "blue_x": c.blue_x,
                "blue_y": c.blue_y,
                "white_x": c.white_x,
                "white_y": c.white_y,
            }
            self.parameter_output_values[self._chromaticities_param.name] = json.dumps(chroma_dict)
        else:
            self.parameter_output_values[self._chromaticities_param.name] = ""

        self.parameter_output_values[self._custom_attributes_param.name] = (
            json.dumps(header.custom, indent=2, default=str) if header.custom else "{}"
        )

    def _populate_structured_outputs(self, file_path: str, exr_data: EXRData) -> None:
        """Build and set the structured artifact outputs."""
        part_artifacts = [self._build_part_artifact(file_path, i, p) for i, p in enumerate(exr_data.parts)]
        self.parameter_output_values[self._parts_param.name] = part_artifacts

    def _populate_parts_group(self, file_path: str, exr_data: EXRData) -> None:
        """One output per part; hidden if single-part."""
        if len(exr_data.parts) <= 1:
            return

        for i, part in enumerate(exr_data.parts):
            artifact = self._build_part_artifact(file_path, i, part)
            display = self._part_display_name(part, is_multi=True)
            param = Parameter(
                name=f"{_PART_PREFIX}{part.name}",
                display_name=display,
                type="EXRPartArtifact",
                output_type="EXRPartArtifact",
                tooltip=f"Descriptor for part {part.name} ({part.width}×{part.height})",
                allowed_modes={ParameterMode.OUTPUT},
                settable=False,
            )
            self._parts_group.add_child(param)
            self.parameter_output_values[param.name] = artifact

    def _populate_channels_group(self, file_path: str, exr_data: EXRData) -> None:
        """One output per channel as an EXRChannelArtifact."""
        is_multi = len(exr_data.parts) > 1

        for part_index, part in enumerate(exr_data.parts):
            prefix = f"p{part.name}_" if is_multi else ""
            for ch in part.channels:
                key = f"{prefix}{ch.name}"
                sampling = "" if (ch.x_sampling == 1 and ch.y_sampling == 1) else f" [{ch.x_sampling}×{ch.y_sampling}]"
                display = f"{ch.name} ({ch.pixel_type.value}{sampling})"
                param = Parameter(
                    name=f"{_CHANNEL_PREFIX}{key}",
                    display_name=display,
                    type="EXRChannelArtifact",
                    output_type="EXRChannelArtifact",
                    tooltip=f"Channel '{ch.name}', type={ch.pixel_type.value}, x_sampling={ch.x_sampling}, y_sampling={ch.y_sampling}",
                    allowed_modes={ParameterMode.OUTPUT},
                    settable=False,
                )
                self._channels_group.add_child(param)
                self.parameter_output_values[param.name] = EXRChannelArtifact(
                    file_path=file_path,
                    part_index=part_index,
                    channel=ch,
                )

    # --- Private: helpers ---

    def _build_part_artifact(self, file_path: str, part_index: int, part: EXRPart) -> EXRPartArtifact:
        return EXRPartArtifact(
            file_path=file_path,
            part_index=part_index,
            name=part.header.name,
            width=part.width,
            height=part.height,
            header=part.header,
            channels=part.channels,
        )

    def _part_display_name(self, part: EXRPart, *, is_multi: bool) -> str:
        label = f"Part {part.name}" if is_multi else "Single Part"
        if part.header.name:
            return f"{label}: {part.header.name}"
        return label

    def _remove_dynamic_elements(self) -> None:
        """Clear all dynamic children from the two dynamic groups."""
        for group in (self._parts_group, self._channels_group):
            for child in list(group.children):
                group.remove_child(child)
