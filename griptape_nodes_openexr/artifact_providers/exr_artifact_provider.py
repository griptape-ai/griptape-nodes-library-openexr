"""EXR artifact provider for the ArtifactManager."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from griptape_nodes.retained_mode.managers.artifact_providers.base_artifact_provider import (
    BaseArtifactProvider,
)

# BaseArtifactMetadata was added in a newer engine version; import conditionally.
try:
    from griptape_nodes.retained_mode.managers.artifact_providers.base_artifact_provider import (
        BaseArtifactMetadata,
    )

    _MetadataBase = BaseArtifactMetadata
except ImportError:
    from pydantic import BaseModel

    _MetadataBase = BaseModel  # type: ignore[assignment,misc]

if TYPE_CHECKING:
    from griptape_nodes.retained_mode.managers.artifact_providers.base_artifact_preview_generator import (
        BaseArtifactPreviewGenerator,
    )
    from griptape_nodes.retained_mode.managers.artifact_providers.provider_registry import ProviderRegistry

logger = logging.getLogger("griptape_nodes")


class EXRArtifactMetadata(_MetadataBase):
    """Metadata extracted from an EXR file header."""

    width: int
    height: int
    parts: int
    channels: int
    compression: str
    detected_colorspace: str


class EXRArtifactProvider(BaseArtifactProvider):
    """Artifact provider for OpenEXR files.

    Registers EXR (.exr) as a supported format and provides two preview
    generators: a tone-mapped RGB composite and a single-channel greyscale.
    """

    def __init__(self, registry: ProviderRegistry) -> None:
        super().__init__(registry)

    @classmethod
    def get_friendly_name(cls) -> str:
        return "EXR"

    @classmethod
    def get_supported_formats(cls) -> set[str]:
        return {"exr"}

    @classmethod
    def get_preview_formats(cls) -> set[str]:
        return {"png", "webp", "jpg"}

    @classmethod
    def get_default_preview_format(cls) -> str:
        return "png"

    @classmethod
    def get_default_preview_generator(cls) -> str:
        return "EXR Preview Generation"

    @classmethod
    def get_default_preview_generators(cls) -> list[type[BaseArtifactPreviewGenerator]]:
        from griptape_nodes_openexr.artifact_providers.exr_preview_generators import (
            EXRChannelPreviewGenerator,
            EXRPreviewGenerator,
        )

        return [EXRPreviewGenerator, EXRChannelPreviewGenerator]

    @classmethod
    def get_artifact_metadata(cls, source_path: str) -> EXRArtifactMetadata | None:
        """Extract EXR header metadata without loading pixels."""
        try:
            from griptape_nodes_openexr.exr.exr_io import scan_exr_header
            from griptape_nodes_openexr.exr.exr_pixel_io import detect_colorspace
            from griptape_nodes_openexr.exr.strategies.registry import get_strategy

            strategy = get_strategy("nuke")
            exr_data = scan_exr_header(source_path, strategy)
            part = exr_data.parts[0]

            colorspace = detect_colorspace(
                source_path,
                part_index=0,
                chromaticities=part.header.chromaticities,
            )

            return EXRArtifactMetadata(
                width=part.width,
                height=part.height,
                parts=len(exr_data.parts),
                channels=sum(len(p.channels) for p in exr_data.parts),
                compression=part.header.compression.value,
                detected_colorspace=colorspace,
            )
        except Exception:
            logger.debug("Failed to extract EXR metadata from '%s'", source_path, exc_info=True)
            return None
