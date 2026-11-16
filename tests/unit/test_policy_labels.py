"""Policy labels use title casing without branch-only compatibility aliases."""

from unittest.mock import patch

import pytest

from griptape_nodes_openexr.exr import channel_alignment, sequence_canvas
from griptape_nodes_openexr.nodes.display_exr_channel import DisplayEXRChannel
from griptape_nodes_openexr.nodes.exr_sequence_to_preview import EXRSequenceToPreview


@pytest.mark.parametrize(
    ("node_type", "parameter", "label"),
    [
        (DisplayEXRChannel, "channel_alignment", "Match Pixel Positions"),
        (DisplayEXRChannel, "channel_alignment", "Match EXR Coordinates"),
        (DisplayEXRChannel, "channel_alignment", "Require Matching Windows"),
        (DisplayEXRChannel, "frame_reference", "First Connected RGB"),
        (EXRSequenceToPreview, "odd_dimensions", "Pad to Even"),
        (EXRSequenceToPreview, "odd_dimensions", "Require Even"),
        (EXRSequenceToPreview, "frame_sizes", "Require Matching Sizes"),
        (EXRSequenceToPreview, "frame_sizes", "Pad to Largest"),
    ],
)
def test_parameters_offer_final_labels(node_type: type, parameter: str, label: str) -> None:
    with patch("griptape_nodes_openexr.exr.ocio_helpers.find_colorspace_transform_request_type", return_value=None):
        node = node_type("labels")
    node.set_parameter_value(parameter, label)
    assert node.get_parameter_value(parameter) == label
    options = node.get_parameter_by_name(parameter).ui_options["simple_dropdown"]
    assert label in options
    node.set_parameter_value(parameter, "typo")
    assert node.get_parameter_value(parameter) == "typo"


def test_previous_alignment_casing_is_rejected() -> None:
    with pytest.raises(ValueError, match="Unknown channel_alignment"):
        channel_alignment.resolve_reference({"R": object()}, "Match pixel positions", "First Connected RGB", "Full")


def test_previous_reference_casing_is_rejected() -> None:
    with pytest.raises(ValueError, match="Unknown frame_reference"):
        channel_alignment.resolve_reference({"R": object()}, "Match Pixel Positions", "First connected RGB", "Full")


@pytest.mark.parametrize(
    ("odd", "sizes"), [("Pad to even", "Require Matching Sizes"), ("Pad to Even", "Pad to largest")]
)
def test_previous_sequence_policy_casing_is_rejected(odd: str, sizes: str) -> None:
    with pytest.raises(ValueError, match="Unknown"):
        sequence_canvas.validate_policies(odd, sizes)


@pytest.mark.parametrize(
    ("module", "constant", "node_type", "parameter"),
    [
        (channel_alignment, "ALIGN_PIXELS", DisplayEXRChannel, "channel_alignment"),
        (channel_alignment, "ALIGN_COORDINATES", DisplayEXRChannel, "channel_alignment"),
        (channel_alignment, "ALIGN_STRICT", DisplayEXRChannel, "channel_alignment"),
        (channel_alignment, "REFERENCE_DEFAULT", DisplayEXRChannel, "frame_reference"),
        (sequence_canvas, "ODD_PAD", EXRSequenceToPreview, "odd_dimensions"),
        (sequence_canvas, "ODD_REQUIRE", EXRSequenceToPreview, "odd_dimensions"),
        (sequence_canvas, "SIZES_REQUIRE", EXRSequenceToPreview, "frame_sizes"),
        (sequence_canvas, "SIZES_PAD", EXRSequenceToPreview, "frame_sizes"),
    ],
)
def test_tooltips_use_option_constants(module, constant: str, node_type: type, parameter: str) -> None:
    with (
        patch.object(module, constant, "Updated option label"),
        patch("griptape_nodes_openexr.exr.ocio_helpers.find_colorspace_transform_request_type", return_value=None),
    ):
        node = node_type("tooltips")
    assert "Updated option label" in node.get_parameter_by_name(parameter).tooltip


def test_missing_reference_error_uses_option_constant() -> None:
    with patch.object(channel_alignment, "REFERENCE_DEFAULT", "Updated reference label"):
        with pytest.raises(ValueError, match="choose Updated reference label"):
            channel_alignment.resolve_reference({"R": object()}, "Match Pixel Positions", "A", "Full")


def test_offered_labels_use_title_casing() -> None:
    assert channel_alignment.ALIGN_CHOICES == [
        "Match Pixel Positions",
        "Match EXR Coordinates",
        "Require Matching Windows",
    ]
    assert channel_alignment.REFERENCE_DEFAULT == "First Connected RGB"
    assert sequence_canvas.ODD_CHOICES == ["Pad to Even", "Require Even"]
    assert sequence_canvas.SIZES_CHOICES == ["Require Matching Sizes", "Pad to Largest"]
