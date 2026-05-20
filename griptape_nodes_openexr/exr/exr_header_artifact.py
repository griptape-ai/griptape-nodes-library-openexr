"""Metadata-only artifacts for EXR header data.

These are descriptors - they carry file path, header metadata, and channel/
layer structure but never load pixel data. Downstream nodes use them to
understand file structure and, when needed, initiate pixel loading via OIIO.

Intentionally independent of griptape-nodes-library-opencolorio so this
library can be installed without an OCIO dependency.
"""

from __future__ import annotations

from dataclasses import dataclass

from griptape_nodes_openexr.exr.exr_types import EXRChannelInfo, EXRHeader, EXRLayer


@dataclass
class EXRPartHeaderArtifact:
    """Descriptor for a single part within an EXR file.

    Self-contained: a downstream node with this artifact has everything
    it needs to understand the part's structure. Pixel loading requires a
    separate OIIO call using file_path and part_index.

    Attributes:
        file_path: Absolute path to the EXR file
        part_index: Zero-based part index within the file
        name: Part name (empty for single-part or unnamed parts)
        width: Image width in pixels
        height: Image height in pixels
        header: Full EXR header metadata
        channels: Channel metadata (no pixel data)
        layers: Layer groupings (strategy-dependent)
    """

    file_path: str
    name: str
    width: int
    height: int
    header: EXRHeader
    channels: list[EXRChannelInfo]
    layers: list[EXRLayer]

    def to_text(self) -> str:
        display_name = self.name or f"part {self.part_index}"
        return (
            f"EXR Part '{display_name}' from {self.file_path} "
            f"({len(self.layers)} layers, {len(self.channels)} channels, "
            f"{self.width}x{self.height})"
        )


@dataclass
class EXRHeaderArtifact:
    """Descriptor for an entire EXR file. Metadata only, no pixel data.

    Primary output of the ReadEXRHeader node. Downstream nodes use the
    embedded EXRPartHeaderArtifacts to access per-part structure, then
    initiate pixel loading themselves.

    Attributes:
        file_path: Absolute path to the EXR file
        parts: Per-part descriptors (one for single-part files)
    """

    file_path: str
    parts: list[EXRPartHeaderArtifact]

    def to_text(self) -> str:
        total_channels = sum(len(p.channels) for p in self.parts)
        total_layers = sum(len(p.layers) for p in self.parts)
        return f"EXR: {self.file_path} ({len(self.parts)} parts, {total_layers} layers, {total_channels} channels)"


@dataclass
class EXRLayerArtifact:
    """Descriptor for a single layer within an EXR part.

    Composes a part artifact (which file, which part) with a layer (which
    channels). Downstream pixel-loading nodes use part.file_path and
    part.part_index together with the channel indices.

    Attributes:
        part: The part this layer belongs to
        layer: Channel metadata for this specific layer
    """

    part: EXRPartHeaderArtifact
    layer: EXRLayer

    def to_text(self) -> str:
        display_name = self.layer.name or "default"
        return (
            f"EXR Layer '{display_name}' from {self.part.file_path} "
            f"(part {self.part.part_index}, {len(self.layer.channels)} channels, "
            f"{self.part.width}x{self.part.height})"
        )
