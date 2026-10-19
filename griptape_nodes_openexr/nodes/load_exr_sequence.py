"""LoadEXRSequence node — load per-frame EXR structure from a frame sequence."""

from __future__ import annotations

import logging
from collections import Counter
from typing import Any

from griptape_nodes.common.sequences import SequenceEntry
from griptape_nodes.exe_types.core_types import Parameter, ParameterGroup, ParameterMode
from griptape_nodes.exe_types.node_types import SuccessFailureNode
from griptape_nodes.exe_types.param_types.parameter_int import ParameterInt
from griptape_nodes.exe_types.param_types.parameter_string import ParameterString
from griptape_nodes.traits.options import Options

from griptape_nodes_openexr.exr.exr_header_artifact import EXRPartArtifact
from griptape_nodes_openexr.exr.exr_io import scan_exr_header
from griptape_nodes_openexr.exr.sequence_helpers import resolve_sequence, run_over_sequence

logger = logging.getLogger("griptape_nodes")

_DEFAULT_THREADS = 4

MISMATCH_POLICY_SKIP = "skip"
MISMATCH_POLICY_FAIL = "fail"


class LoadEXRSequence(SuccessFailureNode):
    """Load per-frame EXR structure from a frame sequence, without reading pixels.

    Accepts a Sequence artifact (from ScanSequenceNode/ScanSplitSequenceNode),
    scans each frame's header (reusing scan_exr_header, always header-only since
    EXRPartArtifact carries no pixel data), and outputs one EXRPartArtifact per
    frame. Header scanning is parallelised across a configurable thread pool,
    following the same pattern as EXRSequenceToPreview. v1 is single-part: the
    same part_index is read from every frame.
    """

    def __init__(self, name: str, metadata: dict[Any, Any] | None = None) -> None:
        super().__init__(name, metadata)

        self._sequence_param = Parameter(
            name="sequence",
            input_types=["Sequence"],
            type="Sequence",
            tooltip="EXR frame sequence from ScanSequenceNode.",
            allowed_modes={ParameterMode.INPUT},
        )
        self.add_parameter(self._sequence_param)

        self._part_index_param = ParameterInt(
            name="part_index",
            default_value=0,
            min_val=0,
            tooltip="Zero-based EXR part index to scan in every frame",
            allowed_modes={ParameterMode.INPUT, ParameterMode.PROPERTY},
        )
        self.add_parameter(self._part_index_param)

        self._on_mismatch_param = ParameterString(
            name="on_mismatch",
            default_value=MISMATCH_POLICY_SKIP,
            tooltip=(
                "What to do when a frame fails to scan or doesn't match the sequence's "
                "resolution/channel layout. 'skip' drops only that frame (parts may be "
                "shorter than frame_count); 'fail' fails the whole run instead."
            ),
            allowed_modes={ParameterMode.INPUT, ParameterMode.PROPERTY},
        )
        self._on_mismatch_param.add_trait(Options(choices=[MISMATCH_POLICY_SKIP, MISMATCH_POLICY_FAIL]))
        self.add_parameter(self._on_mismatch_param)

        # --- Advanced group ---
        advanced_group = ParameterGroup(name="Advanced", ui_options={"collapsed": True})
        with advanced_group:
            self._threads_param = ParameterInt(
                name="threads",
                default_value=_DEFAULT_THREADS,
                min_val=1,
                max_val=16,
                tooltip="Number of concurrent header-scan workers (1-16)",
                allowed_modes={ParameterMode.INPUT, ParameterMode.PROPERTY},
            )
        self.add_node_element(advanced_group)

        # --- Outputs ---
        self._parts_param = Parameter(
            name="parts",
            display_name="Parts",
            type="list[EXRPartArtifact]",
            output_type="list[EXRPartArtifact]",
            tooltip=(
                "One EXR part descriptor per scanned frame, ordered by frame number. "
                "Each artifact's frame_number field carries the real sequence frame "
                "number — list position does not. May be shorter than frame_count "
                "when on_mismatch is 'skip' and some frames were dropped."
            ),
            allowed_modes={ParameterMode.OUTPUT},
            settable=False,
        )
        self.add_parameter(self._parts_param)

        self._frame_count_param = ParameterInt(
            name="frame_count",
            default_value=0,
            tooltip="Number of frames in the sequence",
            allowed_modes={ParameterMode.OUTPUT},
        )
        self.add_parameter(self._frame_count_param)

        self._create_status_parameters(
            result_details_tooltip="Details about the sequence scan result",
            result_details_placeholder="Scan details will appear here.",
            parameter_group_initially_collapsed=True,
        )

    def _on_fail(self, details: str) -> None:
        self.parameter_output_values[self._parts_param.name] = None
        self.parameter_output_values[self._frame_count_param.name] = 0
        self._set_status_results(was_successful=False, result_details=details)

    async def aprocess(self) -> None:
        self._clear_execution_status()
        self.parameter_output_values[self._parts_param.name] = None

        raw = self.get_parameter_value(self._sequence_param.name)
        resolved = resolve_sequence(raw)
        if isinstance(resolved, str):
            self._on_fail(resolved)
            return
        entries = resolved.entries

        total = len(entries)
        self.parameter_output_values[self._frame_count_param.name] = total
        self.publish_update_to_parameter(self._frame_count_param.name, total)

        part_index = int(self.get_parameter_value(self._part_index_param.name))
        threads = int(self.get_parameter_value(self._threads_param.name))
        on_mismatch = str(self.get_parameter_value(self._on_mismatch_param.name))

        logger.info("%s: scanning %d EXR frame header(s) (%d thread(s))", self.name, total, threads)

        def _scan_frame(_index: int, entry: SequenceEntry) -> EXRPartArtifact:
            # EXRPartArtifact never carries pixel data, so there is nothing for a full
            # (non-header-only) scan to buy here beyond slower, discarded pixel reads —
            # always scan header-only regardless of the openexr.header_only setting.
            exr_data = scan_exr_header(entry.path, header_only=True)
            if not 0 <= part_index < len(exr_data.parts):
                msg = f"part_index {part_index} out of range (file has {len(exr_data.parts)} part(s))"
                raise ValueError(msg)
            part = exr_data.parts[part_index]
            return EXRPartArtifact(
                file_path=entry.path,
                part_index=part_index,
                name=part.header.name,
                width=part.width,
                height=part.height,
                header=part.header,
                channels=part.channels,
                frame_number=entry.number,
            )

        results, errors = await run_over_sequence(entries, threads=threads, work=_scan_frame)
        for message in errors:
            logger.warning("%s: %s", self.name, message)

        scanned = [(idx, artifact) for idx, artifact in enumerate(results) if artifact is not None]

        if not scanned:
            self._on_fail(f"All {total} frame(s) failed to scan: {'; '.join(errors)}")
            return

        if on_mismatch == MISMATCH_POLICY_FAIL and len(scanned) != total:
            self._on_fail(f"{total - len(scanned)}/{total} frame(s) failed to scan: {'; '.join(errors)}")
            return

        # Use the most common (width, height, channel layout) as the reference, so an outlier
        # frame (e.g. a slate) is rejected instead of the frames that are consistent.
        def _layout_key(artifact: EXRPartArtifact) -> tuple[int, int, tuple[str, ...]]:
            return (artifact.width, artifact.height, tuple(c.name for c in artifact.channels))

        layout_counts = Counter(_layout_key(artifact) for _, artifact in scanned)
        reference_key, reference_count = layout_counts.most_common(1)[0]

        if reference_count <= len(scanned) // 2:
            self._on_fail(
                f"Sequence has no consistent part layout across {len(scanned)} scanned frame(s): "
                f"most common layout only covers {reference_count}/{len(scanned)}."
            )
            return

        ordered: list[EXRPartArtifact] = []
        mismatched = 0
        for idx, artifact in scanned:
            if _layout_key(artifact) != reference_key:
                frame_number = entries[idx].number
                errors.append(f"frame {frame_number}: resolution/channel layout does not match sequence")
                logger.warning("%s: frame %d: layout mismatch", self.name, frame_number)
                mismatched += 1
                continue
            ordered.append(artifact)

        if on_mismatch == MISMATCH_POLICY_FAIL and mismatched:
            self._on_fail(
                f"{mismatched}/{len(scanned)} scanned frame(s) did not match the sequence layout: {'; '.join(errors)}"
            )
            return

        self.parameter_output_values[self._parts_param.name] = ordered
        self.publish_update_to_parameter(self._parts_param.name, ordered)

        error_note = f", {len(errors)} frame error(s)" if errors else ""
        details = f"Scanned {len(ordered)}/{total} frame(s){error_note}"
        self._set_status_results(was_successful=True, result_details=details)
        logger.info("%s: %s", self.name, details)
