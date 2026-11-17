"""ReadEXR node - load an EXR file or frame sequence and render one frame."""

from __future__ import annotations

import asyncio
import logging
import os
from concurrent.futures import Future
from contextvars import ContextVar
from copy import deepcopy
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, NamedTuple

from griptape.artifacts import ImageUrlArtifact
from griptape_nodes.common.macro_parser import MacroSyntaxError, ParsedMacro
from griptape_nodes.common.sequences import Sequence, SequenceEntry
from griptape_nodes.exe_types.core_types import NodeMessageResult, Parameter, ParameterGroup, ParameterMode
from griptape_nodes.exe_types.node_types import SuccessFailureNode
from griptape_nodes.exe_types.param_components.project_file_parameter import ProjectFileParameter
from griptape_nodes.exe_types.param_types.parameter_button import ParameterButton
from griptape_nodes.exe_types.param_types.parameter_float import ParameterFloat
from griptape_nodes.exe_types.param_types.parameter_int import ParameterInt
from griptape_nodes.exe_types.param_types.parameter_string import ParameterString
from griptape_nodes.retained_mode.events.artifact_events import (
    GetPreviewForArtifactRequest,
    PreviewGenerationPolicy,
)
from griptape_nodes.retained_mode.events.project_events import MacroPath
from griptape_nodes.retained_mode.griptape_nodes import GriptapeNodes
from griptape_nodes.traits.options import Options
from griptape_nodes.utils.async_utils import to_thread

from griptape_nodes_openexr.exr.channel_selection import (
    channels_in_layer,
    default_layer,
    list_layers,
    order_channels_for_display,
    select_alpha_channel,
    select_display_channels,
)
from griptape_nodes_openexr.exr.display_window import (
    WindowFraming,
    add_window_mode_parameter,
    validate_window_mode,
    window_size,
)
from griptape_nodes_openexr.exr.exr_header_artifact import EXRChannelArtifact, EXRPartArtifact
from griptape_nodes_openexr.exr.header_metadata import (
    HEADER_METADATA_DEFAULTS,
    HEADER_METADATA_DEFINITIONS,
    header_metadata_outputs,
)
from griptape_nodes_openexr.exr.metadata_reader import read_metadata
from griptape_nodes_openexr.exr.metadata_service import (
    MetadataServiceClosed,
    MetadataSuperseded,
    get_metadata_service,
)
from griptape_nodes_openexr.exr.ocio_helpers import (
    COLOR_MODE_BASIC,
    COLOR_MODE_OCIO,
    ColorParamsProtocol,
    add_color_mode_parameters,
    handle_color_mode_change,
)
from griptape_nodes_openexr.exr.rendering import render_part_png
from griptape_nodes_openexr.exr.sequence_helpers import resolve_sequence_path, sequence_path_parameter
from griptape_nodes_openexr.exr.tone_mapping import EV_MAX, EV_MIN, TONE_DEFAULT
from griptape_nodes_openexr.exr.viewer_launcher import handle_viewer_button_click

if TYPE_CHECKING:
    from griptape_nodes.traits.button import Button, ButtonDetailsMessagePayload

    from griptape_nodes_openexr.exr.exr_types import EXRData, EXRPart

logger = logging.getLogger("griptape_nodes")
_execution_generation: ContextVar[int | None] = ContextVar("read_exr_execution_generation", default=None)

# The provider's friendly name, matched case-insensitively by the artifact manager.
_EXR_PROVIDER_NAME = "EXR"

# Slider bounds before a scan has told us the real sequence range. soft_limits=True
# means these only size the track, so a connection driving a frame outside them is
# still accepted.
_DEFAULT_FRAME_MIN = 0
_DEFAULT_FRAME_MAX = 100


class RenderKey(NamedTuple):
    """Everything that changes the rendered pixels.

    Named rather than a bare tuple so that adding a cache-affecting input is a type
    error at every construction site instead of a silently stale render.
    """

    scan_key: str
    source_stamp: tuple[int, int]
    frame_number: int
    part_index: int
    layer: str
    window_mode: str
    exposure_ev: float
    color_mode: str
    tone_mapping: str
    embed_color_profile: bool
    color_params: tuple | None


@dataclass(frozen=True)
class ScanState:
    """A completed header scan: the file it describes, and what it found.

    Frozen so a worker thread reads the whole scan as one snapshot rather than
    risking a tear across fields while the loop installs a newer one.
    """

    key: str
    sequence: Sequence
    exr_data: EXRData
    source_stamp: tuple[int, int] | None
    entry: SequenceEntry
    frame_note: str
    part_index: int


@dataclass(frozen=True)
class _FramePlan:
    """Everything resolved from the parameters before a frame can be rendered."""

    entry: SequenceEntry
    frame_note: str
    is_sequence: bool
    # The dropdown label ("1: diffuse"), which is what failure messages quote back.
    part_name: str
    part_index: int
    part: EXRPart
    exr_data: EXRData
    layer: str
    window_mode: str
    exposure_ev: float
    color_mode: str
    tone_mapping: str
    embed_color_profile: bool
    color_params: ColorParamsProtocol | None

    @property
    def framing(self) -> WindowFraming:
        # From the frame being read, since each frame of a sequence carries its own windows.
        return WindowFraming.for_header(self.window_mode, self.part.header)


