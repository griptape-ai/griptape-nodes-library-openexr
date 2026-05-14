"""Raw EXR channel grouping strategy.

Presents channels exactly as stored in the file:
- No name sanitization
- No layer grouping — each channel is its own single-channel layer
- Display window coordinates preserved as-is (no origin normalisation)
- Multi-part legacy detection skipped
"""

from __future__ import annotations

from griptape_nodes_openexr.exr.exr_types import ChannelNameParts, EXRChannelInfo, EXRLayer, EXRPart


class RawEXRChannelGrouping:
    name = "raw"
    display_name = "Raw EXR"

    def parse_channel(self, full_name: str) -> ChannelNameParts:
        return ChannelNameParts(layer_name="", channel_name=full_name)

    def group_into_layers(self, channels: list[EXRChannelInfo]) -> list[EXRLayer]:
        # Each channel becomes its own layer for maximum transparency
        return [EXRLayer(name="", channels=[ch]) for ch in channels]

    def postprocess_parts(self, parts: list[EXRPart]) -> None:
        pass
