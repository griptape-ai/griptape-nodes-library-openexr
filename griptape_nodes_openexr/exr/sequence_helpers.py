"""Shared helpers for nodes that process a Sequence of EXR frames.

Factors out the Sequence parsing and threaded per-frame execution shared by
LoadEXRSequence and EXRSequenceToPreview, so error-reporting conventions
(e.g. citing the real sequence frame number, not a list index) stay in one
place.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Any

from griptape_nodes.common.sequences import Sequence, SequenceEntry


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

    try:
        sequence = Sequence.model_validate(raw) if isinstance(raw, dict) else raw
    except Exception as e:
        return f"Failed to parse sequence: {e}"

    if not sequence.entries:
        return "Sequence contains no entries."

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
            sequence frame number (`entry.number`) — useful for callers that
            need a contiguous output ordering (e.g. an image sequence fed to
            ffmpeg) distinct from frame identity.
        on_completed: Optional callback invoked after each entry finishes
            (success or failure), with the running count of completed entries.
            Useful for progress reporting; errors from `work` are still
            collected and returned even without this callback.

    Returns:
        A `(results, errors)` pair. `results` has one slot per entry, in the
        same order as `entries`, `None` where `work` raised. `errors` holds
        one message per failed entry, formatted as `"frame {entry.number}: {exc}"`.
    """
    results: list[T | None] = [None] * len(entries)
    errors: list[str] = []

    loop = asyncio.get_running_loop()

    async def _run_one(
        i: int, entry: SequenceEntry, executor: ThreadPoolExecutor
    ) -> tuple[int, T | None, Exception | None]:
        try:
            result = await loop.run_in_executor(executor, work, i, entry)
            return i, result, None
        except Exception as e:  # noqa: BLE001 - reported as a per-frame error, not raised
            return i, None, e

    with ThreadPoolExecutor(max_workers=threads) as executor:
        tasks = [_run_one(i, entry, executor) for i, entry in enumerate(entries)]
        completed = 0
        for coro in asyncio.as_completed(tasks):
            idx, result, err = await coro
            if err is not None:
                errors.append(f"frame {entries[idx].number}: {err}")
            else:
                results[idx] = result
            completed += 1
            if on_completed is not None:
                on_completed(completed)

    return results, errors
