"""ReadEXR node - load an EXR file or frame sequence and render one frame."""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any

from griptape.artifacts import ImageUrlArtifact
from griptape_nodes.common.macro_parser import MacroSyntaxError, ParsedMacro
from griptape_nodes.common.sequences import MissingItemPolicy, NoTokenBehavior, Sequence, SequenceEntry
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
from griptape_nodes.retained_mode.events.os_events import (
    ScanSequencesRequest,
    ScanSequencesResultSuccess,
)
from griptape_nodes.retained_mode.events.project_events import MacroPath
from griptape_nodes.retained_mode.griptape_nodes import GriptapeNodes
from griptape_nodes.traits.file_system_picker import FileSystemPicker
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
from griptape_nodes_openexr.exr.exr_header_artifact import EXRChannelArtifact, EXRPartArtifact
from griptape_nodes_openexr.exr.exr_io import scan_exr_header
from griptape_nodes_openexr.exr.ocio_helpers import (
    COLOR_MODE_BASIC,
    COLOR_MODE_OCIO,
    ColorParamsProtocol,
    add_color_mode_parameters,
    handle_color_mode_change,
)
from griptape_nodes_openexr.exr.rendering import render_part_png
from griptape_nodes_openexr.exr.tone_mapping import EV_MAX, EV_MIN, TONE_FILMIC
from griptape_nodes_openexr.exr.viewer_launcher import handle_viewer_button_click

if TYPE_CHECKING:
    from griptape_nodes.traits.button import Button, ButtonDetailsMessagePayload

    from griptape_nodes_openexr.exr.exr_types import EXRData, EXRPart

logger = logging.getLogger("griptape_nodes")

# The provider's friendly name, matched case-insensitively by the artifact manager.
_EXR_PROVIDER_NAME = "EXR"

