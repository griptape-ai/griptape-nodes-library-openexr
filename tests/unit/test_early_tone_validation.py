"""Colour settings fail before execution-time source I/O."""

from __future__ import annotations

import asyncio
from contextlib import ExitStack
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from griptape_nodes_openexr.exr.ocio_helpers import COLOR_MODE_BASIC, COLOR_MODE_OCIO, read_color_settings
from griptape_nodes_openexr.nodes.display_exr_channel import DisplayEXRChannel
from griptape_nodes_openexr.nodes.display_exr_part import DisplayEXRPart
from griptape_nodes_openexr.nodes.exr_sequence_to_preview import EXRSequenceToPreview
from griptape_nodes_openexr.nodes.read_exr import ReadEXR


@pytest.mark.parametrize("tone", [None, "", "sRGB", "acescg to srgb"])
def test_execution_settings_preserve_defaults_and_aliases(tone: str | None) -> None:
    node = ReadEXR("settings")
    node.set_parameter_value("tone_mapping", tone, initial_setup=True)
    mode, canonical, artifact = read_color_settings(
        node, node._color_mode_param, node._tone_mapping_param, node._color_params_param
    )
    assert mode == COLOR_MODE_BASIC
    assert canonical == ("ACEScg to sRGB" if tone == "acescg to srgb" else "Linear sRGB to sRGB")
    assert artifact is None


def test_ocio_settings_require_an_artifact_before_validating_hidden_tone() -> None:
    node = ReadEXR("settings")
    node.set_parameter_value("color_mode", COLOR_MODE_OCIO)
    node.set_parameter_value("tone_mapping", "filmic", initial_setup=True)
    with pytest.raises(ValueError, match="OCIO mode requires a connected color_params artifact"):
        read_color_settings(node, node._color_mode_param, node._tone_mapping_param, node._color_params_param)


@pytest.mark.parametrize("node_type", [ReadEXR, DisplayEXRPart, DisplayEXRChannel, EXRSequenceToPreview])
@pytest.mark.parametrize("tone", ["filmic", "linear", "reinhard"])
@pytest.mark.parametrize("color_mode", [COLOR_MODE_BASIC, COLOR_MODE_OCIO])
def test_tone_validation_precedes_source_io(node_type: type, tone: str, color_mode: str) -> None:
    # Given a saved invalid tone and an I/O boundary that stops execution.
    with ExitStack() as stack:
        stack.enter_context(
            patch("griptape_nodes_openexr.exr.ocio_helpers.find_colorspace_transform_request_type", return_value=None)
        )
        node = node_type("early_validation")
        node.set_parameter_value("tone_mapping", tone, initial_setup=True)
        node.set_parameter_value("color_mode", color_mode)
        node.set_parameter_value("color_params", MagicMock())
        module = node_type.__module__
        if node_type is ReadEXR:
            source_io = stack.enter_context(
                patch.object(node, "_aresolve_scan", new_callable=AsyncMock, return_value=None)
            )
        elif node_type is EXRSequenceToPreview:
            source_io = stack.enter_context(
                patch(f"{module}.resolve_sequence_path", return_value="stopped at source I/O")
            )
        elif node_type is DisplayEXRPart:
            part = MagicMock()
            part.channels = [MagicMock() for _ in "RGB"]
            for channel, name in zip(part.channels, "RGB", strict=True):
                channel.name = name
            node.set_parameter_value("part", part)
            stack.enter_context(patch(f"{module}.WindowFraming.for_header", return_value=MagicMock()))
            source_io = stack.enter_context(
                patch(f"{module}.render_part_png", side_effect=RuntimeError("stopped at source I/O"))
            )
        else:
            channel = MagicMock()
            channel.file_path = "/tmp/validation.exr"
            node.set_parameter_value("channel_r", channel)
            stack.enter_context(patch(f"{module}.channel_alignment.resolve_reference"))
            stack.enter_context(patch(f"{module}.render_limits.get_render_limit", return_value=100))
            source_io = stack.enter_context(
                patch(f"{module}.load_slot_sources", side_effect=RuntimeError("stopped at source I/O"))
            )

        # When execution starts.
        asyncio.run(node.aprocess())

        # Then basic mode rejects the value before I/O; OCIO ignores the hidden tone.
        if color_mode == COLOR_MODE_BASIC:
            source_io.assert_not_called()
            assert node.parameter_output_values["was_successful"] is False
            expected = "Unsupported tone mapping" if tone == "reinhard" else "no longer supported"
            assert expected in str(node.parameter_output_values["result_details"])
        else:
            source_io.assert_called_once()
