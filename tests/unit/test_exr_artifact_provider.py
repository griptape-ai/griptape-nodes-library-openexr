"""Unit tests for EXRArtifactProvider."""

from __future__ import annotations

from griptape_nodes_openexr.artifact_providers.exr_artifact_provider import (
    EXRArtifactMetadata,
    EXRArtifactProvider,
)
from griptape_nodes_openexr.artifact_providers.exr_preview_generator import EXRPreviewGenerator

_EXR_MAGIC = b"\x76\x2f\x31\x01"


class TestEXRArtifactProviderClassMethods:
    def test_friendly_name(self) -> None:
        assert EXRArtifactProvider.get_friendly_name() == "EXR"

    def test_supported_formats(self) -> None:
        assert EXRArtifactProvider.get_supported_formats() == {"exr"}

    def test_preview_formats(self) -> None:
        assert EXRArtifactProvider.get_preview_formats() == {"png", "webp"}

    def test_default_preview_generator(self) -> None:
        assert EXRArtifactProvider.get_default_preview_generator() == "EXR Preview"

    def test_default_preview_format(self) -> None:
        assert EXRArtifactProvider.get_default_preview_format() == "webp"

    def test_default_preview_generators_list(self) -> None:
        generators = EXRArtifactProvider.get_default_preview_generators()
        assert len(generators) == 1
        assert generators[0] is EXRPreviewGenerator


class TestEXRArtifactProviderDetectFormat:
    def test_detects_exr_magic(self) -> None:
        data = _EXR_MAGIC + b"\x00" * 100
        assert EXRArtifactProvider.detect_format(data) == "exr"

    def test_rejects_png_magic(self) -> None:
        data = b"\x89PNG\r\n\x1a\n" + b"\x00" * 100
        assert EXRArtifactProvider.detect_format(data) is None

    def test_rejects_jpeg_magic(self) -> None:
        data = b"\xff\xd8\xff\xe0" + b"\x00" * 100
        assert EXRArtifactProvider.detect_format(data) is None

    def test_rejects_too_short(self) -> None:
        assert EXRArtifactProvider.detect_format(b"\x76\x2f") is None

    def test_rejects_empty(self) -> None:
        assert EXRArtifactProvider.detect_format(b"") is None

    def test_rejects_wrong_first_byte(self) -> None:
        data = b"\x00\x2f\x31\x01" + b"\x00" * 100
        assert EXRArtifactProvider.detect_format(data) is None


class TestEXRArtifactMetadata:
    def test_fields(self) -> None:
        meta = EXRArtifactMetadata(
            width=1920,
            height=1080,
            part_count=1,
            channel_count=4,
            file_size=102400,
        )
        assert meta.width == 1920
        assert meta.height == 1080
        assert meta.part_count == 1
        assert meta.channel_count == 4
        assert meta.file_size == 102400
