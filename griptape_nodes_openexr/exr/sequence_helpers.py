"""Shared EXR sequence resolution, path inputs and per-frame execution.

ReadEXR and EXRSequenceToPreview share path discovery and picker configuration.
LoadEXRSequence uses the connected-sequence parser and bounded frame executor.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Any

from griptape_nodes.common.sequences import MissingItemPolicy, NoTokenBehavior, Sequence, SequenceEntry
from griptape_nodes.exe_types.core_types import Parameter, ParameterMode
from griptape_nodes.retained_mode.events.os_events import ScanSequencesRequest, ScanSequencesResultSuccess
from griptape_nodes.retained_mode.griptape_nodes import GriptapeNodes
from griptape_nodes.traits.file_system_picker import FileSystemPicker
from pydantic import ValidationError


def sequence_path_parameter(name: str) -> Parameter:
    """Build an editable EXR path input that also accepts connected Sequences."""
    parameter = Parameter(
        name=name,
        input_types=["Sequence", "str"],
        type="str",
        default_value="",
        tooltip="Path to an .exr file, or a frame pattern such as render.####.exr. A connected Sequence is used as-is.",
        allowed_modes={ParameterMode.INPUT, ParameterMode.PROPERTY},
    )
    parameter.add_trait(
        FileSystemPicker(allow_files=True, allow_directories=False, allow_sequences=True, file_extensions=[".exr"])
    )
    return parameter


def _parse_sequence(raw: Any) -> Sequence | str:
    """Validate a connected Sequence without changing its entries or policy."""
    try:
        sequence = Sequence.model_validate(raw)
    except ValidationError as error:
        return f"Failed to parse sequence: {error}"
    if not sequence.entries:
        return "Sequence contains no entries."
    return sequence


def resolve_sequence_path(raw: Any) -> Sequence | str:
    """Resolve an EXR path or connected Sequence, returning diagnostics on failure.

    Paths use the engine's SKIP and SINGLE_FILE policies. Connected Sequences
    retain their entries and policy. This function performs blocking I/O;
    async callers must use griptape_nodes.utils.async_utils.to_thread.
    """
    if raw is None or (isinstance(raw, str) and not raw.strip()):
        return "No sequence provided. Enter an EXR path or connect a Sequence."
    if not isinstance(raw, str):
        return _parse_sequence(raw)

    path = raw.strip()
    result = GriptapeNodes.handle_request(
        ScanSequencesRequest(path=path, policy=MissingItemPolicy.SKIP, no_token_behavior=NoTokenBehavior.SINGLE_FILE)
    )
    if result.failed():
        return f"Failed to scan sequence '{path}': {result.result_details}"
    if not isinstance(result, ScanSequencesResultSuccess) or not result.sequences:
        return f"No EXR frames matched '{path}'. Check the path and frame pattern."
    return _parse_sequence(result.sequences[0])


@dataclass
class ResolvedSequence:
    """A successfully parsed, non-empty Sequence ready for per-frame processing."""

    entries: list[SequenceEntry]


def resolve_sequence(raw: Any) -> ResolvedSequence | str:
    """Parse a raw Sequence parameter value and validate it has entries.

    Args:
        raw: The value read from a `sequence` input parameter. `None` means
            nothing is connected. A `dict` is the common case when the value
            crossed a serialization boundary; a `Sequence` is passed through.

    Returns:
        A `ResolvedSequence` on success, or an error message string describing
        why the sequence could not be used (no input connected, unparsable, or
        empty) suitable for passing straight to a node's `_on_fail`.
    """
    if raw is None:
        return "No sequence provided — connect a ScanSequenceNode."

    sequence = _parse_sequence(raw)
    if isinstance(sequence, str):
        return sequence
    return ResolvedSequence(entries=sequence.entries)


async def run_over_sequence[T](
    entries: list[SequenceEntry],
    *,
    threads: int,
    work: Callable[[int, SequenceEntry], T],
    on_completed: Callable[[int], None] | None = None,
) -> tuple[list[T | None], list[str]]:
    """Run `work` for every sequence entry on a thread pool, in parallel.

    Args:
        entries: Sequence entries to process, in sequence order.
        threads: Max worker threads.
        work: Synchronous callable invoked with each entry's (0-based list
            index, entry) on a worker thread. Raises to report a per-frame
            failure. The index is the entry's position in `entries`, not its
            sequence frame number (`entry.number`), so it orders output
            independently of frame identity. It has a hole wherever `work`
            raises: a caller that needs gap-free numbering (e.g. an image
            sequence fed to ffmpeg) must re-number from the non-`None` results.
        on_completed: Optional callback invoked after each entry finishes
            (success or failure), with the running count of completed entries.
            Useful for progress reporting; errors from `work` are still
            collected and returned even without this callback.

    Returns:
        A `(results, errors)` pair. `results` has one slot per entry, in the
        same order as `entries`, `None` where `work` raised. `errors` holds
        one message per failed entry, in sequence order, formatted as
        `"frame {entry.number}: {exc}"`.
    """
    results: list[T | None] = [None] * len(entries)
    failures: dict[int, str] = {}

    loop = asyncio.get_running_loop()

    async def _run_one(i: int, entry: SequenceEntry, executor: ThreadPoolExecutor) -> tuple[int, T | None, str | None]:
        try:
            result = await loop.run_in_executor(executor, work, i, entry)
            return i, result, None
        except Exception as e:  # noqa: BLE001 - reported as a per-frame error, not raised
            # Keep only the message: the exception's traceback pins the frame's
            # pixel buffers until the whole sequence finishes.
            return i, None, f"frame {entry.number}: {e}"

    with ThreadPoolExecutor(max_workers=threads) as executor:
        tasks = [_run_one(i, entry, executor) for i, entry in enumerate(entries)]
        completed = 0
        for coro in asyncio.as_completed(tasks):
            idx, result, err = await coro
            if err is not None:
                failures[idx] = err
            else:
                results[idx] = result
            completed += 1
            if on_completed is not None:
                on_completed(completed)

    errors = [failures[idx] for idx in sorted(failures)]
    return results, errors