@dataclass(frozen=True)
class RenderState:
    """A completed render: its cache key, output values, and source file.

    The outputs are held alongside the key because the engine clears
    parameter_output_values whenever it marks the node unresolved, so a cache hit
    has to re-emit rather than assume the previous run's values survived.
    """

    key: RenderKey
    outputs: dict[str, Any]
    source_path: str


class ReadEXR(SuccessFailureNode):
    """Read an EXR file or frame sequence and emit a viewable image in one node.

    Takes a path or a Sequence, scans the header, decodes the selected part and
    layer, and emits a tone-mapped 8-bit PNG alongside the EXRPartArtifact and
    EXRChannelArtifact descriptors that carry the float data onward. Modelled on
    Nuke's Read node.

    Scalar outputs only: one frame per execution, with iteration left to the
    graph (wire `For Loop Start.index` into `frame`).

    The `openexr.header_only` setting is ignored here, since the node has to
    decode pixels for the selected part regardless of what the setting says.
    """

    def __init__(self, name: str, metadata: dict[Any, Any] | None = None) -> None:
        super().__init__(name, metadata)

        # Keyed on the resolved path, so a frame scrub reuses the scan.
        self._scan: ScanState | None = None
        self._render: RenderState | None = None
        # The after_value_set scan runs as a background task so the setter can return.
        self._metadata_owner = object()
        self._control_selection: tuple[str, int] | None = None
        self._deleted = False
        self._metadata_generation = 0
        self._applied_generation = -1
        self._request: Future[ScanState] | None = None
        self._request_loop: asyncio.AbstractEventLoop | None = None
        self._service = get_metadata_service()
        self._adjusting_selection = False
        self._request_preview = False

        self._path_param = sequence_path_parameter("path")
        self.add_parameter(self._path_param)

        self._part_name_param = ParameterString(
            name="part_name",
            default_value="",
            tooltip="Which part of the file to read. Hidden for single-part files.",
            allowed_modes={ParameterMode.INPUT, ParameterMode.PROPERTY},
            ui_options={"hide": True},
        )
        # allow_custom disables the Options converter and validator. Without it, a value
        # restored on workflow load (hooks suppressed, so no scan has run and choices are
        # still empty) hits `self.choices[0]` on an empty list: the saved part is dropped
        # and the node silently reads part 0 instead. _apply_scan_to_ui corrects a value
        # the file does not have, once the scan can say what the real choices are.
        self._part_name_param.add_trait(Options(choices=[], allow_custom=True))
        self.add_parameter(self._part_name_param)

        self._layer_param = ParameterString(
            name="layer",
            default_value="",
            tooltip="Which layer of the selected part to render. Hidden when the part has one layer.",
            allowed_modes={ParameterMode.INPUT, ParameterMode.PROPERTY},
            ui_options={"hide": True},
        )
        self._layer_param.add_trait(Options(choices=[], allow_custom=True))
        self.add_parameter(self._layer_param)

        self._frame_param = ParameterInt(
            name="frame",
            default_value=0,
            min_val=_DEFAULT_FRAME_MIN,
            max_val=_DEFAULT_FRAME_MAX,
            slider=True,
            # Mandatory, not cosmetic: the real range is unknown before the scan, and
            # without soft limits a connection pushing a frame outside the track raises
            # and aborts the run.
            soft_limits=True,
            tooltip=(
                "Absolute frame number to read, matching the filenames on disk. "
                "Connect For Loop Start.index to iterate. Hidden for a single file."
            ),
            allowed_modes={ParameterMode.INPUT, ParameterMode.PROPERTY},
            ui_options={"hide": True},
        )
        self.add_parameter(self._frame_param)

        self._window_mode_param = add_window_mode_parameter(self)

        # --- Color management ---
        color_group = ParameterGroup(name="Color Management", ui_options={"collapsed": True})
        with color_group:
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
            (
                self._color_mode_param,
                self._tone_mapping_param,
                self._color_params_param,
                self._embed_color_profile_param,
            ) = add_color_mode_parameters(
                self,
                color_mode_tooltip="Color management mode. 'basic' uses local tone mapping; 'ocio' uses the connected OCIOColorParamsArtifact.",
                color_params_tooltip="OCIO color parameters artifact (source colorspace, display, view). Required when color_mode is 'ocio'.",
            )
        self.add_node_element(color_group)
        # add_color_mode_parameters registers its parameters on the node directly, which
        # re-parents them out of the group context. Pull them back in so the whole colour
        # section collapses together.
        for parameter in (
            self._color_mode_param,
            self._tone_mapping_param,
            self._color_params_param,
            self._embed_color_profile_param,
        ):
            color_group.add_child(parameter)

        # --- Outputs ---
        self._image_param = Parameter(
            name="image",
            type="ImageUrlArtifact",
            output_type="ImageUrlArtifact",
            tooltip=(
                "8-bit PNG of the current frame, encoded with the selected display transform, "
                "for in-canvas display and downstream image nodes"
            ),
            allowed_modes={ParameterMode.OUTPUT},
            ui_options={"expander": True, "pulse_on_run": True},
        )
        self.add_parameter(self._image_param)

        self._part_param = Parameter(
            name="part",
            type="EXRPartArtifact",
            output_type="EXRPartArtifact",
            tooltip="Descriptor for the part that was read, carrying the float pixel data onward",
            allowed_modes={ParameterMode.OUTPUT},
            settable=False,
        )
        self.add_parameter(self._part_param)

        self._channels_param = Parameter(
            name="channels",
            type="list[EXRChannelArtifact]",
            output_type="list[EXRChannelArtifact]",
            tooltip="One descriptor per channel in the part that was read",
            allowed_modes={ParameterMode.OUTPUT},
            settable=False,
        )
        self.add_parameter(self._channels_param)

        self._output_file = ProjectFileParameter(
            node=self,
            name="output_file",
            default_filename="read_exr.png",
        )
        self._output_file.add_parameter()

        info_group = ParameterGroup(name="EXR Info", ui_options={"collapsed": True})
        with info_group:
            self._image_width_param = ParameterInt(
                name="image_width",
                display_name="Image Width",
                default_value=0,
                tooltip=(
                    "Width of the rendered image in pixels: the display window in 'display window' mode, "
                    "otherwise the data window"
                ),
                allowed_modes={ParameterMode.OUTPUT},
            )
            self._image_height_param = ParameterInt(
                name="image_height",
                display_name="Image Height",
                default_value=0,
                tooltip=(
                    "Height of the rendered image in pixels: the display window in 'display window' mode, "
                    "otherwise the data window"
                ),
                allowed_modes={ParameterMode.OUTPUT},
            )
            self._part_count_param = ParameterInt(
                name="part_count",
                display_name="Part Count",
                default_value=0,
                tooltip="Number of parts in the file",
                allowed_modes={ParameterMode.OUTPUT},
            )
            self._channel_count_param = ParameterInt(
                name="channel_count",
                display_name="Channel Count",
                default_value=0,
                tooltip="Number of channels in the part that was read",
                allowed_modes={ParameterMode.OUTPUT},
            )
            self._first_frame_param = ParameterInt(
                name="first_frame",
                display_name="First Frame",
                default_value=0,
                tooltip="Lowest frame number in the sequence; 0 for a single file",
                allowed_modes={ParameterMode.OUTPUT},
            )
            self._last_frame_param = ParameterInt(
                name="last_frame",
                display_name="Last Frame",
                default_value=0,
                tooltip="Highest frame number in the sequence; 0 for a single file",
                allowed_modes={ParameterMode.OUTPUT},
            )
            self._frame_count_param = ParameterInt(
                name="frame_count",
                display_name="Frame Count",
                default_value=0,
                tooltip="Number of frames in the sequence; 1 for a single file",
                allowed_modes={ParameterMode.OUTPUT},
            )
        with info_group:
            for output_name, definition in HEADER_METADATA_DEFINITIONS.items():
                parameter = (
                    ParameterFloat(
                        name=output_name, default_value=float(definition.default), allowed_modes={ParameterMode.OUTPUT}
                    )
                    if definition.type == "float"
                    else ParameterString(
                        name=output_name, default_value=str(definition.default), allowed_modes={ParameterMode.OUTPUT}
                    )
                )
                parameter.display_name = definition.display_name
                parameter.tooltip = definition.tooltip + " Describes the selected part of the selected frame."
        self.add_node_element(info_group)
        # Cleared together on failure; see _on_fail.
        self._info_params = (
            self._image_width_param,
            self._image_height_param,
            self._part_count_param,
            self._channel_count_param,
            self._first_frame_param,
            self._last_frame_param,
            self._frame_count_param,
        )

        self._open_viewer_param = ParameterButton(
            name="open_in_viewer",
            label="Open in external viewer",
            tooltip="Open the source EXR file in the configured external viewer (or OS default)",
            on_click=self._on_open_viewer_click,
        )
        self.add_parameter(self._open_viewer_param)

        self._create_status_parameters(
            result_details_tooltip="Details about the EXR read result",
            result_details_placeholder="Read details will appear here.",
            parameter_group_initially_collapsed=True,
        )

    # ------------------------------------------------------------------
    # Value hooks
    # ------------------------------------------------------------------

    def after_value_set(self, parameter: Parameter, value: Any) -> None:
        if parameter.name == self._color_mode_param.name:
            handle_color_mode_change(
                self,
                value,
                self._tone_mapping_param.name,
                self._color_params_param.name,
                self._embed_color_profile_param.name,
            )
            return

        if self._adjusting_selection:
            return
        if parameter.name in {self._path_param.name, self._frame_param.name, self._part_name_param.name}:
            if parameter.name == self._path_param.name:
                self._render = None
                self._control_selection = None
            self._submit_metadata(preview=parameter.name == self._path_param.name)

    def _submit_metadata(self, *, preview: bool = False) -> Future[ScanState]:
        """Capture selection and enqueue work; disk reading never runs in the setter."""
        raw_path = deepcopy(self.get_parameter_value(self._path_param.name))
        frame = self.get_parameter_value(self._frame_param.name)
        part_choice = str(self.get_parameter_value(self._part_name_param.name) or "")
        self._metadata_generation += 1
        generation = self._metadata_generation
        self._scan = None
        self.parameter_output_values.update(HEADER_METADATA_DEFAULTS)
        self._request_preview = preview
        # The engine loop owns node changes even when a setter is called on another
        # thread. Standalone callers apply results when explicitly awaiting them.
        engine_loop = self.engine.event_manager.event_loop
        loop = engine_loop if engine_loop is not None and engine_loop.is_running() else _running_loop()
        self._request_loop = loop
        selection = (_scan_key_for(raw_path), frame, part_choice)
        reset_part = self._control_selection is None
        future = self._service.submit(
            self._metadata_owner,
            selection,
            lambda: self._read_selection(raw_path, frame, part_choice, reset_part=reset_part),
        )
        self._request = future
        if loop is not None:

            def completed(result: Future[ScanState]) -> None:
                if not loop.is_closed():
                    loop.call_soon_threadsafe(self._complete_metadata, result, generation)

            future.add_done_callback(completed)
        return future

    @staticmethod
    def _read_selection(raw_path: Any, frame: Any, part_choice: str, *, reset_part: bool) -> ScanState:
        """Resolve and read exactly the selected file, retaining no completed cache."""
        if not _scan_key_for(raw_path):
            raise ValueError("No path provided")
        sequence = resolve_sequence_path(raw_path)
        if isinstance(sequence, str):
            raise ValueError(sequence)
        entry, note = _select_sequence_entry(sequence, frame, is_sequence=len(sequence.entries) > 1)
        if entry is None:
            raise ValueError(note)
        stamp = _file_stamp(entry.path)
        if stamp is None:
            raise ValueError(f"Cannot read{note}: '{entry.path}' is missing or unreadable")
        data = read_metadata(entry.path)
        index = _part_index_for(data, part_choice)
        if reset_part and (index is None or (part_choice and part_choice != _part_choices_for(data)[index])):
            index = 0
            part_choice = ""
        if index is None:
            raise ValueError(f"Part '{part_choice}' is not in{note or ' this file'}")
        if part_choice and part_choice != _part_choices_for(data)[index]:
            raise ValueError(f"Part '{part_choice}' is not in{note or ' this file'}")
        return ScanState(_scan_key_for(raw_path), sequence, data, stamp, entry, note, index)

    def _complete_metadata(self, future: Future[ScanState], generation: int) -> None:
        if (
            self._deleted
            or generation != self._metadata_generation
            or self._applied_generation == generation
            or self._service.closed
        ):
            return
        self._applied_generation = generation
        try:
            scan = future.result()
        except (MetadataSuperseded, MetadataServiceClosed):
            return
        except Exception:
            self._scan = None
            self._render = None
            self.parameter_output_values.update(HEADER_METADATA_DEFAULTS)
            return
        self._adopt_scan(scan)
        if self._request_preview:
            # Preview generation has its own engine policy. It never supplies this
            # node's metadata and remains outside the metadata reader service.
            if _running_loop() is not None:
                asyncio.create_task(to_thread(self._request_canvas_preview))
            else:
                self._request_canvas_preview()

    async def wait_for_pending_scan(self) -> None:
        """Await the latest metadata request and apply it in the owning context."""
        while self._request is not None:
            future = self._request
            generation = self._metadata_generation
            try:
                await asyncio.shield(asyncio.wrap_future(future))
            except (MetadataSuperseded, MetadataServiceClosed):
                if future is not self._request:
                    continue
            except Exception:
                pass
            if future is not self._request:
                continue
            loop = self._request_loop
            if loop is None or loop is _running_loop() or not loop.is_running():
                self._complete_metadata(future, generation)
            else:
                applied: Future[None] = Future()

                def apply(
                    result: Future[ScanState] = future,
                    revision: int = generation,
                    completion: Future[None] = applied,
                ) -> None:
                    try:
                        self._complete_metadata(result, revision)
                    except Exception as error:
                        completion.set_exception(error)
                    else:
                        completion.set_result(None)

                loop.call_soon_threadsafe(apply)
                await asyncio.shield(asyncio.wrap_future(applied))
            return

    def _clear_metadata(self) -> None:
        self._metadata_generation += 1
        self._service.close_owner(self._metadata_owner)
        self._request = None
        self.parameter_output_values.update(HEADER_METADATA_DEFAULTS)

    def _adopt_scan(self, scan: ScanState) -> None:
        """Update node controls and outputs from the current request only."""
        self._scan = scan
        control_selection = (scan.key, scan.part_index)
        reset_layer = self._control_selection != control_selection
        self._control_selection = control_selection
        self._adjusting_selection = True
        try:
            self._apply_scan_to_ui(scan.sequence, scan.exr_data, reset_layer=reset_layer)
        finally:
            self._adjusting_selection = False
        self.parameter_output_values.update(header_metadata_outputs(scan.exr_data.parts[scan.part_index].header))

    def _apply_scan_to_ui(self, sequence: Sequence, exr_data: EXRData, *, reset_layer: bool = True) -> None:
        """Populate the dropdowns and the frame slider range from a fresh scan."""
        part_choices = _part_choices_for(exr_data)
        _set_choices(self._part_name_param, part_choices)
        current_part = self.get_parameter_value(self._part_name_param.name)
        if current_part not in part_choices:
            self.set_parameter_value(self._part_name_param.name, part_choices[0], emit_change=False)
        self._set_hidden(self._part_name_param, hidden=len(part_choices) <= 1)

        self._apply_layer_choices(
            exr_data, str(self.get_parameter_value(self._part_name_param.name) or ""), reset_layer=reset_layer
        )

        is_sequence = len(sequence.entries) > 1
        self._set_hidden(self._frame_param, hidden=not is_sequence)
        if is_sequence:
            self._frame_param.min_val = sequence.first
            self._frame_param.max_val = sequence.last
            # Falsy means unset. ParameterInt's converter turns None and "" into 0 and
            # the default is 0, so 0 is the only falsy value that reaches here and an
            # explicit frame 0 cannot be told apart from an untouched slider. See
            # _select_entry for why that costs nothing.
            if not self.get_parameter_value(self._frame_param.name):
                self.set_parameter_value(self._frame_param.name, sequence.first, emit_change=False)
        else:
            # Back to the placeholder range rather than keeping the last sequence's.
            self._frame_param.min_val = _DEFAULT_FRAME_MIN
            self._frame_param.max_val = _DEFAULT_FRAME_MAX
        # Reasserted rather than assumed: the range setters rebuild the Slider trait,
        # and losing soft limits would let a loop-driven frame outside the range abort
        # the run.
        self._frame_param.soft_limits = True

    def _apply_layer_choices(self, exr_data: EXRData, part_choice: str, *, reset_layer: bool = True) -> None:
        """Repopulate the layer dropdown for the currently selected part."""
        channel_names = [channel.name for channel in _part_for(exr_data, part_choice).channels]
        layer_choices = list_layers(channel_names)
        _set_choices(self._layer_param, layer_choices)
        current_layer = self.get_parameter_value(self._layer_param.name)
        if current_layer not in layer_choices and (reset_layer or not current_layer):
            fallback = default_layer(channel_names) or (layer_choices[0] if layer_choices else "")
            self.set_parameter_value(self._layer_param.name, fallback, emit_change=False)
        self._set_hidden(self._layer_param, hidden=len(layer_choices) <= 1)

    def _request_canvas_preview(self) -> None:
        """Ask the engine for a cheap provider preview of the scanned file.

        Skipped when `frame` is connection-driven: a loop is iterating and nobody
        is watching the canvas. A failure degrades to no preview.
        """
        if self._param_has_incoming_connection(self._frame_param.name):
            return
        scan = self._scan
        if scan is None or not scan.sequence.entries:
            return
        sequence = scan.sequence

        try:
            macro_path = MacroPath(ParsedMacro(sequence.entries[0].path), {})
        except MacroSyntaxError:
            logger.debug("ReadEXR '%s': path is not a usable macro, skipping preview", self.name)
            return

        result = GriptapeNodes.handle_request(
            GetPreviewForArtifactRequest(
                macro_path=macro_path,
                artifact_provider_name=_EXR_PROVIDER_NAME,
                preview_generation_policy=PreviewGenerationPolicy.ONLY_IF_STALE,
            )
        )
        if result.failed():
            logger.debug("ReadEXR '%s': no canvas preview available - %s", self.name, result.result_details)

    # ------------------------------------------------------------------
    # Introspection used by the dropdowns (and by tests)
    # ------------------------------------------------------------------

    def _part_choices(self) -> list[str]:
        if self._scan is None:
            return []
        return _part_choices_for(self._scan.exr_data)

    def _layer_choices(self) -> list[str]:
        if self._scan is None:
            return []
        part = _part_for(self._scan.exr_data, self.get_parameter_value(self._part_name_param.name))
        return list_layers([channel.name for channel in part.channels])

    def _set_hidden(self, parameter: Parameter, *, hidden: bool) -> None:
        if hidden:
            self.hide_parameter_by_name(parameter.name)
        else:
            self.show_parameter_by_name(parameter.name)

    # ------------------------------------------------------------------
    # Buttons and failure
    # ------------------------------------------------------------------

    def _on_open_viewer_click(
        self,
        button: Button,  # noqa: ARG002
        button_details: ButtonDetailsMessagePayload,  # noqa: ARG002
    ) -> NodeMessageResult | None:
        render = self._render
        return handle_viewer_button_click(self.name, render.source_path if render is not None else None)

    def after_node_deleted(self) -> None:
        self._deleted = True
        self._clear_metadata()
        super().after_node_deleted()

    def _on_fail(self, details: str) -> None:
        executing_generation = _execution_generation.get()
        if executing_generation is not None and executing_generation != self._metadata_generation:
            return
        self._clear_metadata()
        self._render = None
        self.parameter_output_values[self._image_param.name] = None
        self.parameter_output_values[self._part_param.name] = None
        self.parameter_output_values[self._channels_param.name] = None
        # The info outputs go too. Leaving them behind publishes a mixed state where the
        # dimensions describe the last good read and the bounds describe the failed one,
        # which is worse than publishing nothing.
        for parameter in self._info_params:
            self.parameter_output_values[parameter.name] = 0
        self._set_status_results(was_successful=False, result_details=details)

    # ------------------------------------------------------------------
    # Execution
    # ------------------------------------------------------------------

    async def aprocess(self) -> None:
        token = _execution_generation.set(None)
        try:
            await self._aprocess_current()
        finally:
            _execution_generation.reset(token)

    async def _aprocess_current(self) -> None:
        """Resolve the parameters to one frame, render it, and publish the outputs.

        Each step below returns `None` when it has already called `_on_fail`, so the
        spine stays a flat sequence of guards.
        """
        self._clear_execution_status()
        scan = await self._aresolve_scan()
        if scan is None:
            return
        generation = self._metadata_generation
        _execution_generation.set(generation)

        is_sequence = self._publish_sequence_info(scan)

        plan = self._plan_frame(scan, is_sequence=is_sequence)
        if plan is None:
            return

        render_key = self._render_key_for(scan, plan)
        if render_key is None:
            return

        cached = self._render
        cached_part = cached.outputs[self._part_param.name] if cached is not None else None
        if (
            cached is not None
            and render_key == cached.key
            and cached_part is not None
            and (
                cached_part.header.data_window == plan.part.header.data_window
                and cached_part.header.display_window == plan.part.header.display_window
                and cached_part.channels == plan.part.channels
            )
        ):
            outputs = self._build_outputs(plan, cached.outputs[self._image_param.name])
            self._emit_render(outputs)
            details = f"Reused cached render of {_part_label(plan.part, plan.part_index)}{plan.frame_note}"
            self._set_status_results(was_successful=True, result_details=details)
            return

        channels = self._select_channels(plan)
        if channels is None:
            return
        selected, alpha_channel = channels

        rendered = await self._arender_png(plan, selected, alpha_channel)
        if rendered is None:
            return
        if self._deleted or self._service.closed or generation != self._metadata_generation:
            return
        png_bytes, tone_mode = rendered

        artifact = self._write_png(png_bytes, plan)
        if artifact is None:
            return

        outputs = self._build_outputs(plan, artifact)
        self._emit_render(outputs)
        self._render = RenderState(key=render_key, outputs=outputs, source_path=plan.entry.path)

        alpha_info = f", alpha: {alpha_channel}" if alpha_channel else ""
        details = (
            f"Read {_part_label(plan.part, plan.part_index)}{plan.frame_note} - "
            f"{outputs[self._image_width_param.name]}x{outputs[self._image_height_param.name]} ({plan.window_mode}), "
            f"channels: {selected}{alpha_info}, EV={plan.exposure_ev:+.1f}, {tone_mode}"
        )
        self._set_status_results(was_successful=True, result_details=details)
        logger.info("ReadEXR '%s': %s", self.name, details)

    async def _aresolve_scan(self) -> ScanState | None:
        """Join an active selection read or request fresh headers for execution."""
        request = self._request
        if request is None or request.done():
            request = self._submit_metadata()
        generation = self._metadata_generation
        await self.wait_for_pending_scan()
        if self._deleted or self._service.closed or generation != self._metadata_generation:
            return None
        try:
            scan = request.result()
        except (MetadataSuperseded, MetadataServiceClosed):
            return None
        except Exception as error:
            self._on_fail(str(error))
            return None
        return scan

    def _publish_sequence_info(self, scan: ScanState) -> bool:
        """Publish the sequence bounds and return whether this is a sequence at all."""
        sequence = scan.sequence
        is_sequence = len(sequence.entries) > 1
        self.parameter_output_values[self._first_frame_param.name] = sequence.first if is_sequence else 0
        self.parameter_output_values[self._last_frame_param.name] = sequence.last if is_sequence else 0
        self.parameter_output_values[self._frame_count_param.name] = len(sequence.entries)
        return is_sequence

    def _plan_frame(self, scan: ScanState, *, is_sequence: bool) -> _FramePlan | None:
        """Resolve frame, part and colour settings. Returns None after calling `_on_fail`."""
        try:
            window_mode = validate_window_mode(str(self.get_parameter_value(self._window_mode_param.name)))
        except ValueError as e:
            self._on_fail(str(e))
            return None
        exr_data = scan.exr_data

        entry, frame_note = scan.entry, scan.frame_note

        part_name = str(self.get_parameter_value(self._part_name_param.name) or "")
        part_index = scan.part_index

        color_mode = str(self.get_parameter_value(self._color_mode_param.name) or COLOR_MODE_BASIC)
        color_params: ColorParamsProtocol | None = None
        if color_mode == COLOR_MODE_OCIO:
            color_params = self.get_parameter_value(self._color_params_param.name)  # type: ignore[assignment]
            if color_params is None:
                self._on_fail("OCIO mode requires a connected color_params artifact.")
                return None

        return _FramePlan(
            entry=entry,
            frame_note=frame_note,
            is_sequence=is_sequence,
            part_name=part_name,
            part_index=part_index,
            part=exr_data.parts[part_index],
            exr_data=exr_data,
            layer=str(self.get_parameter_value(self._layer_param.name) or ""),
            window_mode=window_mode,
            exposure_ev=float(self.get_parameter_value(self._exposure_param.name) or 0.0),
            color_mode=color_mode,
            tone_mapping=str(self.get_parameter_value(self._tone_mapping_param.name) or TONE_DEFAULT),
            embed_color_profile=bool(self.get_parameter_value(self._embed_color_profile_param.name)),
            color_params=color_params,
        )

    def _render_key_for(self, scan: ScanState, plan: _FramePlan) -> RenderKey | None:
        """Build the render cache key. Returns None after calling `_on_fail`."""
        # The frame actually being read, not frame 1: one stat against a multi-second
        # decode. A frame that vanished between the scan and now fails here rather than
        # as an OpenEXR exception from inside the worker thread.
        source_stamp = _file_stamp(plan.entry.path)
        if source_stamp is None:
            self._on_fail(f"Cannot read{plan.frame_note}: '{plan.entry.path}' is missing or unreadable")
            return None

        return RenderKey(
            scan_key=scan.key,
            source_stamp=source_stamp,
            frame_number=plan.entry.number,
            part_index=plan.part_index,
            layer=plan.layer,
            window_mode=plan.window_mode,
            exposure_ev=plan.exposure_ev,
            color_mode=plan.color_mode,
            tone_mapping=plan.tone_mapping,
            embed_color_profile=plan.embed_color_profile,
            # The artifact's values, not its identity: an upstream OCIO node can mutate
            # and re-emit the same object, which leaves id() unchanged and would serve a
            # stale image for the new display.
            color_params=_color_params_key(plan.color_params),
        )

    def _select_channels(self, plan: _FramePlan) -> tuple[list[str], str | None] | None:
        """Pick the channels to display. Returns None after calling `_on_fail`."""
        channel_names = [channel.name for channel in plan.part.channels]
        candidates = channels_in_layer(channel_names, plan.layer) if plan.layer else channel_names
        if not candidates:
            # Preserve the selected layer across frames so a missing layer fails clearly.
            self._on_fail(f"Layer '{plan.layer}' is not in {_part_label(plan.part, plan.part_index)}{plan.frame_note}")
            return None

        selected = select_display_channels(candidates)
        if selected is None:
            self._on_fail(f"No channels found in {_part_label(plan.part, plan.part_index)}")
            return None

        return selected, select_alpha_channel(candidates, selected)

    async def _arender_png(
        self, plan: _FramePlan, selected: list[str], alpha_channel: str | None
    ) -> tuple[bytes, str] | None:
        """Decode and encode the frame off the loop. Returns None after calling `_on_fail`.

        On a 4K frame this is ~3.2s of CPU, which would otherwise freeze the canvas for
        the whole run.
        """
        try:
            return await to_thread(
                self._render_png,
                plan.entry.path,
                plan.part_index,
                selected,
                alpha_channel,
                plan.exposure_ev,
                plan.color_params,
                plan.tone_mapping,
                plan.embed_color_profile,
                plan.framing,
            )
        except (ValueError, RuntimeError) as e:
            self._on_fail(str(e))
            return None

    def _write_png(self, png_bytes: bytes, plan: _FramePlan) -> ImageUrlArtifact | None:
        """Save the PNG into the project. Returns None after calling `_on_fail`."""
        try:
            # The frame number goes into the filename so a loop leaves a clean numbered
            # set; a single file keeps the plain name.
            extra_vars: dict[str, str | int] = {"_index": plan.entry.number} if plan.is_sequence else {}
            destination = self._output_file.build_file(**extra_vars)
            saved = destination.write_bytes(png_bytes)
        except Exception as e:  # noqa: BLE001 - surfaced as a node failure
            self._on_fail(f"Failed to save image: {e}")
            return None
        return ImageUrlArtifact(saved.location)

    def _build_outputs(self, plan: _FramePlan, artifact: ImageUrlArtifact) -> dict[str, Any]:
        """Assemble the node outputs for a completed render."""
        part = plan.part
        width, height = window_size(plan.framing)
        part_artifact = EXRPartArtifact(
            file_path=plan.entry.path,
            part_index=plan.part_index,
            name=part.header.name,
            width=part.width,
            height=part.height,
            header=part.header,
            channels=part.channels,
            frame_number=plan.entry.number if plan.is_sequence else None,
        )
        channel_artifacts = [
            EXRChannelArtifact(file_path=plan.entry.path, part_index=plan.part_index, channel=channel)
            # R, G, B, A rather than the alphabetical order EXR stores, matching LoadEXR.
            for channel in order_channels_for_display(part.channels)
        ]

        return {
            self._image_param.name: artifact,
            self._part_param.name: part_artifact,
            self._channels_param.name: channel_artifacts,
            self._image_width_param.name: width,
            self._image_height_param.name: height,
            self._part_count_param.name: len(plan.exr_data.parts),
            self._channel_count_param.name: len(part.channels),
            **header_metadata_outputs(part.header),
        }

    def _render_png(  # noqa: PLR0913, PLR0917
        self,
        file_path: str,
        part_index: int,
        selected: list[str],
        alpha_channel: str | None,
        ev: float,
        color_params: ColorParamsProtocol | None,
        tone_mapping: str,
        embed_color_profile: bool,
        framing: WindowFraming,
    ) -> tuple[bytes, str]:
        """Decode the selected channels and encode them as PNG bytes.

        Synchronous and CPU-bound; callers run it through `to_thread`. OCIO dispatch
        happens in here too, which is already how EXRSequenceToPreview calls it (per
        frame, on a thread pool), so the request path is known to work off the loop.
        """
        return render_part_png(
            file_path=file_path,
            part_index=part_index,
            selected=selected,
            alpha_channel=alpha_channel,
            ev=ev,
            color_params=color_params,
            tone_mapping=tone_mapping,
            embed_color_profile=embed_color_profile,
            framing=framing,
        )

    def _emit_render(self, outputs: dict[str, Any]) -> None:
        """Update the node outputs from a fresh or cached render."""
        for name, value in outputs.items():
            self.parameter_output_values[name] = value
        self.publish_update_to_parameter(self._image_param.name, outputs[self._image_param.name])

    def _select_entry(self, sequence: Sequence, *, is_sequence: bool) -> tuple[SequenceEntry | None, str]:
        return _select_sequence_entry(
            sequence, self.get_parameter_value(self._frame_param.name), is_sequence=is_sequence
        )


