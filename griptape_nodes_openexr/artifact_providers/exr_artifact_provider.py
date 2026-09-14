"""EXR artifact provider: declares EXR as a supported artifact type."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING

from griptape_nodes.retained_mode.managers.artifact_providers.base_artifact_provider import (
    BaseArtifactMetadata,
    BaseArtifactProvider,
)

from griptape_nodes_openexr.exr.exr_io import scan_exr_header

if TYPE_CHECKING:
    from griptape_nodes.retained_mode.managers.artifact_providers.base_artifact_preview_generator import (
        BaseArtifactPreviewGenerator,
    )

logger = logging.getLogger("griptape_nodes")

_EXR_MAGIC = b"\x76\x2f\x31\x01"
_MAGIC_LEN = len(_EXR_MAGIC)


class EXRArtifactMetadata(BaseArtifactMetadata):
    """Metadata extracted from an EXR file header."""

    width: int
    height: int
    part_count: int
    channel_count: int
    file_size: int


class EXRArtifactProvider(BaseArtifactProvider):
    """Provider for OpenEXR artifacts."""

    @classmethod
    def get_friendly_name(cls) -> str:
        return "EXR"

    @classmethod
    def get_supported_formats(cls) -> set[str]:
        return {"exr"}

    @classmethod
    def get_preview_formats(cls) -> set[str]:
        return {"png", "webp"}

    @classmethod
    def get_default_preview_generator(cls) -> str:
        return "EXR Preview"

    @classmethod
    def get_default_preview_format(cls) -> str:
        return "webp"

    @classmethod
    def get_default_preview_generators(cls) -> list[type[BaseArtifactPreviewGenerator]]:
        from griptape_nodes_openexr.artifact_providers.exr_preview_generator import EXRPreviewGenerator

        return [EXRPreviewGenerator]

    @classmethod
    def detect_format(cls, data: bytes) -> str | None:
        if len(data) < _MAGIC_LEN:
            return None
        return "exr" if data[:_MAGIC_LEN] == _EXR_MAGIC else None

    @classmethod
    def get_artifact_metadata(cls, source_path: str) -> EXRArtifactMetadata | None:
        try:
            exr_data = scan_exr_header(source_path, header_only=True)
            # Devoid of further information take the first part in the file we find
            # and return its metadata.
            part = exr_data.parts[0]
            return EXRArtifactMetadata(
                width=part.width,
                height=part.height,
                part_count=len(exr_data.parts),
                channel_count=len(part.channels),
                file_size=Path(source_path).stat().st_size,
            )
        except Exception:
            logger.exception("Failed to extract EXR metadata from '%s'", source_path)
            return None
