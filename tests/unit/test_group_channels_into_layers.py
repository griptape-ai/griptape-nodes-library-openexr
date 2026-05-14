"""Unit tests for group_channels_into_layers()."""

from griptape_nodes_openexr.exr.exr_types import (
    EXRChannelInfo,
    PixelType,
    group_channels_into_layers,
)


def _ch(name: str, idx: int = 0) -> EXRChannelInfo:
    return EXRChannelInfo(name=name, pixel_type=PixelType.HALF, channel_index=idx, x_sampling=1, y_sampling=1)


def test_single_default_layer() -> None:
    channels = [_ch("R", 0), _ch("G", 1), _ch("B", 2), _ch("A", 3)]
    layers = group_channels_into_layers(channels)
    assert len(layers) == 1
    assert layers[0].name == ""
    assert len(layers[0].channels) == 4


def test_multiple_layers_sorted() -> None:
    channels = [_ch("beauty.R", 0), _ch("beauty.G", 1), _ch("diffuse.R", 2), _ch("R", 3)]
    layers = group_channels_into_layers(channels)
    # Default layer (empty name) first, then alphabetical
    assert layers[0].name == ""
    names = [layer.name for layer in layers]
    assert names == ["", "beauty", "diffuse"]


def test_default_layer_first_when_mixed() -> None:
    channels = [_ch("z_layer.R", 0), _ch("R", 1), _ch("a_layer.G", 2)]
    layers = group_channels_into_layers(channels)
    assert layers[0].name == ""
    assert layers[1].name == "a_layer"
    assert layers[2].name == "z_layer"


def test_single_channel_per_layer() -> None:
    channels = [_ch("depth.Z", 0)]
    layers = group_channels_into_layers(channels)
    assert len(layers) == 1
    assert layers[0].name == "depth"
    assert layers[0].channels[0].name == "depth.Z"


def test_channels_preserved_within_layer() -> None:
    channels = [_ch("beauty.R", 0), _ch("beauty.G", 1), _ch("beauty.B", 2), _ch("beauty.A", 3)]
    layers = group_channels_into_layers(channels)
    assert len(layers) == 1
    assert len(layers[0].channels) == 4