def _select_sequence_entry(
    sequence: Sequence, requested: Any, *, is_sequence: bool
) -> tuple[SequenceEntry | None, str]:
    """Select an entry using the same clamp and missing-frame policy for metadata and rendering."""
    if not is_sequence:
        return sequence.entries[0], ""

    # ParameterInt's converter maps None and "" to 0 and the parameter defaults to 0,
    # so unset and an explicit frame 0 arrive here as the same int. They also pick the
    # same entry either way: the engine's scanner drops negative frame numbers, so
    # sequence.first is never below 0 and the clamp below would land on entries[0].
    if not requested:
        return sequence.entries[0], f" frame {sequence.entries[0].number}"

    try:
        requested = int(requested)
    except (TypeError, ValueError):
        # Unreachable through a connection or a property set: ParameterInt's converter
        # runs first and raises there. This catches a direct parameter_values write,
        # so it fails the node rather than letting the exception escape aprocess and
        # leave the previous run's outputs published.
        return None, f"Frame value {requested!r} is not a number"

    clamped = max(sequence.first, min(sequence.last, requested))
    for entry in sequence.entries:
        if entry.number == clamped:
            note = f" frame {clamped}"
            if clamped != requested:
                note += f" (clamped from {requested}, sequence is {sequence.first}-{sequence.last})"
            return entry, note

    # The scan already applied the user's MissingItemPolicy, so a gap here is their
    # decision to tolerate. Re-deciding it would override them.
    reason = (
        f"Frame {clamped} is not in the sequence (it spans {sequence.first}-{sequence.last} "
        f"with {len(sequence.entries)} frame(s) present). Rescan with a SKIP or FILL_NEAREST "
        f"missing-item policy to tolerate gaps."
    )
    return None, reason


