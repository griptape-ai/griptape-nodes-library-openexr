"""Unit tests for load_slot_channels(): one decode per source file."""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from griptape_nodes_openexr.exr.exr_io import load_slot_channels

if TYPE_CHECKING:
    from collections.abc import Mapping

_LOAD = "griptape_nodes_openexr.exr.exr_io._load_parts_channels"


def _channel(name: str, file_path: str = "/tmp/a.exr", part_index: int = 0) -> MagicMock:
    artifact = MagicMock()
    artifact.file_path = file_path
    artifact.part_index = part_index
    artifact.channel.name = name
    return artifact


def _plane(file_path: str, part_index: int, name: str) -> np.ndarray:
    return np.full((2, 2), hash((file_path, part_index, name)) % 100, dtype=np.float32)


def _fake_load(file_path: str, requests: Mapping[int, list[str]]) -> dict[int, dict[str, np.ndarray]]:
    return {i: {name: _plane(file_path, i, name) for name in names} for i, names in requests.items()}


class TestGrouping:
    def test_slots_from_one_part_are_read_in_one_call(self) -> None:
        slots = {slot: _channel(slot) for slot in "RGBA"}
        with patch(_LOAD, side_effect=_fake_load) as load:
            load_slot_channels(slots)
        load.assert_called_once_with("/tmp/a.exr", {0: ["R", "G", "B", "A"]})

    def test_slots_from_different_parts_of_one_file_are_read_in_one_call(self) -> None:
        slots = {"R": _channel("R"), "G": _channel("Z", part_index=1), "B": _channel("B")}
        with patch(_LOAD, side_effect=_fake_load) as load:
            pixels = load_slot_channels(slots)
        load.assert_called_once_with("/tmp/a.exr", {0: ["R", "B"], 1: ["Z"]})
        np.testing.assert_array_equal(pixels["G"], _plane("/tmp/a.exr", 1, "Z"))

    def test_slots_from_different_files_are_read_separately(self) -> None:
        slots = {"R": _channel("R"), "G": _channel("G", file_path="/tmp/b.exr"), "B": _channel("B")}
        with patch(_LOAD, side_effect=_fake_load) as load:
            pixels = load_slot_channels(slots)
        assert [c.args for c in load.call_args_list] == [
            ("/tmp/a.exr", {0: ["R", "B"]}),
            ("/tmp/b.exr", {0: ["G"]}),
        ]
        np.testing.assert_array_equal(pixels["G"], _plane("/tmp/b.exr", 0, "G"))

    def test_channel_shared_by_slots_is_requested_once(self) -> None:
        slots = {slot: _channel("Y") for slot in "RGB"}
        with patch(_LOAD, side_effect=_fake_load) as load:
            pixels = load_slot_channels(slots)
        load.assert_called_once_with("/tmp/a.exr", {0: ["Y"]})
        assert set(pixels) == {"R", "G", "B"}

    def test_result_follows_slot_order(self) -> None:
        slots = {"R": _channel("R"), "G": _channel("G", file_path="/tmp/b.exr"), "B": _channel("B")}
        with patch(_LOAD, side_effect=_fake_load):
            assert list(load_slot_channels(slots)) == ["R", "G", "B"]


class TestErrors:
    @pytest.mark.parametrize("error", [ValueError, RuntimeError])
    def test_single_slot_failure_names_the_slot(self, error: type[Exception]) -> None:
        slots = {"R": _channel("R"), "G": _channel("G", file_path="/tmp/bad.exr")}

        def load(file_path: str, requests: Mapping[int, list[str]]) -> dict[int, dict[str, np.ndarray]]:
            if file_path == "/tmp/bad.exr":
                raise error("boom")
            return _fake_load(file_path, requests)

        with patch(_LOAD, side_effect=load), pytest.raises(error, match=r"^Failed to load G channel: boom$"):
            load_slot_channels(slots)

    def test_grouped_failure_names_every_slot_in_the_group(self) -> None:
        slots = {slot: _channel(slot) for slot in "RGB"}
        with (
            patch(_LOAD, side_effect=RuntimeError("boom")),
            pytest.raises(RuntimeError, match=r"^Failed to load R, G, B channels: boom$"),
        ):
            load_slot_channels(slots)
