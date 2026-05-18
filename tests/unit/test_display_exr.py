"""Unit tests for DisplayEXR node — no real EXR files required."""

from __future__ import annotations

from dataclasses import dataclass, field
from unittest.mock import MagicMock

import pytest

from griptape_nodes_openexr.exr.exr_header_artifact import EXRLayerArtifact, EXRPartHeaderArtifact
from griptape_nodes_openexr.exr.exr_types import EXRChannelInfo, EXRLayer, PixelType
from griptape_nodes_openexr.nodes.display_exr import DisplayEXR


# ---------------------------------------------------------------------------
# Minimal fixture helpers
# ---------------------------------------------------------------------------


def _make_channel(name: str, index: int) -> EXRChannelInfo:
    return EXRChannelInfo(
        name=name,
        pixel_type=PixelType.HALF,
        channel_index=index,
        x_sampling=1,
        y_sampling=1,
    )


def _make_layer(name: str, channel_names: list[str], start_index: int = 0) -> EXRLayer:
    channels = [_make_channel(f"{name}.{c}" if name else c, start_index + i) for i, c in enumerate(channel_names)]
    return EXRLayer(name=name, channels=channels)


def _make_part(layers: list[EXRLayer], width: int = 1920, height: int = 1080) -> EXRPartHeaderArtifact:
    header = MagicMock()
    header.chromaticities = None
    all_channels = [ch for layer in layers for ch in layer.channels]
    return EXRPartHeaderArtifact(
        file_path="/fake/test.exr",
        part_index=0,
        name="beauty",
        width=width,
        height=height,
        header=header,
        channels=all_channels,
        layers=layers,
    )


# ---------------------------------------------------------------------------
# Instantiation
# ---------------------------------------------------------------------------


def test_node_instantiation():
    node = DisplayEXR("test_display_exr")
    param_names = [p.name for p in node.parameters]
    assert "part" in param_names
    assert "tone_mapping" in param_names
    assert "exposure" in param_names
    assert "layer_name" in param_names
    assert "output" in param_names
    assert "part_name" in param_names
    assert "width" in param_names
    assert "height" in param_names
    assert "detected_colorspace" in param_names


def test_tone_mapping_has_options_trait():
    from griptape_nodes.traits.options import Options
    node = DisplayEXR("test_tm")
    tm_param = next(p for p in node.parameters if p.name == "tone_mapping")
    options_traits = tm_param.find_elements_by_type(Options)
    assert options_traits, "Expected an Options trait on tone_mapping"
    choices = options_traits[0].choices
    assert "simple" in choices
    assert "reinhard" in choices
    assert "filmic" in choices


def test_tone_mapping_default():
    node = DisplayEXR("test_tm_default")
    tm_param = next(p for p in node.parameters if p.name == "tone_mapping")
    assert tm_param.default_value == "simple"


def test_exposure_default():
    node = DisplayEXR("test_exp")
    exp_param = next(p for p in node.parameters if p.name == "exposure")
    assert exp_param.default_value == 0.0


# ---------------------------------------------------------------------------
# after_value_set — metadata
# ---------------------------------------------------------------------------


def test_after_value_set_updates_width_height():
    node = DisplayEXR("test_avs")
    part = _make_part([_make_layer("", ["R", "G", "B"])], width=3840, height=2160)

    # detect_colorspace returns "unknown" for missing files rather than raising
    node.after_value_set(node._part_param, part)

    assert node.parameter_output_values.get("width") == 3840
    assert node.parameter_output_values.get("height") == 2160


def test_after_value_set_sets_part_name():
    node = DisplayEXR("test_name")
    part = _make_part([_make_layer("", ["R"])], width=10, height=10)

    node.after_value_set(node._part_param, part)

    assert node.parameter_output_values.get("part_name") == "beauty"


def test_after_value_set_ignores_non_part():
    node = DisplayEXR("test_ignore")
    # Should not raise and should not touch parameter_output_values
    node.after_value_set(node._part_param, "not a part artifact")
    assert node.parameter_output_values.get("width", 0) == 0


# ---------------------------------------------------------------------------
# after_value_set — dynamic layers
# ---------------------------------------------------------------------------


def test_after_value_set_populates_layers():
    node = DisplayEXR("test_layers")
    layers = [
        _make_layer("beauty", ["R", "G", "B", "A"], start_index=0),
        _make_layer("depth", ["Z"], start_index=4),
    ]
    part = _make_part(layers)

    node.after_value_set(node._part_param, part)

    children = list(node._layers_group.children)
    assert len(children) == 2
    child_names = [c.name for c in children]
    assert "layer_beauty" in child_names
    assert "layer_depth" in child_names


def test_after_value_set_layer_values_are_artifacts():
    node = DisplayEXR("test_layer_values")
    layers = [_make_layer("beauty", ["R", "G", "B"], start_index=0)]
    part = _make_part(layers)

    node.after_value_set(node._part_param, part)

    value = node.parameter_output_values.get("layer_beauty")
    assert isinstance(value, EXRLayerArtifact)
    assert value.layer.name == "beauty"


def test_after_value_set_clears_layers_on_reconnect():
    node = DisplayEXR("test_clear")
    part_a = _make_part([_make_layer("a", ["R"]), _make_layer("b", ["G"])])
    part_b = _make_part([_make_layer("single", ["R", "G", "B"])])

    for part in (part_a, part_b):
        try:
            node.after_value_set(node._part_param, part)
        except Exception:
            pass

    # After connecting part_b (1 layer), the group should have 1 child, not 3
    children = list(node._layers_group.children)
    assert len(children) == 1


def test_after_value_set_default_layer_name():
    """Unnamed layer (empty string) gets the 'default' label in the parameter name."""
    node = DisplayEXR("test_default_label")
    layers = [_make_layer("", ["R", "G", "B"])]
    part = _make_part(layers)

    node.after_value_set(node._part_param, part)

    child_names = [c.name for c in node._layers_group.children]
    assert "layer_default" in child_names