# ---------------------------------------------------------------------------
# Module helpers
# ---------------------------------------------------------------------------


def _color_params_key(color_params: ColorParamsProtocol | None) -> tuple | None:
    """The parts of an OCIO artifact that change the rendered pixels."""
    if color_params is None:
        return None
    return (
        getattr(color_params, "source_colorspace", None),
        getattr(color_params, "display", None),
        getattr(color_params, "view", None),
        getattr(color_params, "config_path", None),
    )


def _running_loop() -> asyncio.AbstractEventLoop | None:
    """The running event loop, or None when called from sync code with no loop."""
    try:
        return asyncio.get_running_loop()
    except RuntimeError:
        return None


def _file_stamp(path: str) -> tuple[int, int] | None:
    """(mtime_ns, size) for a frame, or None when it cannot be stat'd.

    Deliberately not folded into `_scan_key_for`: that stays a pure, non-raising key
    function, and it runs on the event loop for every superseded-scan check.
    """
    try:
        stat = os.stat(path)
    except OSError:
        return None
    return (stat.st_mtime_ns, stat.st_size)


def _sequence_scan_key(directory: str, pattern: str, entries: list[Any]) -> str:
    """Identify a sequence's explicit frame-to-file mapping for in-flight sharing."""
    mapping = tuple(
        (entry.number, entry.path) if isinstance(entry, SequenceEntry) else (entry.get("number"), entry.get("path"))
        for entry in entries
    )
    return repr((directory, pattern, mapping))


