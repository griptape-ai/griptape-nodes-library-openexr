"""color_mode defaults and visibility, shared by every node with colour management controls."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from griptape_nodes_openexr.exr.ocio_helpers import COLOR_MODE_BASIC, COLOR_MODE_OCIO
from griptape_nodes_openexr.exr.tone_mapping import (
    TONE_ACES2065_SRGB,
    TONE_ACESCG_REC709,
    TONE_ACESCG_SRGB,
    TONE_CHOICES,
)
from griptape_nodes_openexr.nodes.display_exr_channel import DisplayEXRChannel
from griptape_nodes_openexr.nodes.display_exr_part import DisplayEXRPart
from griptape_nodes_openexr.nodes.exr_sequence_to_preview import EXRSequenceToPreview
from griptape_nodes_openexr.nodes.read_exr import ReadEXR

_OCIO_MODULE = "griptape_nodes_openexr.exr.ocio_helpers"

_NODE_TYPES = [ReadEXR, DisplayEXRPart, DisplayEXRChannel, EXRSequenceToPreview]


def _make_node(node_type: type, *, ocio_available: bool, metadata: dict | None = None):
    detected = MagicMock() if ocio_available else None
    with patch(f"{_OCIO_MODULE}.find_colorspace_transform_request_type", return_value=detected):
        return node_type("test_node", metadata=metadata)


def _hidden(node, name: str) -> bool:
    parameter = node.get_parameter_by_name(name)
    assert parameter is not None, name
    return bool(parameter.ui_options.get("hide", False))


def _assert_visibility(node, *, ocio_active: bool) -> None:
    assert _hidden(node, "tone_mapping") is ocio_active
    assert _hidden(node, "color_params") is not ocio_active
    if node.get_parameter_by_name("embed_color_profile") is not None:
        assert _hidden(node, "embed_color_profile") is ocio_active


@pytest.mark.parametrize("node_type", _NODE_TYPES)
@pytest.mark.parametrize("ocio_available", [False, True])
class TestColorModeDefaults:
    def test_new_node_defaults_to_basic(self, node_type: type, ocio_available: bool) -> None:
        node = _make_node(node_type, ocio_available=ocio_available)

        assert node.get_parameter_by_name("color_mode").default_value == COLOR_MODE_BASIC
        assert node.get_parameter_value("color_mode") == COLOR_MODE_BASIC

    def test_new_node_shows_basic_controls(self, node_type: type, ocio_available: bool) -> None:
        node = _make_node(node_type, ocio_available=ocio_available)

        _assert_visibility(node, ocio_active=False)

    def test_selecting_ocio_shows_ocio_controls(self, node_type: type, ocio_available: bool) -> None:
        node = _make_node(node_type, ocio_available=ocio_available)

        node.set_parameter_value("color_mode", COLOR_MODE_OCIO)

        assert node.get_parameter_value("color_mode") == COLOR_MODE_OCIO
        _assert_visibility(node, ocio_active=True)

    def test_restored_ocio_value_without_metadata_shows_ocio_controls(
        self, node_type: type, ocio_available: bool
    ) -> None:
        # A workflow saved while 'ocio' was the default stores the value but no "_color_mode"
        # metadata, and the engine restores the value with initial_setup=True.
        node = _make_node(node_type, ocio_available=ocio_available)

        node.set_parameter_value("color_mode", COLOR_MODE_OCIO, initial_setup=True)

        assert node.get_parameter_value("color_mode") == COLOR_MODE_OCIO
        _assert_visibility(node, ocio_active=True)

    def test_restored_basic_value_shows_basic_controls(self, node_type: type, ocio_available: bool) -> None:
        node = _make_node(node_type, ocio_available=ocio_available, metadata={"_color_mode": COLOR_MODE_OCIO})

        node.set_parameter_value("color_mode", COLOR_MODE_BASIC, initial_setup=True)

        assert node.get_parameter_value("color_mode") == COLOR_MODE_BASIC
        _assert_visibility(node, ocio_active=False)

    def test_saved_ocio_metadata_shows_ocio_controls(self, node_type: type, ocio_available: bool) -> None:
        node = _make_node(node_type, ocio_available=ocio_available, metadata={"_color_mode": COLOR_MODE_OCIO})

        _assert_visibility(node, ocio_active=True)


def _tone_choices(node) -> list[str]:
    from griptape_nodes.traits.options import Options

    parameter = node.get_parameter_by_name("tone_mapping")
    return [trait.choices for trait in parameter.find_elements_by_type(Options)][0]


@pytest.mark.parametrize("node_type", _NODE_TYPES)
class TestToneMappingChoices:
    """Every node with basic mode offers the same display transforms."""

    def test_offers_every_choice_including_aces(self, node_type: type) -> None:
        node = _make_node(node_type, ocio_available=False)

        assert _tone_choices(node) == TONE_CHOICES
        assert {TONE_ACESCG_SRGB, TONE_ACESCG_REC709, TONE_ACES2065_SRGB} <= set(_tone_choices(node))

    @pytest.mark.parametrize("tone", TONE_CHOICES)
    def test_saved_choice_is_restored_unchanged(self, node_type: type, tone: str) -> None:
        # Saved values come back with initial_setup=True; existing names must keep loading as-is.
        node = _make_node(node_type, ocio_available=False)

        node.set_parameter_value("tone_mapping", tone, initial_setup=True)

        assert node.get_parameter_value("tone_mapping") == tone
