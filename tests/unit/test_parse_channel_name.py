"""Unit tests for parse_channel_name() — Nuke-compatible algorithm."""

import pytest

from griptape_nodes_openexr.exr.exr_types import ChannelNameParts, parse_channel_name


@pytest.mark.parametrize(
    ("full_name", "expected"),
    [
        # Bare channel names → default layer
        ("R", ChannelNameParts("", "R")),
        ("G", ChannelNameParts("", "G")),
        ("B", ChannelNameParts("", "B")),
        ("A", ChannelNameParts("", "A")),
        ("Z", ChannelNameParts("", "Z")),
        # Simple layer.channel
        ("beauty.R", ChannelNameParts("beauty", "R")),
        ("diffuse.G", ChannelNameParts("diffuse", "G")),
        ("depth.Z", ChannelNameParts("depth", "Z")),
        # Three-component: layer1.layer2.channel
        ("View Layer.AO.R", ChannelNameParts("View_Layer_AO", "R")),
        ("render.beauty.R", ChannelNameParts("render_beauty", "R")),
        # Leading digits stripped from each part
        ("1beauty.R", ChannelNameParts("beauty", "R")),
        ("beauty.1R", ChannelNameParts("beauty", "R")),
        ("12layer.34channel", ChannelNameParts("layer", "channel")),
        # Non-alphanumeric replaced with underscore
        ("beauty-final.R", ChannelNameParts("beauty_final", "R")),
        ("layer name.R", ChannelNameParts("layer_name", "R")),
        # Ci prefix → default layer (RenderMan convention)
        ("Ci.R", ChannelNameParts("", "R")),
        ("Ci.G", ChannelNameParts("", "G")),
        # Empty or all-digits (degenerate cases)
        ("", ChannelNameParts("", "unnamed")),
    ],
)
def test_parse_channel_name(full_name: str, expected: ChannelNameParts) -> None:
    assert parse_channel_name(full_name) == expected