def _scan_key_for(raw_path: Any) -> str:
    """A stable selection identity, empty when there is nothing to read."""
    if raw_path is None:
        return ""
    if isinstance(raw_path, Sequence):
        if not raw_path.pattern:
            return raw_path.entries[0].path if raw_path.entries else ""
        return _sequence_scan_key(raw_path.directory, raw_path.pattern, raw_path.entries)
    if isinstance(raw_path, dict):
        entries = raw_path.get("entries") or []
        pattern = raw_path.get("pattern")
        if not pattern:
            return str(entries[0].get("path", "")) if entries else ""
        return _sequence_scan_key(str(raw_path.get("directory") or ""), str(pattern), entries)
    return str(raw_path).strip()


def _describe_path(raw_path: Any) -> str:
    return _scan_key_for(raw_path) or "<empty>"


def _part_choices_for(exr_data: EXRData) -> list[str]:
    """Dropdown labels for a file's parts, index-prefixed so unnamed parts stay distinct."""
    return [f"{index}: {part.header.name or part.name or 'part'}" for index, part in enumerate(exr_data.parts)]


def _part_index_for(exr_data: EXRData, choice: str) -> int | None:
    if not choice:
        return 0
    index_text, _, _ = choice.partition(":")
    try:
        index = int(index_text)
    except ValueError:
        return None
    return index if 0 <= index < len(exr_data.parts) else None


def _part_for(exr_data: EXRData, choice: str) -> EXRPart:
    index = _part_index_for(exr_data, choice)
    return exr_data.parts[index if index is not None else 0]


def _part_label(part: EXRPart, part_index: int) -> str:
    name = part.header.name or part.name
    return f"'{name}'" if name else f"part {part_index}"


def _set_choices(parameter: Parameter, choices: list[str]) -> None:
    for trait in parameter.find_elements_by_type(Options):
        trait.choices = list(choices)
        return
