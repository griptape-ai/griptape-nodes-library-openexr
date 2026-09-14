"""Unit tests for EXRPreviewGenerator and EXRPreviewParameters."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from griptape_nodes_openexr.artifact_providers.exr_preview_generator import (
    EXRPreviewGenerator,
    EXRPreviewParameters,
)


class TestEXRPreviewParameters:
    def test_defaults(self) -> None:
        params = EXRPreviewParameters()
        assert params.tone_mapping == "filmic"
        assert params.exposure == 0.0
        assert params.max_width == 1024
        assert params.max_height == 1024

    def test_custom_values(self) -> None:
        params = EXRPreviewParameters(tone_mapping="linear", exposure=2.0, max_width=512, max_height=256)
        assert params.tone_mapping == "linear"
        assert params.exposure == 2.0
        assert params.max_width == 512
        assert params.max_height == 256

    def test_exposure_at_min_boundary(self) -> None:
        params = EXRPreviewParameters(exposure=-10.0)
        assert params.exposure == -10.0

    def test_exposure_at_max_boundary(self) -> None:
        params = EXRPreviewParameters(exposure=10.0)
        assert params.exposure == 10.0

    def test_exposure_below_min_raises(self) -> None:
        with pytest.raises(ValidationError):
            EXRPreviewParameters(exposure=-10.1)

    def test_exposure_above_max_raises(self) -> None:
        with pytest.raises(ValidationError):
            EXRPreviewParameters(exposure=10.1)

    def test_max_width_zero_raises(self) -> None:
        with pytest.raises(ValidationError):
            EXRPreviewParameters(max_width=0)

    def test_max_height_zero_raises(self) -> None:
        with pytest.raises(ValidationError):
            EXRPreviewParameters(max_height=0)

    def test_max_width_above_ceiling_raises(self) -> None:
        with pytest.raises(ValidationError):
            EXRPreviewParameters(max_width=8193)

    def test_max_height_above_ceiling_raises(self) -> None:
        with pytest.raises(ValidationError):
            EXRPreviewParameters(max_height=8193)

    def test_model_validate_from_dict(self) -> None:
        params = EXRPreviewParameters.model_validate({"tone_mapping": "linear", "exposure": -1.0})
        assert params.tone_mapping == "linear"
        assert params.exposure == -1.0

    def test_extra_fields_ignored(self) -> None:
        params = EXRPreviewParameters.model_validate({"unknown_field": "ignored"})
        assert params.tone_mapping == "filmic"

    def test_editor_schema_type_present(self) -> None:
        assert EXRPreviewParameters.get_json_schema_type("tone_mapping") == "string"
        assert EXRPreviewParameters.get_json_schema_type("exposure") == "number"
        assert EXRPreviewParameters.get_json_schema_type("max_width") == "integer"
        assert EXRPreviewParameters.get_json_schema_type("max_height") == "integer"


class TestEXRPreviewGeneratorClassMethods:
    def test_friendly_name(self) -> None:
        assert EXRPreviewGenerator.get_friendly_name() == "EXR Preview"

    def test_supported_source_formats(self) -> None:
        assert EXRPreviewGenerator.get_supported_source_formats() == {"exr"}

    def test_supported_preview_formats(self) -> None:
        assert EXRPreviewGenerator.get_supported_preview_formats() == {"png", "webp"}

    def test_get_parameters_returns_correct_class(self) -> None:
        assert EXRPreviewGenerator.get_parameters() is EXRPreviewParameters
