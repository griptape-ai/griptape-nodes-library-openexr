"""Metadata-only artifacts for EXR data.

These are descriptors - they carry file path, header metadata, and channel
structure but never load pixel data. Downstream nodes use them to understand
file structure and, when needed, initiate pixel loading.

Intentionally independent of griptape-nodes-library-opencolorio so this
library can be installed without an OCIO dependency.
"""

from __future__ import annotations

from dataclasses import dataclass

from griptape_nodes_openexr.exr.exr_types import EXRChannelInfo, EXRHeader


@dataclass
class EXRPartArtifact:
    """Descriptor for a single part within an EXR file.

    Self-contained: a downstream node with this artifact has everything
    it needs to understand the part's structure. Pixel loading requires a
    separate call using file_path and part_index.

    Attributes:
        file_path: Absolute path to the EXR file
        part_index: Zero-based part index within the file
        name: Part name (empty for single-part or unnamed parts)
        width: Image width in pixels
        height: Image height in pixels
        header: Full EXR header metadata
        channels: Channel metadata (no pixel data)
    """

    file_path: str
    part_index: int
    name: str
    width: int
    height: int
    header: EXRHeader
    channels: list[EXRChannelInfo]

    def to_text(self) -> str:
        display_name = self.name or f"part {self.part_index}"
        return (
            f"EXR Part '{display_name}' from {self.file_path} "
            f"({len(self.channels)} channels, "
            f"{self.width}x{self.height})"
        )


@dataclass
class EXRChannelArtifact:
    """Descriptor for a single raw EXR channel. Metadata only, no pixel data.

    Enables downstream nodes to inspect or load exactly one channel without
    carrying the full part context. Use file_path + part_index + channel.name
    to initiate pixel loading.

    Attributes:
        file_path: Absolute path to the EXR file
        part_index: Zero-based part index within the file
        channel: Channel metadata (name, pixel_type, x_sampling, y_sampling)
    """

    file_path: str
    part_index: int
    channel: EXRChannelInfo

    def to_text(self) -> str:
        return (
            f"EXR Channel '{self.channel.name}' from {self.file_path} "
            f"(part {self.part_index}, type={self.channel.pixel_type.value})"
        )
