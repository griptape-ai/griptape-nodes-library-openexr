"""Unit tests for EXRArtifactProvider class methods — no instantiation required."""

from __future__ import annotations

from griptape_nodes_openexr.artifact_providers.exr_artifact_provider import EXRArtifactProvider
from griptape_nodes_openexr.artifact_providers.exr_preview_generators import (
    EXRChannelPreviewGenerator,
    EXRPreviewGenerator,
)


def test_friendly_name():
    assert EXRArtifactProvider.get_friendly_name() == "EXR"


def test_supported_formats():
    assert EXRArtifactProvider.get_supported_formats() == {"exr"}


def test_preview_formats():
    assert EXRArtifactProvider.get_preview_formats() == {"png", "webp", "jpg"}


def test_default_preview_format():
    assert EXRArtifactProvider.get_default_preview_format() == "png"


def test_default_preview_generator_name():
    assert EXRArtifactProvider.get_default_preview_generator() == "EXR Preview Generation"


def test_default_preview_generators_returns_both():
    generators = EXRArtifactProvider.get_default_preview_generators()
    assert EXRPreviewGenerator in generators
    assert EXRChannelPreviewGenerator in generators


def test_config_key_prefix():
    assert EXRArtifactProvider.get_config_key_prefix() == "artifacts.exr.preview_generation"


def test_preview_format_config_key():
    assert EXRArtifactProvider.get_preview_format_config_key() == "artifacts.exr.preview_generation.preview_format"


def test_preview_generator_config_key():
    assert (
        EXRArtifactProvider.get_preview_generator_config_key() == "artifacts.exr.preview_generation.preview_generator"
    )
