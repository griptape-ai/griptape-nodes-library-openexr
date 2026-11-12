"""Unit tests for the channel-list inputs on DisplayEXRChannel and SaveEXR."""

from __future__ import annotations

import asyncio
import importlib
from typing import Any
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from griptape_nodes_openexr.exr.channel_selection import resolve_channel_slots, slots_from_channel_list
from griptape_nodes_openexr.exr.exr_header_artifact import EXRChannelArtifact
from griptape_nodes_openexr.exr.exr_types import EXRChannelInfo, PixelType

_DISPLAY_MODULE = "griptape_nodes_openexr.nodes.display_exr_channel"

H, W = 2, 3


def _artifact(name: str, file_path: str = "/fake/a.exr") -> EXRChannelArtifact:
    return EXRChannelArtifact(
        file_path=file_path,
        part_index=0,
        channel=EXRChannelInfo(name=name, pixel_type=PixelType.HALF, x_sampling=1, y_sampling=1),
    )


def _names(slots: dict[str, EXRChannelArtifact]) -> dict[str, str]:
    return {slot: artifact.channel.name for slot, artifact in slots.items()}


class TestSlotsFromChannelList:
    def test_rgba_list_fills_every_slot(self) -> None:
        slots = slots_from_channel_list([_artifact(n) for n in ("R", "G", "B", "A")])
        assert _names(slots) == {"R": "R", "G": "G", "B": "B", "A": "A"}

    def test_stored_order_is_matched_by_name(self) -> None:
        slots = slots_from_channel_list([_artifact(n) for n in ("A", "B", "G", "R")])
        assert _names(slots) == {"R": "R", "G": "G", "B": "B", "A": "A"}

    def test_rgb_without_alpha_leaves_a_empty(self) -> None:
        slots = slots_from_channel_list([_artifact(n) for n in ("R", "G", "B")])
        assert _names(slots) == {"R": "R", "G": "G", "B": "B"}

    def test_multi_layer_list_picks_the_layer_read_exr_renders(self) -> None:
        names = ("R", "G", "B", "A", "diffuse.R", "diffuse.G", "diffuse.B", "depth.Z")
        slots = slots_from_channel_list([_artifact(n) for n in names])
        assert _names(slots) == {"R": "R", "G": "G", "B": "B", "A": "A"}

    def test_prefixed_layer_is_matched(self) -> None:
        names = ("diffuse.R", "diffuse.G", "diffuse.B", "diffuse.A")
        slots = slots_from_channel_list([_artifact(n) for n in names])
        assert _names(slots) == {"R": "diffuse.R", "G": "diffuse.G", "B": "diffuse.B", "A": "diffuse.A"}

    def test_single_channel_fills_rgb_as_grayscale(self) -> None:
        slots = slots_from_channel_list([_artifact("depth.Z")])
        assert _names(slots) == {"R": "depth.Z", "G": "depth.Z", "B": "depth.Z"}

    def test_single_channel_stays_in_r_without_grayscale_fill(self) -> None:
        slots = slots_from_channel_list([_artifact("depth.Z")], grayscale_fills_rgb=False)
        assert _names(slots) == {"R": "depth.Z"}

    def test_empty_list_fills_nothing(self) -> None:
        assert slots_from_channel_list([]) == {}

    def test_duplicate_names_keep_the_first(self) -> None:
        first, second = _artifact("R", "/fake/a.exr"), _artifact("R", "/fake/b.exr")
        slots = slots_from_channel_list([first, second, _artifact("G"), _artifact("B")])
        assert slots["R"] is first


class TestResolveChannelSlots:
    def test_connected_slot_overrides_the_list(self) -> None:
        override = _artifact("diffuse.R", "/fake/b.exr")
        listed = [_artifact(n) for n in ("R", "G", "B", "A")]
        slots = resolve_channel_slots(listed, {"R": override, "G": None, "B": None, "A": None})
        assert slots["R"] is override
        assert _names(slots) == {"R": "diffuse.R", "G": "G", "B": "B", "A": "A"}

    def test_no_list_keeps_only_connected_slots(self) -> None:
        g = _artifact("G")
        assert resolve_channel_slots(None, {"R": None, "G": g, "B": None, "A": None}) == {"G": g}

    def test_slots_come_back_in_rgba_order(self) -> None:
        slots = resolve_channel_slots([_artifact(n) for n in ("A", "B", "G", "R")], {"A": None})
        assert list(slots) == ["R", "G", "B", "A"]


class TestReadEXRChannelsConnect:
    """Read EXR's list outputs connect straight to the channel-list inputs downstream."""

    @pytest.mark.parametrize("output_name", ["channels", "layer_channels"])
    @pytest.mark.parametrize(
        ("module_name", "class_name"),
        [(_DISPLAY_MODULE, "DisplayEXRChannel"), ("griptape_nodes_openexr.nodes.save_exr", "SaveEXR")],
    )
    def test_list_output_is_accepted(self, output_name: str, module_name: str, class_name: str) -> None:
        from griptape_nodes_openexr.nodes.read_exr import ReadEXR

        module = importlib.import_module(module_name)
        with (
            patch("griptape_nodes_openexr.nodes.read_exr.ProjectFileParameter", return_value=MagicMock()),
            patch(f"{module_name}.ProjectFileParameter", return_value=MagicMock()),
        ):
            source = ReadEXR("read")
            sink = getattr(module, class_name)("sink")

        output = source.get_parameter_by_name(output_name)
        channels_in = sink.get_parameter_by_name("channels")
        assert output is not None
        assert channels_in is not None
        assert channels_in.is_incoming_type_allowed(output.output_type)


class TestDisplayEXRChannelList:
    def _run(self, **inputs: object) -> tuple[Any, dict[str, str]]:
        with patch(f"{_DISPLAY_MODULE}.ProjectFileParameter", return_value=MagicMock()):
            from griptape_nodes_openexr.nodes.display_exr_channel import DisplayEXRChannel

            node = DisplayEXRChannel("display")
        for name, value in inputs.items():
            node.set_parameter_value(name, value)

        loaded: dict[str, str] = {}

        def _load(slots: dict[str, EXRChannelArtifact]) -> dict[str, np.ndarray]:
            loaded.update(_names(slots))
            return {slot: np.full((H, W), 0.5, dtype=np.float32) for slot in slots}

        with (
            patch(f"{_DISPLAY_MODULE}.load_slot_channels", side_effect=_load),
            patch(f"{_DISPLAY_MODULE}.ndarray_to_png", return_value=b""),
            patch(f"{_DISPLAY_MODULE}.ImageUrlArtifact"),
        ):
            asyncio.run(node.aprocess())
        return node, loaded

    def test_list_alone_renders_rgba(self) -> None:
        node, loaded = self._run(channels=[_artifact(n) for n in ("R", "G", "B", "A")])
        assert node.get_parameter_value("was_successful") is True, node.get_parameter_value("result_details")
        assert loaded == {"R": "R", "G": "G", "B": "B", "A": "A"}

    def test_connected_slot_replaces_the_listed_channel(self) -> None:
        _, loaded = self._run(
            channels=[_artifact(n) for n in ("R", "G", "B", "A")],
            channel_a=_artifact("matte.A"),
        )
        assert loaded == {"R": "R", "G": "G", "B": "B", "A": "matte.A"}

    def test_empty_list_and_no_slots_fails(self) -> None:
        node, loaded = self._run(channels=[])
        assert node.get_parameter_value("was_successful") is False
        assert loaded == {}
