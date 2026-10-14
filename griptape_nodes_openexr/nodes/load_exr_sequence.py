"""LoadEXRSequence node — load per-frame EXR structure from a frame sequence."""

from __future__ import annotations

import asyncio
import logging
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from griptape_nodes.common.sequences import Sequence
from griptape_nodes.exe_types.core_types import Parameter, ParameterGroup, ParameterMode
from griptape_nodes.exe_types.node_types import SuccessFailureNode
from griptape_nodes.exe_types.param_types.parameter_int import ParameterInt
from griptape_nodes.retained_mode.events.config_events import GetConfigValueRequest, GetConfigValueResultSuccess
from griptape_nodes.retained_mode.griptape_nodes import GriptapeNodes

from griptape_nodes_openexr.exr.exr_header_artifact import EXRPartArtifact
from griptape_nodes_openexr.exr.exr_io import scan_exr_header

logger = logging.getLogger("griptape_nodes")

_DEFAULT_THREADS = 4


class LoadEXRSequence(SuccessFailureNode):
    """Load per-frame EXR structure from a frame sequence, without reading pixels.

    Accepts a Sequence artifact (from ScanSequenceNode/ScanSplitSequenceNode),
    scans each frame's header (reusing scan_exr_header, header-only by default),
    and outputs one EXRPartArtifact per frame. Header scanning is parallelised
    across a configurable thread pool, following the same pattern as
    EXRSequenceToPreview. v1 is single-part: the same part_index is read from
    every frame.
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
            tooltip="One EXR part descriptor per frame, ordered by frame number",
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
        if raw is None:
            self._on_fail("No sequence provided — connect a ScanSequenceNode.")
            return

        try:
            sequence = Sequence.model_validate(raw) if isinstance(raw, dict) else raw
        except Exception as e:
            self._on_fail(f"Failed to parse sequence: {e}")
            return

        exr_paths = [entry.path for entry in sequence.entries]
        if not exr_paths:
            self._on_fail("Sequence contains no entries.")
            return

        total = len(exr_paths)
        self.parameter_output_values[self._frame_count_param.name] = total
        self.publish_update_to_parameter(self._frame_count_param.name, total)

        part_index = int(self.get_parameter_value(self._part_index_param.name))
        threads = int(self.get_parameter_value(self._threads_param.name))

        header_only_result = GriptapeNodes.handle_request(GetConfigValueRequest(category_and_key="openexr.header_only"))
        header_only: bool = (
            bool(header_only_result.value) if isinstance(header_only_result, GetConfigValueResultSuccess) else True
        )

        logger.info("%s: scanning %d EXR frame header(s) (%d thread(s))", self.name, total, threads)

        results: list[EXRPartArtifact | None] = [None] * total
        errors: list[str] = []

        loop = asyncio.get_running_loop()

        def _scan_frame(path: str) -> EXRPartArtifact:
            exr_data = scan_exr_header(path, header_only=header_only)
            if not 0 <= part_index < len(exr_data.parts):
                msg = f"part_index {part_index} out of range (file has {len(exr_data.parts)} part(s))"
                raise ValueError(msg)
            part = exr_data.parts[part_index]
            return EXRPartArtifact(
                file_path=path,
                part_index=part_index,
                name=part.header.name,
                width=part.width,
                height=part.height,
                header=part.header,
                channels=part.channels,
            )

        async def _run_frame(i: int, path: str) -> tuple[int, EXRPartArtifact | None, Exception | None]:
            try:
                artifact = await loop.run_in_executor(executor, _scan_frame, path)
                return i, artifact, None
            except Exception as e:
                return i, None, e

        with ThreadPoolExecutor(max_workers=threads) as executor:
            tasks = [_run_frame(i, path) for i, path in enumerate(exr_paths)]
            for coro in asyncio.as_completed(tasks):
                idx, artifact, err = await coro
                if err is not None:
                    errors.append(f"frame {idx}: {err}")
                    logger.warning("%s: frame %d failed: %s", self.name, idx, err)
                    continue
                results[idx] = artifact

        scanned = [(idx, artifact) for idx, artifact in enumerate(results) if artifact is not None]

        if not scanned:
            self._on_fail(f"All {total} frame(s) failed to scan: {'; '.join(errors)}")
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
        for idx, artifact in scanned:
            if _layout_key(artifact) != reference_key:
                errors.append(f"frame {idx}: resolution/channel layout does not match sequence")
                logger.warning("%s: frame %d: layout mismatch", self.name, idx)
                continue
            ordered.append(artifact)

        self.parameter_output_values[self._parts_param.name] = ordered
        self.publish_update_to_parameter(self._parts_param.name, ordered)

        if errors:
            logger.warning("%s: %d frame(s) failed: %s", self.name, len(errors), "; ".join(errors))

        error_note = f", {len(errors)} frame error(s)" if errors else ""
        details = f"Scanned {len(ordered)}/{total} frame(s){error_note}"
        self._set_status_results(was_successful=True, result_details=details)
        logger.info("%s: %s", self.name, details)