# Slider bounds before a scan has told us the real sequence range. soft_limits=True
# means these only size the track, so a connection driving a frame outside them is
# still accepted.
_DEFAULT_FRAME_MIN = 0
_DEFAULT_FRAME_MAX = 100


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
        self._scan_key: str | None = None
        self._cached_sequence: Sequence | None = None
        self._cached_exr_data: EXRData | None = None
        # Keyed on everything that changes the rendered pixels. The outputs are cached
        # alongside the key because the engine clears parameter_output_values whenever it
        # marks the node unresolved, so a cache hit has to re-emit rather than assume the
        # previous run's values are still there.
        self._render_key: tuple | None = None
        self._render_outputs: dict[str, Any] | None = None
        self._current_exr_path: str | None = None
        # The after_value_set scan runs as a background task so the setter can return.
        self._pending_scan: asyncio.Task | None = None

        self._path_param = Parameter(
            name="path",
            input_types=["Sequence", "str"],
            type="str",
            default_value="",
            tooltip=(
                "Path to an .exr file, or a frame pattern such as render.####.exr. A connected Sequence is used as-is."
            ),
            allowed_modes={ParameterMode.INPUT, ParameterMode.PROPERTY},
        )
        self._path_param.add_trait(
            FileSystemPicker(
                allow_files=True,
                allow_directories=False,
                allow_sequences=True,
                file_extensions=[".exr"],
            )
        )
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
            self._color_mode_param, self._tone_mapping_param, self._color_params_param = add_color_mode_parameters(
                self,
                color_mode_tooltip="Color management mode. 'basic' uses local tone mapping; 'ocio' uses the connected OCIOColorParamsArtifact.",
                tone_mapping_tooltip="Local tone mapping when color_mode is 'basic'. 'filmic' applies Narkowicz 2015 curve; 'linear' clamps to [0, 1].",
                color_params_tooltip="OCIO color parameters artifact (source colorspace, display, view). Required when color_mode is 'ocio'.",
            )
        self.add_node_element(color_group)
        # add_color_mode_parameters registers its parameters on the node directly, which
        # re-parents them out of the group context. Pull them back in so the whole colour
        # section collapses together.
        for parameter in (self._color_mode_param, self._tone_mapping_param, self._color_params_param):
            color_group.add_child(parameter)

        # --- Outputs ---
        self._image_param = Parameter(
            name="image",
            type="ImageUrlArtifact",
            output_type="ImageUrlArtifact",
            tooltip="8-bit sRGB PNG of the current frame, for in-canvas display and downstream image nodes",
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
            handle_color_mode_change(self, value, self._tone_mapping_param.name, self._color_params_param.name)
            return

        if parameter.name == self._part_name_param.name:
            # Layers belong to a part, so the dropdown has to follow the part selection.
            # No rescan: the cached header already describes every part in the file.
            if self._cached_exr_data is not None:
                self._apply_layer_choices(self._cached_exr_data, str(value or ""))
            return

        if parameter.name == self._path_param.name:
            # A live-preview optimisation only. after_value_set is skipped on workflow
            # load and unreliable under parallel execution, so aprocess scans on a miss
            # rather than relying on this having run.
            #
            # The handler for SetParameterValueRequest is synchronous, so whatever happens
            # here happens on the engine's event loop. Scanning a 4K header takes about
            # 1.8s and the preview request blocks until the provider returns, so both go
            # to a worker thread and the setter returns immediately. The scan result is
            # adopted back on the loop in between, so node state is only written there.
            if _running_loop() is None:
                # Bootstrap and worker threads have no loop to protect. Scanning inline
                # keeps the dropdowns populated for callers that set values that way.
                self._scan_and_preview(value)
                return
            previous = self._pending_scan
            if previous is not None and not previous.done():
                # Superseded. Cancelling stops it adopting; the scan already on a worker
                # thread runs to completion and its result is dropped.
                previous.cancel()
            self._pending_scan = asyncio.ensure_future(self._ascan_and_preview(value))

    async def wait_for_pending_scan(self) -> None:
        """Await the background scan started by `after_value_set`, if one is running.

        Exists for tests and for anything that needs the dropdowns populated before it
        reads them. Execution does not need it: `aprocess` scans on a cache miss.
        """
        task = self._pending_scan
        if task is not None:
            self._pending_scan = None
            await asyncio.shield(task)

    async def _ascan_and_preview(self, raw_path: Any) -> None:
        """Scan and request a canvas preview, with only the blocking calls off the loop.

        Swallows its own failures: a bad path must not break the value being set.
        """
        try:
            scanned = await to_thread(self._scan_blocking, raw_path)
        except Exception:
            logger.warning("ReadEXR '%s': failed to scan '%s'", self.name, raw_path, exc_info=True)
            return
        if not self._adopt_if_current(raw_path, scanned):
            return
        await to_thread(self._request_canvas_preview)

    def _scan_and_preview(self, raw_path: Any) -> None:
        """Scan and request a canvas preview inline, for callers with no event loop.

        Swallows its own failures: a bad path must not break the value being set.
        """
        try:
            scanned = self._scan_blocking(raw_path)
        except Exception:
            logger.warning("ReadEXR '%s': failed to scan '%s'", self.name, raw_path, exc_info=True)
            return
        if not self._adopt_if_current(raw_path, scanned):
            return
        self._request_canvas_preview()

    def _adopt_if_current(self, raw_path: Any, scanned: tuple[str, Sequence, EXRData] | None) -> bool:
        """Install a scan result, unless `path` has moved on since the scan started.

        Clicking through the file picker starts a scan per click, and they finish in
        whatever order the files take to read. Without this check a slow earlier scan
        lands after a fast later one and leaves the node describing a file the user has
        already navigated away from.
        """
        wanted = _scan_key_for(self.get_parameter_value(self._path_param.name))
        if _scan_key_for(raw_path) != wanted:
            logger.debug("ReadEXR '%s': discarding a superseded scan of '%s'", self.name, raw_path)
            return False
        if scanned is None:
            if not wanted:
                self._clear_scan()
            return False
        self._adopt_scan(*scanned)
        return True

    # ------------------------------------------------------------------
    # Scanning
    # ------------------------------------------------------------------

    def _clear_scan(self) -> None:
        self._scan_key = None
        self._cached_sequence = None
        self._cached_exr_data = None
        self._render_key = None
        self._render_outputs = None

    def _scan_blocking(self, raw_path: Any) -> tuple[str, Sequence, EXRData] | None:
        """Resolve `raw_path` and read its first entry's header. Returns None when there is nothing to read.

        Synchronous and slow (a 4K multi-part header is ~1.8s), so callers on the event
        loop run it through `to_thread`. Reads `self._scan_key` to skip a redundant scan
        but writes nothing: every mutation belongs to `_adopt_scan` or `_clear_scan` on
        the loop, which is what makes this safe to call from a worker thread.
        """
        key = _scan_key_for(raw_path)
        if not key or key == self._scan_key:
            return None

        sequence = _resolve_to_sequence(raw_path)
        if sequence is None or not sequence.entries:
            return None

        return key, sequence, scan_exr_header(sequence.entries[0].path, header_only=True)

    def _adopt_scan(self, key: str, sequence: Sequence, exr_data: EXRData) -> None:
        """Install a completed scan and refresh the UI from it."""
        self._scan_key = key
        self._cached_sequence = sequence
        self._cached_exr_data = exr_data
        # Any cached render belongs to the file that just went away.
        self._render_key = None
        self._render_outputs = None

        self._apply_scan_to_ui(sequence, exr_data)

    async def _arefresh_scan(self, raw_path: Any) -> None:
        """Scan off the event loop, then adopt the result on it."""
        scanned = await to_thread(self._scan_blocking, raw_path)
        self._adopt_if_current(raw_path, scanned)

    def _apply_scan_to_ui(self, sequence: Sequence, exr_data: EXRData) -> None:
        """Populate the dropdowns and the frame slider range from a fresh scan."""
        part_choices = _part_choices_for(exr_data)
        _set_choices(self._part_name_param, part_choices)
        current_part = self.get_parameter_value(self._part_name_param.name)
        if current_part not in part_choices:
            self.set_parameter_value(self._part_name_param.name, part_choices[0], emit_change=False)
        self._set_hidden(self._part_name_param, hidden=len(part_choices) <= 1)

        self._apply_layer_choices(exr_data, str(self.get_parameter_value(self._part_name_param.name) or ""))

        is_sequence = len(sequence.entries) > 1
        self._set_hidden(self._frame_param, hidden=not is_sequence)
        if is_sequence:
            self._frame_param.min_val = sequence.first
            self._frame_param.max_val = sequence.last
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

    def _apply_layer_choices(self, exr_data: EXRData, part_choice: str) -> None:
        """Repopulate the layer dropdown for the currently selected part."""
        channel_names = [channel.name for channel in _part_for(exr_data, part_choice).channels]
        layer_choices = list_layers(channel_names)
        _set_choices(self._layer_param, layer_choices)
        current_layer = self.get_parameter_value(self._layer_param.name)
        if current_layer not in layer_choices:
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
        sequence = self._cached_sequence
        if sequence is None or not sequence.entries:
            return

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
        if self._cached_exr_data is None:
            return []
        return _part_choices_for(self._cached_exr_data)

    def _layer_choices(self) -> list[str]:
        if self._cached_exr_data is None:
            return []
        part = _part_for(self._cached_exr_data, self.get_parameter_value(self._part_name_param.name))
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
        return handle_viewer_button_click(self.name, self._current_exr_path)

    def _on_fail(self, details: str) -> None:
        self._render_key = None
        self._render_outputs = None
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

    async def aprocess(self) -> None:  # noqa: PLR0911, PLR0912, PLR0915
        self._clear_execution_status()

        raw_path = self.get_parameter_value(self._path_param.name)
        if not _scan_key_for(raw_path):
            self._on_fail("No path provided")
            return

        # LoadEXR hard-fails on a cache miss here. Scanning instead is what makes the
        # node work cold after a workflow reload, and in a worker that never saw the
        # after_value_set hook.
        try:
            await self._arefresh_scan(raw_path)
        except (ValueError, RuntimeError) as e:
            self._on_fail(f"Failed to scan EXR header: {e}")
            return

        sequence = self._cached_sequence
        exr_data = self._cached_exr_data
        if sequence is None or exr_data is None:
            self._on_fail(f"No EXR found at '{_describe_path(raw_path)}'")
            return

        is_sequence = len(sequence.entries) > 1
        self.parameter_output_values[self._first_frame_param.name] = sequence.first if is_sequence else 0
        self.parameter_output_values[self._last_frame_param.name] = sequence.last if is_sequence else 0
        self.parameter_output_values[self._frame_count_param.name] = len(sequence.entries)

        entry, frame_note = self._select_entry(sequence, is_sequence=is_sequence)
        if entry is None:
            self._on_fail(frame_note)
            return

        part_name = str(self.get_parameter_value(self._part_name_param.name) or "")
        part_index = _part_index_for(exr_data, part_name)
        if part_index is None:
            self._on_fail(f"Part '{part_name}' is not in this file")
            return
        part = exr_data.parts[part_index]

        ev = float(self.get_parameter_value(self._exposure_param.name) or 0.0)
        tone_mapping = str(self.get_parameter_value(self._tone_mapping_param.name) or TONE_FILMIC)
        color_mode = str(self.get_parameter_value(self._color_mode_param.name) or COLOR_MODE_BASIC)
        layer = str(self.get_parameter_value(self._layer_param.name) or "")

        color_params: ColorParamsProtocol | None = None
        if color_mode == COLOR_MODE_OCIO:
            color_params = self.get_parameter_value(self._color_params_param.name)  # type: ignore[assignment]
            if color_params is None:
                self._on_fail("OCIO mode requires a connected color_params artifact.")
                return

        render_key = (
            self._scan_key,
            entry.number,
            part_index,
            layer,
            ev,
            color_mode,
            tone_mapping,
            # The artifact's values, not its identity: an upstream OCIO node can mutate
            # and re-emit the same object, which leaves id() unchanged and would serve a
            # stale image for the new display.
            _color_params_key(color_params),
        )
        if render_key == self._render_key and self._render_outputs is not None:
            self._emit_render(self._render_outputs)
            details = f"Reused cached render of {_part_label(part, part_index)}{frame_note}"
            self._set_status_results(was_successful=True, result_details=details)
            return

        # The cached scan is frame 1's header, which drives the dropdowns. Each frame
        # carries its own data window and channel list, so the frame being read
        # supplies its own.
        if entry.path != sequence.entries[0].path:
            try:
                frame_data = await to_thread(scan_exr_header, entry.path, header_only=True)
            except (ValueError, RuntimeError) as e:
                self._on_fail(f"Failed to read the header of{frame_note}: {e}")
                return
            # Matched by name as well as index: a frame that drops a part shifts the
            # ones after it, and reading by index alone would render the wrong one.
            if part_index >= len(frame_data.parts) or frame_data.parts[part_index].header.name != part.header.name:
                self._on_fail(f"Part '{part_name}' is not in{frame_note}")
                return
            exr_data = frame_data
            part = frame_data.parts[part_index]

        self._current_exr_path = entry.path

        channel_names = [channel.name for channel in part.channels]
        candidates = channels_in_layer(channel_names, layer) if layer else channel_names
        if not candidates:
            # The layer dropdown is built from frame 1, so a later frame can lack it.
            self._on_fail(f"Layer '{layer}' is not in {_part_label(part, part_index)}{frame_note}")
            return
        selected = select_display_channels(candidates)
        if selected is None:
            self._on_fail(f"No channels found in {_part_label(part, part_index)}")
            return
        alpha_channel = select_alpha_channel(candidates, selected)

        # Decode, tone map and encode together on a worker thread. On a 4K frame this is
        # ~3.2s of CPU, which would otherwise freeze the canvas for the whole run.
        try:
            png_bytes, tone_mode = await to_thread(
                self._render_png,
                entry.path,
                part_index,
                selected,
                alpha_channel,
                ev,
                color_params,
                tone_mapping,
            )
        except (ValueError, RuntimeError) as e:
            self._on_fail(str(e))
            return

        try:
            # The frame number goes into the filename so a loop leaves a clean numbered
            # set; a single file keeps the plain name.
            extra_vars: dict[str, str | int] = {"_index": entry.number} if is_sequence else {}
            destination = self._output_file.build_file(**extra_vars)
            saved = destination.write_bytes(png_bytes)
            artifact = ImageUrlArtifact(saved.location)
        except Exception as e:  # noqa: BLE001 - surfaced as a node failure
            self._on_fail(f"Failed to save image: {e}")
            return

        frame_number = entry.number if is_sequence else None
        part_artifact = EXRPartArtifact(
            file_path=entry.path,
            part_index=part_index,
            name=part.header.name,
            width=part.width,
            height=part.height,
            header=part.header,
            channels=part.channels,
            frame_number=frame_number,
        )
        channel_artifacts = [
            EXRChannelArtifact(file_path=entry.path, part_index=part_index, channel=channel)
            # R, G, B, A rather than the alphabetical order EXR stores, matching LoadEXR.
            for channel in order_channels_for_display(part.channels)
        ]

        outputs = {
            self._image_param.name: artifact,
            self._part_param.name: part_artifact,
            self._channels_param.name: channel_artifacts,
            self._image_width_param.name: part.width,
            self._image_height_param.name: part.height,
            self._part_count_param.name: len(exr_data.parts),
            self._channel_count_param.name: len(part.channels),
        }
        self._emit_render(outputs)

        self._render_key = render_key
        self._render_outputs = outputs

        alpha_info = f", alpha: {alpha_channel}" if alpha_channel else ""
        details = (
            f"Read {_part_label(part, part_index)}{frame_note} - {part.width}x{part.height}, "
            f"channels: {selected}{alpha_info}, EV={ev:+.1f}, {tone_mode}"
        )
        self._set_status_results(was_successful=True, result_details=details)
        logger.info("ReadEXR '%s': %s", self.name, details)

    def _render_png(  # noqa: PLR0913, PLR0917
        self,
        file_path: str,
        part_index: int,
        selected: list[str],
        alpha_channel: str | None,
        ev: float,
        color_params: ColorParamsProtocol | None,
        tone_mapping: str,
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
        )

    def _emit_render(self, outputs: dict[str, Any]) -> None:
        """Publish a render's outputs, whether it was just computed or came from the cache."""
        for name, value in outputs.items():
            self.parameter_output_values[name] = value
        self.publish_update_to_parameter(self._image_param.name, outputs[self._image_param.name])

    def _select_entry(self, sequence: Sequence, *, is_sequence: bool) -> tuple[SequenceEntry | None, str]:
        """Pick the entry for the requested frame.

        Returns the entry plus a note for the status details, or `(None, reason)`
        when the requested frame is absent from the sequence.
        """
        if not is_sequence:
            return sequence.entries[0], ""

        requested = self.get_parameter_value(self._frame_param.name)
        if not requested:
            return sequence.entries[0], f" frame {sequence.entries[0].number}"

        requested = int(requested)
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


def _sequence_scan_key(directory: str, pattern: str, entries: list[Any]) -> str:
    """A cache key covering everything that identifies a connected Sequence's content.

    The pattern alone (e.g. "render.####.exr") is shared by every version of a shot,
    so a re-scan that picks up newly rendered frames, or a switch from one render
    directory to another with the same filenames, must still produce a different key.
    """
    numbers = ",".join(str(e.number if isinstance(e, SequenceEntry) else e.get("number")) for e in entries)
    return f"{directory}|{pattern}|{numbers}"


def _scan_key_for(raw_path: Any) -> str:
    """A stable cache key for a path parameter value, empty when there is nothing to read."""
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


def _resolve_to_sequence(raw_path: Any) -> Sequence | None:
    """Turn a path parameter value into a Sequence.

    A connected Sequence (or its serialized dict form) wins over a typed path. A
    typed path is scanned over the event bus, where NoTokenBehavior.SINGLE_FILE
    makes a bare filename come back as a one-entry sequence. That is what lets
    single file and sequence share one input and one code path.
    """
    if isinstance(raw_path, Sequence):
        return raw_path
    if isinstance(raw_path, dict):
        try:
            return Sequence.model_validate(raw_path)
        except Exception:
            logger.warning("ReadEXR: could not parse the connected sequence value", exc_info=True)
            return None

    path = str(raw_path or "").strip()
    if not path:
        return None

    result = GriptapeNodes.handle_request(
        ScanSequencesRequest(
            path=path,
            policy=MissingItemPolicy.SKIP,
            no_token_behavior=NoTokenBehavior.SINGLE_FILE,
        )
    )
    if not isinstance(result, ScanSequencesResultSuccess) or not result.sequences:
        return None
    return result.sequences[0]


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
