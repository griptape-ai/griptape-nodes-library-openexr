"""Channel grouping strategy protocol."""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from griptape_nodes_openexr.exr.exr_types import ChannelNameParts, EXRChannelInfo, EXRLayer, EXRPart


@runtime_checkable
class ChannelGroupingStrategy(Protocol):
    """Protocol for pluggable EXR channel parsing and layer grouping.

    Implementations define how raw EXR channel names are parsed into
    layer/channel pairs and how channels are grouped into layers.
    """

    name: str
    display_name: str

    def parse_channel(self, full_name: str) -> ChannelNameParts:
        """Parse a full channel name into (layer_name, channel_name) parts."""
        ...

    def group_into_layers(self, channels: list[EXRChannelInfo]) -> list[EXRLayer]:
        """Group a flat channel list into named layers."""
        ...

    def postprocess_parts(self, parts: list[EXRPart]) -> None:
        """Apply any file-level post-processing (e.g. legacy multi-part detection).

        Called once after all parts have been scanned. Mutates parts in place.
        """
        ...
