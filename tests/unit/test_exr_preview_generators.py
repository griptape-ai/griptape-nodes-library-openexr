"""Unit tests for EXR preview generator classes — no real EXR files required."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from griptape_nodes_openexr.artifact_providers.exr_preview_generators import (
    EXRChannelPreviewGenerator,
    EXRChannelPreviewParameters,
    EXRPreviewGenerator,
    EXRPreviewParameters,
)

# ---------------------------------------------------------------------------
# EXRPreviewGenerator class methods
# ---------------------------------------------------------------------------


def test_preview_generator_friendly_name():
    assert EXRPreviewGenerator.get_friendly_name() == "EXR Preview Generation"


def test_preview_generator_source_formats():
    assert EXRPreviewGenerator.get_supported_source_formats() == {"exr"}


def test_preview_generator_preview_formats():
    assert EXRPreviewGenerator.get_supported_preview_formats() == {"png", "jpg", "webp"}


def test_preview_generator_parameters_class():
    assert EXRPreviewGenerator.get_parameters() is EXRPreviewParameters


# ---------------------------------------------------------------------------
# EXRChannelPreviewGenerator class methods
# ---------------------------------------------------------------------------


def test_channel_generator_friendly_name():
    assert EXRChannelPreviewGenerator.get_friendly_name() == "EXR Channel Preview Generation"


def test_channel_generator_source_formats():
    assert EXRChannelPreviewGenerator.get_supported_source_formats() == {"exr"}


def test_channel_generator_preview_formats():
    assert EXRChannelPreviewGenerator.get_supported_preview_formats() == {"png", "jpg", "webp"}


def test_channel_generator_parameters_class():
    assert EXRChannelPreviewGenerator.get_parameters() is EXRChannelPreviewParameters


# ---------------------------------------------------------------------------
# EXRPreviewParameters validation
# ---------------------------------------------------------------------------


def test_preview_params_defaults():
    p = EXRPreviewParameters()
    assert p.part_index == 0
    assert p.layer_name == ""
    assert p.tone_mapping == "simple"
    assert p.exposure == 0.0
    assert p.max_width == 1024
    assert p.max_height == 1024


def test_preview_params_part_index_negative_rejected():
    with pytest.raises(ValidationError):
        EXRPreviewParameters(part_index=-1)


def test_preview_params_max_width_zero_rejected():
    with pytest.raises(ValidationError):
        EXRPreviewParameters(max_width=0)


def test_preview_params_max_width_over_limit_rejected():
    with pytest.raises(ValidationError):
        EXRPreviewParameters(max_width=9999)


def test_preview_params_round_trip():
    p = EXRPreviewParameters(part_index=2, layer_name="beauty", tone_mapping="filmic", exposure=1.5)
    assert EXRPreviewParameters.model_validate(p.model_dump()) == p


# ---------------------------------------------------------------------------
# EXRChannelPreviewParameters validation
# ---------------------------------------------------------------------------


def test_channel_params_defaults():
    p = EXRChannelPreviewParameters()
    assert p.part_index == 0
    assert p.layer_name == ""
    assert p.channel_name == "R"
    assert p.normalize is False
    assert p.exposure == 0.0
    assert p.gamma == pytest.approx(2.2)
    assert p.max_width == 1024
    assert p.max_height == 1024


def test_channel_params_gamma_zero_rejected():
    with pytest.raises(ValidationError):
        EXRChannelPreviewParameters(gamma=0.0)


def test_channel_params_gamma_negative_rejected():
    with pytest.raises(ValidationError):
        EXRChannelPreviewParameters(gamma=-1.0)


def test_channel_params_round_trip():
    p = EXRChannelPreviewParameters(channel_name="Z", normalize=True, gamma=1.0)
    assert EXRChannelPreviewParameters.model_validate(p.model_dump()) == p
