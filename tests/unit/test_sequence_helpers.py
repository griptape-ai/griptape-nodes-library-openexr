"""Unit tests for the shared Sequence helpers used by LoadEXRSequence and EXRSequenceToPreview."""

from __future__ import annotations

import asyncio

from griptape_nodes_openexr.exr.sequence_helpers import ResolvedSequence, resolve_sequence, run_over_sequence


def _make_sequence_dict(numbers: list[int]) -> dict:
    entries = [{"number": n, "padded_number": f"{n:04d}", "path": f"/frame.{n:04d}.exr"} for n in numbers]
    return {
        "entries": entries,
        "first": numbers[0] if numbers else 0,
        "last": numbers[-1] if numbers else 0,
        "discovered_first": numbers[0] if numbers else 0,
        "discovered_last": numbers[-1] if numbers else 0,
        "padding": 4,
        "pattern": "frame.####.exr",
        "directory": "/",
        "policy": "skip",
        "dropped_negative_number_count": 0,
        "present_numbers": numbers,
        "missing_numbers": [],
    }


class TestResolveSequence:
    def test_none_input_reports_no_sequence(self) -> None:
        result = resolve_sequence(None)
        assert isinstance(result, str)
        assert "No sequence provided" in result

    def test_unparsable_dict_reports_parse_failure(self) -> None:
        result = resolve_sequence({"not": "a sequence"})
        assert isinstance(result, str)
        assert "Failed to parse sequence" in result

    def test_empty_entries_reports_no_entries(self) -> None:
        result = resolve_sequence(_make_sequence_dict([]))
        assert isinstance(result, str)
        assert "no entries" in result

    def test_valid_dict_resolves_entries_in_order(self) -> None:
        result = resolve_sequence(_make_sequence_dict([1001, 1002, 1003]))
        assert isinstance(result, ResolvedSequence)
        assert [e.number for e in result.entries] == [1001, 1002, 1003]

    def test_sequence_instance_is_passed_through(self) -> None:
        from griptape_nodes.common.sequences import Sequence

        sequence = Sequence.model_validate(_make_sequence_dict([5]))
        result = resolve_sequence(sequence)
        assert isinstance(result, ResolvedSequence)
        assert [e.number for e in result.entries] == [5]


class TestRunOverSequence:
    def _entries(self, numbers: list[int]):
        resolved = resolve_sequence(_make_sequence_dict(numbers))
        assert isinstance(resolved, ResolvedSequence)
        return resolved.entries

    def test_results_preserve_entry_order(self) -> None:
        entries = self._entries([1001, 1002, 1003])

        def work(_index: int, entry) -> int:
            return entry.number * 10

        results, errors = asyncio.run(run_over_sequence(entries, threads=2, work=work))
        assert results == [10010, 10020, 10030]
        assert errors == []

    def test_work_receives_contiguous_index_and_entry(self) -> None:
        entries = self._entries([1001, 1002, 1003])
        seen: list[tuple[int, int]] = []

        def work(index: int, entry) -> None:
            seen.append((index, entry.number))

        asyncio.run(run_over_sequence(entries, threads=2, work=work))
        assert sorted(seen) == [(0, 1001), (1, 1002), (2, 1003)]

    def test_failed_entry_reports_real_frame_number_not_index(self) -> None:
        entries = self._entries([1001, 1002, 1003])

        def work(_index: int, entry) -> int:
            if entry.number == 1002:
                msg = "boom"
                raise RuntimeError(msg)
            return entry.number

        results, errors = asyncio.run(run_over_sequence(entries, threads=2, work=work))
        assert results[1] is None
        assert errors == ["frame 1002: boom"]
        assert "frame 1:" not in errors[0]

    def test_on_completed_is_called_once_per_entry(self) -> None:
        entries = self._entries([1001, 1002, 1003])
        counts: list[int] = []

        def work(_index: int, entry) -> int:
            return entry.number

        asyncio.run(run_over_sequence(entries, threads=2, work=work, on_completed=counts.append))
        assert sorted(counts) == [1, 2, 3]
