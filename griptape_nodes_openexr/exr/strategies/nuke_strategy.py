"""Nuke-compatible channel grouping strategy.

Replicates some of Nuke's channel -> layer mapping.
"""

from __future__ import annotations

from griptape_nodes_openexr.exr.exr_types import (
    ChannelNameParts,
    EXRChannelInfo,
    EXRLayer,
    EXRPart,
    _apply_legacy_part_name_prefix,
    _normalize_windows,
    group_channels_into_layers,
    parse_channel_name,
)


class NukeChannelGrouping:
    name = "nuke"
    display_name = "Nuke"

    def parse_channel(self, full_name: str) -> ChannelNameParts:
        return parse_channel_name(full_name)

    def group_into_layers(self, channels: list[EXRChannelInfo]) -> list[EXRLayer]:
        return group_channels_into_layers(channels)

    def postprocess_parts(self, parts: list[EXRPart]) -> None:
        if len(parts) > 1:
            _apply_legacy_part_name_prefix(parts)
        for part in parts:
            normalised = _normalize_windows(part.header.data_window, part.header.display_window)
            part.header.data_window = normalised.data
            part.header.display_window = normalised.display
            part.width = normalised.data.xmax - normalised.data.xmin + 1
            part.height = normalised.data.ymax - normalised.data.ymin + 1
