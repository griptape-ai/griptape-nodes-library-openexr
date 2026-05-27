"""LoadEXR node - parse EXR header metadata without loading pixels.

Architecture: Two-phase scanning.
- Phase 1 (after_value_set on file_path): Scan headers only.
  Populates all metadata outputs and dynamic parameter groups.
- Phase 2 (aprocess): Validate state, set success/failure status.

Pixel loading is off by default (header_only=True). This is user-configurable
via the `openexr.header_only` engine setting — set to False to read accurate
pixel types at the cost of loading pixel data into memory.
"""

from __future__ import annotations

import json
import logging
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
from griptape_nodes.retained_mode.griptape_nodes import GriptapeNodes
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

_CHROMA_RED_X = "red_x"
_CHROMA_RED_Y = "red_y"
_CHROMA_GREEN_X = "green_x"
_CHROMA_GREEN_Y = "green_y"
_CHROMA_BLUE_X = "blue_x"
_CHROMA_BLUE_Y = "blue_y"
_CHROMA_WHITE_X = "white_x"
_CHROMA_WHITE_Y = "white_y"


def _sanitize_key(name: str) -> str:
    """Convert an arbitrary string into a valid parameter name segment."""
    return "".join(c if c.isalnum() or c == "_" else "_" for c in name)


class LoadEXR(SuccessFailureNode):
    """Parse an OpenEXR file's header and expose metadata for downstream nodes.

    No pixel data is loaded. Outputs structured artifacts plus scalar
    convenience outputs for common fields (width, height, compression, etc.)
    and dynamic groups that adapt to single-part vs multi-part files.

    Single-part: part metadata is shown in the EXR Info group; channels appear
    directly in the Channels group.

    Multi-part: one collapsible panel per part, each containing that part's
    EXRPartArtifact output and its individual channel outputs.
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
        # Single-part: all metadata lives here.
        # Multi-part: file-level scalars (part count, channel count) live here;
        #   per-part detail moves into the per-part panels below.

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

        # Multi-part: populated with one collapsible sub-group per part.
        # Single-part: left empty (nothing to show at part level).
        self._parts_group = ParameterGroup(name="exr_parts")
        self._parts_group.ui_options = {"display_name": "Parts"}
        self.add_node_element(self._parts_group)

        # Single-part only: flat list of channel outputs.
        # Multi-part: channels live inside each part's sub-group instead.
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
        details = f"Loaded {part.width}×{part.height}, {len(exr_data.parts)} part(s), {total_channels} channel(s)"
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

        header_only: bool = GriptapeNodes.ConfigManager().get_config_value("openexr.header_only")
        if header_only is None:
            header_only = True

        try:
            exr_data = scan_exr_header(file_path, header_only=header_only)
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
                _CHROMA_RED_X: c.red_x,
                _CHROMA_RED_Y: c.red_y,
                _CHROMA_GREEN_X: c.green_x,
                _CHROMA_GREEN_Y: c.green_y,
                _CHROMA_BLUE_X: c.blue_x,
                _CHROMA_BLUE_Y: c.blue_y,
                _CHROMA_WHITE_X: c.white_x,
                _CHROMA_WHITE_Y: c.white_y,
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
        """Multi-part: one collapsible sub-group per part with artifact + channels.

        Single-part files are skipped; their channels go into _channels_group instead.
        """
        if len(exr_data.parts) <= 1:
            return

        for i, part in enumerate(exr_data.parts):
            artifact = self._build_part_artifact(file_path, i, part)
            part_key = _sanitize_key(part.header.name or str(i))
            part_label = part.header.name or f"Part {i}"

            part_subgroup = ParameterGroup(name=f"{_PART_PREFIX}{part_key}")
            part_subgroup.ui_options = {"display_name": part_label, "collapsed": True}

            # Artifact output — the primary connectable output for this part
            artifact_param = Parameter(
                name=f"{_PART_PREFIX}{part_key}",
                display_name=part_label,
                type="EXRPartArtifact",
                output_type="EXRPartArtifact",
                tooltip=f"Descriptor for part '{part_label}' ({part.width}×{part.height})",
                allowed_modes={ParameterMode.OUTPUT},
                settable=False,
            )
            part_subgroup.add_child(artifact_param)
            self.parameter_output_values[artifact_param.name] = artifact

            # Channel outputs for this part
            for ch in part.channels:
                ch_key = _sanitize_key(ch.name)
                sampling = "" if (ch.x_sampling == 1 and ch.y_sampling == 1) else f" [{ch.x_sampling}×{ch.y_sampling}]"
                ch_param = Parameter(
                    name=f"{_CHANNEL_PREFIX}{part_key}_{ch_key}",
                    display_name=f"{ch.name} ({ch.pixel_type.value}{sampling})",
                    type="EXRChannelArtifact",
                    output_type="EXRChannelArtifact",
                    tooltip=f"Channel '{ch.name}', type={ch.pixel_type.value}, x_sampling={ch.x_sampling}, y_sampling={ch.y_sampling}",
                    allowed_modes={ParameterMode.OUTPUT},
                    settable=False,
                )
                part_subgroup.add_child(ch_param)
                self.parameter_output_values[ch_param.name] = EXRChannelArtifact(
                    file_path=file_path,
                    part_index=i,
                    channel=ch,
                )

            self._parts_group.add_child(part_subgroup)

    def _populate_channels_group(self, file_path: str, exr_data: EXRData) -> None:
        """Single-part only: flat channel outputs.

        Multi-part channels live inside each part's sub-group in _parts_group.
        """
        if len(exr_data.parts) > 1:
            return

        part = exr_data.parts[0]
        for ch in part.channels:
            ch_key = _sanitize_key(ch.name)
            sampling = "" if (ch.x_sampling == 1 and ch.y_sampling == 1) else f" [{ch.x_sampling}×{ch.y_sampling}]"
            param = Parameter(
                name=f"{_CHANNEL_PREFIX}{ch_key}",
                display_name=f"{ch.name} ({ch.pixel_type.value}{sampling})",
                type="EXRChannelArtifact",
                output_type="EXRChannelArtifact",
                tooltip=f"Channel '{ch.name}', type={ch.pixel_type.value}, x_sampling={ch.x_sampling}, y_sampling={ch.y_sampling}",
                allowed_modes={ParameterMode.OUTPUT},
                settable=False,
            )
            self._channels_group.add_child(param)
            self.parameter_output_values[param.name] = EXRChannelArtifact(
                file_path=file_path,
                part_index=0,
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

    def _remove_dynamic_elements(self) -> None:
        """Clear all dynamic children from both dynamic groups."""
        for group in (self._parts_group, self._channels_group):
            for child in list(group.children):
                group.remove_child(child)
