"""Colour settings fail before execution-time source I/O."""

from __future__ import annotations

import asyncio
import sys
from unittest.mock import MagicMock, create_autospec

import pytest
from griptape_nodes.exe_types.node_types import BaseNode

from griptape_nodes_openexr.exr.colour.ocio_helpers import COLOR_MODE_BASIC, COLOR_MODE_OCIO, read_color_settings
from griptape_nodes_openexr.exr.exr_header_artifact import EXRChannelArtifact, EXRPartArtifact
from griptape_nodes_openexr.exr.exr_types import EXRChannelInfo
from griptape_nodes_openexr.nodes.display_exr_channel import DisplayEXRChannel
from griptape_nodes_openexr.nodes.display_exr_part import DisplayEXRPart
from griptape_nodes_openexr.nodes.exr_sequence_to_preview import EXRSequenceToPreview
from griptape_nodes_openexr.nodes.read_exr import ReadEXR

_NODE_TYPES = [ReadEXR, DisplayEXRPart, DisplayEXRChannel, EXRSequenceToPreview]


@pytest.mark.parametrize(
    ("tone", "expected"),
    [
        pytest.param(None, "Linear sRGB to sRGB", id="unset"),
        pytest.param("", "Linear sRGB to sRGB", id="empty"),
        pytest.param("sRGB", "Linear sRGB to sRGB", id="earlier-alias"),
        pytest.param("acescg to srgb", "ACEScg to sRGB", id="lowercase-choice"),
    ],
)
def test_execution_settings_preserve_defaults_and_aliases(tone: str | None, expected: str) -> None:
    node = ReadEXR("settings")
    node.set_parameter_value("tone_mapping", tone, initial_setup=True)
    mode, canonical, artifact = read_color_settings(
        node, node._color_mode_param, node._tone_mapping_param, node._color_params_param
    )
    assert mode == COLOR_MODE_BASIC
    assert canonical == expected
    assert artifact is None


def test_ocio_settings_require_an_artifact_before_validating_hidden_tone() -> None:
    node = ReadEXR("settings")
    node.set_parameter_value("color_mode", COLOR_MODE_OCIO)
    node.set_parameter_value("tone_mapping", "filmic", initial_setup=True)
    with pytest.raises(ValueError, match="OCIO mode requires a connected color_params artifact"):
        read_color_settings(node, node._color_mode_param, node._tone_mapping_param, node._color_params_param)


@pytest.mark.parametrize("node_type", _NODE_TYPES)
@pytest.mark.parametrize(
    ("tone", "expected"),
    [
        pytest.param("filmic", "no longer supported", id="filmic"),
        pytest.param("linear", "no longer supported", id="linear"),
        pytest.param("reinhard", "Unsupported tone mapping", id="reinhard"),
    ],
)
def test_basic_mode_rejects_invalid_tone_before_source_io(
    node_type: type, tone: str, expected: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given a saved invalid tone and an I/O boundary that stops execution.
    # When execution starts in basic mode.
    node, source_io = _run_with_saved_tone(node_type, tone, COLOR_MODE_BASIC, monkeypatch)

    # Then the value is rejected before any source I/O.
    source_io.assert_not_called()
    assert node.parameter_output_values["was_successful"] is False
    assert expected in str(node.parameter_output_values["result_details"])


@pytest.mark.parametrize("node_type", _NODE_TYPES)
@pytest.mark.parametrize(
    "tone",
    [
        pytest.param("filmic", id="retired"),
        pytest.param("reinhard", id="unknown"),
    ],
)
def test_ocio_mode_ignores_hidden_tone_and_reaches_source_io(
    node_type: type, tone: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given a saved invalid tone that OCIO mode hides, and an I/O boundary that stops execution.
    # validate_tone_mapping rejects retired and unknown names on separate branches, so one of each
    # is kept. A second retired name takes the same branch as the first.
    # When execution starts in OCIO mode.
    _, source_io = _run_with_saved_tone(node_type, tone, COLOR_MODE_OCIO, monkeypatch)

    # Then execution reaches source I/O.
    source_io.assert_called_once()


def _stub_read_exr_source_io(node: ReadEXR, monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    source_io = create_autospec(node._aresolve_scan, return_value=None)
    monkeypatch.setattr(node, "_aresolve_scan", source_io)
    return source_io


def _stub_sequence_source_io(node: EXRSequenceToPreview, monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    module = sys.modules[type(node).__module__]
    source_io = create_autospec(module.resolve_sequence_path, return_value="stopped at source I/O")
    monkeypatch.setattr(module, "resolve_sequence_path", source_io)
    return source_io


def _stub_part_source_io(node: DisplayEXRPart, monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    module = sys.modules[type(node).__module__]
    part = MagicMock(spec=list(EXRPartArtifact.__annotations__))
    part.channels = [MagicMock(spec=list(EXRChannelInfo.__annotations__)) for _ in "RGB"]
    for channel, name in zip(part.channels, "RGB", strict=True):
        channel.name = name
    node.set_parameter_value("part", part)
    monkeypatch.setattr(
        module.WindowFraming,
        "for_header",
        create_autospec(module.WindowFraming.for_header, return_value=MagicMock(spec=module.WindowFraming)),
    )
    source_io = create_autospec(module.render_part_png, side_effect=RuntimeError("stopped at source I/O"))
    monkeypatch.setattr(module, "render_part_png", source_io)
    return source_io


def _stub_channel_source_io(node: DisplayEXRChannel, monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    module = sys.modules[type(node).__module__]
    channel = MagicMock(spec=list(EXRChannelArtifact.__annotations__))
    channel.file_path = "/tmp/validation.exr"
    node.set_parameter_value("channel_r", channel)
    monkeypatch.setattr(
        module.channel_alignment, "resolve_reference", create_autospec(module.channel_alignment.resolve_reference)
    )
    monkeypatch.setattr(
        module.render_limits,
        "get_render_limit",
        create_autospec(module.render_limits.get_render_limit, return_value=100),
    )
    source_io = create_autospec(module.load_slot_sources, side_effect=RuntimeError("stopped at source I/O"))
    monkeypatch.setattr(module, "load_slot_sources", source_io)
    return source_io


_SOURCE_IO_STUBS = {
    ReadEXR: _stub_read_exr_source_io,
    EXRSequenceToPreview: _stub_sequence_source_io,
    DisplayEXRPart: _stub_part_source_io,
    DisplayEXRChannel: _stub_channel_source_io,
}


def _run_with_saved_tone(
    node_type: type, tone: str, color_mode: str, monkeypatch: pytest.MonkeyPatch
) -> tuple[BaseNode, MagicMock]:
    """Build a node holding a saved tone, stop its source I/O with a stub, and run it."""
    node = node_type("early_validation")
    node.set_parameter_value("tone_mapping", tone, initial_setup=True)
    node.set_parameter_value("color_mode", color_mode)
    node.set_parameter_value("color_params", MagicMock())
    source_io = _SOURCE_IO_STUBS[node_type](node, monkeypatch)
    asyncio.run(node.aprocess())
    return node, source_io
