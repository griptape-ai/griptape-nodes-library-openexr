"""Artist alignment policies against real EXR sources."""

import asyncio
from pathlib import Path
from typing import Any
from unittest.mock import patch

import numpy as np
import pytest
from griptape_nodes.retained_mode.events.config_events import GetConfigValueResultFailure, GetConfigValueResultSuccess
from griptape_nodes.retained_mode.griptape_nodes import GriptapeNodes

from griptape_nodes_openexr.exr.exr_header_artifact import EXRChannelArtifact
from griptape_nodes_openexr.nodes import display_exr_channel as channel_node
from tests.integration import test_read_exr as helpers
from tests.integration.test_read_exr import _part_artifact, _render_with_display_node, _write_windowed

engine = helpers.engine


def _channel(path: Path, name: str = "R") -> EXRChannelArtifact:
    channel = next(item for item in _part_artifact(path).channels if item.name == name)
    return EXRChannelArtifact(str(path), 0, channel)


@pytest.mark.parametrize("alpha", [False, True])
def test_shifted_channels_use_their_own_coordinates(engine, tmp_path: Path, alpha: bool) -> None:
    # Given equally sized arrays with different data-window origins.
    beauty = _write_windowed(tmp_path / "beauty.exr", (-2, 0, 5, 3), (0, 0, 3, 3))
    matte = _write_windowed(tmp_path / "matte.exr", (0, 0, 7, 3), (0, 0, 3, 3))
    other_slot = "channel_a" if alpha else "channel_g"
    # When coordinate-aware framing is chosen.
    image = _render_with_display_node(
        channel_node.DisplayEXRChannel,
        tmp_path,
        channel_r=_channel(beauty),
        **{other_slot: _channel(matte)},
        tone_mapping="raw",
        window_mode="Crop Overscan",
        channel_alignment="Match EXR Coordinates",
    )
    # Then the same EXR coordinate selects a different array column per source.
    np.testing.assert_array_equal(image[0, :, 0], [96, 128, 159, 191])
    np.testing.assert_array_equal(image[0, :, 3 if alpha else 1], [32, 64, 96, 128])


def test_legacy_defaults_keep_pixel_position_rendering(engine, tmp_path: Path) -> None:
    first = _write_windowed(tmp_path / "first.exr", (-2, 0, 5, 3), (0, 0, 3, 3))
    second = _write_windowed(tmp_path / "second.exr", (0, 0, 7, 3), (1, 0, 4, 3))
    image = _render_with_display_node(
        channel_node.DisplayEXRChannel,
        tmp_path,
        channel_r=_channel(first),
        channel_g=_channel(second),
        tone_mapping="raw",
    )
    assert image.shape == (4, 8, 3)
    np.testing.assert_array_equal(image[..., 0], image[..., 1])


def test_reference_alpha_selects_its_frame(engine, tmp_path: Path) -> None:
    first = _write_windowed(tmp_path / "first.exr", (0, 0, 7, 3), (0, 0, 3, 3))
    second = _write_windowed(tmp_path / "second.exr", (0, 0, 7, 3), (2, 0, 7, 3), alpha=1)
    image = _render_with_display_node(
        channel_node.DisplayEXRChannel,
        tmp_path,
        channel_r=_channel(first),
        channel_a=_channel(second, "A"),
        tone_mapping="raw",
        window_mode="Crop Overscan",
        channel_alignment="Match EXR Coordinates",
        frame_reference="A",
    )
    assert image.shape == (4, 6, 4)
    np.testing.assert_array_equal(image[0, :, 0], [96, 128, 159, 191, 223, 255])


@pytest.mark.parametrize("ocio", [False, True])
@pytest.mark.parametrize("background", [False, True])
def test_uncovered_coordinate_regions_stay_black_after_lifting_transform(
    engine, tmp_path: Path, background: bool, ocio: bool
) -> None:
    from PIL import Image

    from tests.integration.test_read_exr import _image_artifact

    first = _write_windowed(tmp_path / "first.exr", (0, 0, 1, 1), (0, 0, 5, 1), alpha=1)
    second = _write_windowed(tmp_path / "second.exr", (4, 0, 5, 1), (0, 0, 5, 1))
    values: dict[str, Any] = {
        "channel_r": _channel(first),
        "channel_g": _channel(second),
        "channel_a": _channel(first, "A"),
    }
    if ocio:
        from unittest.mock import MagicMock

        values.update(color_mode="ocio", color_params=MagicMock())
    if background:
        values["background"] = _image_artifact(Image.new("RGB", (6, 2), (0, 0, 255)))
    with patch.object(channel_node, "apply_color_management", side_effect=lambda rgb, *_: (rgb + 0.25, "lift")):
        image = _render_with_display_node(
            channel_node.DisplayEXRChannel,
            tmp_path,
            **values,
            channel_alignment="Match EXR Coordinates",
            window_mode="Crop Overscan",
        )
    if background:
        assert image[0, 2, 2] == 255
    else:
        assert not image[:, 2:4, :3].any()
        assert not image[:, 2:4, 3].any()


@pytest.mark.parametrize(
    ("parameter", "value", "message"),
    [
        ("channel_alignment", "typo", "Unknown channel_alignment"),
        ("frame_reference", "A", "A is not connected"),
        ("window_mode", "typo", "Unknown window_mode"),
    ],
)
def test_invalid_choices_fail_before_decode(engine, tmp_path: Path, parameter: str, value: str, message: str) -> None:
    with patch("griptape_nodes_openexr.exr.ocio_helpers.find_colorspace_transform_request_type", return_value=None):
        node = channel_node.DisplayEXRChannel("invalid")
    node.set_parameter_value("channel_r", _channel(_write_windowed(tmp_path / "input.exr", (0, 0, 3, 1), (0, 0, 3, 1))))
    node.set_parameter_value(parameter, value)
    with patch.object(channel_node, "load_slot_sources") as load:
        asyncio.run(node.aprocess())
    load.assert_not_called()
    assert node.parameter_output_values["was_successful"] is False
    assert message in node.parameter_output_values["result_details"]


@pytest.mark.parametrize("limit", [0, -1, True, "8", 1.5])
def test_invalid_settings_fail_before_decode(engine, tmp_path: Path, limit: object) -> None:
    result = GetConfigValueResultSuccess(value=limit, result_details="test override")
    assert isinstance(result, GetConfigValueResultSuccess)
    with patch("griptape_nodes_openexr.exr.ocio_helpers.find_colorspace_transform_request_type", return_value=None):
        node = channel_node.DisplayEXRChannel("invalid_setting")
    node.set_parameter_value("channel_r", _channel(_write_windowed(tmp_path / "input.exr", (0, 0, 3, 1), (0, 0, 3, 1))))
    with (
        patch.object(GriptapeNodes, "handle_request", return_value=result),
        patch.object(channel_node, "load_slot_sources") as load,
    ):
        asyncio.run(node.aprocess())
    load.assert_not_called()
    assert "positive integer" in node.parameter_output_values["result_details"]


def test_setting_is_read_each_execution(engine, tmp_path: Path) -> None:
    path = _write_windowed(tmp_path / "input.exr", (0, 0, 3, 1), (0, 0, 3, 1))
    with patch("griptape_nodes_openexr.exr.ocio_helpers.find_colorspace_transform_request_type", return_value=None):
        node = channel_node.DisplayEXRChannel("limit")
    node.set_parameter_value("channel_r", _channel(path))
    for limit in [7, 8, 7]:
        result = GetConfigValueResultSuccess(value=limit, result_details="test override")
        assert isinstance(result, GetConfigValueResultSuccess)
        with patch.object(GriptapeNodes, "handle_request", return_value=result), patch.object(node, "_output_file"):
            asyncio.run(node.aprocess())
        if limit == 7:
            assert "openexr.max_render_pixels" in node.parameter_output_values["result_details"]
        else:
            assert node.parameter_output_values["was_successful"] is True


def test_same_file_different_parts_align_without_a_second_open(engine, tmp_path: Path) -> None:
    import OpenEXR

    from griptape_nodes_openexr.exr import exr_io

    # Given two parts with different stored origins but one shared display window.
    display = (np.array([0, 0], np.int32), np.array([3, 1], np.int32))
    parts = []
    for index, origin in enumerate([-2, 0]):
        header = {
            "name": f"part{index}",
            "displayWindow": display,
            "dataWindow": (np.array([origin, 0], np.int32), np.array([origin + 7, 1], np.int32)),
        }
        plane = np.tile(np.arange(1, 9, dtype=np.float32) / 8, (2, 1))
        parts.append(OpenEXR.Part(header, {"Y": plane}))
    path = tmp_path / "parts.exr"
    OpenEXR.File(parts).write(str(path))
    metadata = exr_io.scan_exr_header(str(path)).parts
    slots = {
        "channel_r": EXRChannelArtifact(str(path), 0, metadata[0].channels[0]),
        "channel_g": EXRChannelArtifact(str(path), 1, metadata[1].channels[0]),
    }
    with patch.object(exr_io.OpenEXR, "File", wraps=OpenEXR.File) as opened:
        image = _render_with_display_node(
            channel_node.DisplayEXRChannel,
            tmp_path,
            **slots,
            channel_alignment="Match EXR Coordinates",
            window_mode="Crop Overscan",
            tone_mapping="raw",
        )
    assert opened.call_count == 1
    np.testing.assert_array_equal(image[0, :, 0], [96, 128, 159, 191])
    np.testing.assert_array_equal(image[0, :, 1], [32, 64, 96, 128])


@pytest.mark.parametrize("mode", ["Full", "Crop Overscan", "Show Overscan Boundary"])
@pytest.mark.parametrize("policy", ["Match Pixel Positions", "Match EXR Coordinates", "Require Matching Windows"])
def test_matching_sources_preserve_rendering(engine, tmp_path: Path, mode: str, policy: str) -> None:
    path = _write_windowed(tmp_path / "matching.exr", (-2, -1, 5, 3), (0, 0, 3, 1), alpha=0.5)
    slots = {f"channel_{slot.lower()}": _channel(path, slot) for slot in "RGBA"}
    expected = _render_with_display_node(channel_node.DisplayEXRChannel, tmp_path, **slots, window_mode=mode)
    actual = _render_with_display_node(
        channel_node.DisplayEXRChannel, tmp_path, **slots, window_mode=mode, channel_alignment=policy
    )
    np.testing.assert_array_equal(actual, expected)


@pytest.mark.parametrize("alpha", [False, True])
def test_alpha_only_extent_does_not_create_rgb_coverage(engine, tmp_path: Path, alpha: bool) -> None:
    beauty = _write_windowed(tmp_path / "beauty.exr", (-3, -1, -2, 0), (-3, -1, 2, 0))
    matte = _write_windowed(tmp_path / "matte.exr", (1, -1, 2, 0), (-3, -1, 2, 0), alpha=1)
    values = {"channel_r": _channel(beauty)}
    if alpha:
        values["channel_a"] = _channel(matte, "A")
    else:
        values["channel_g"] = _channel(matte)
    with patch.object(channel_node, "apply_color_management", side_effect=lambda rgb, *_: (rgb + 0.25, "lift")):
        image = _render_with_display_node(
            channel_node.DisplayEXRChannel, tmp_path, **values, channel_alignment="Match EXR Coordinates"
        )
    assert image.shape[:2] == (2, 6)
    assert not image[:, 2:4, :3].any()
    if alpha:
        assert not image[:, 4:, :3].any()
        assert (image[:, 4:, 3] == 255).all()


@pytest.mark.parametrize("ocio", [False, True])
def test_opaque_alpha_without_rgb_coverage_stays_black_over_background(engine, tmp_path: Path, ocio: bool) -> None:
    from unittest.mock import MagicMock

    from PIL import Image

    beauty = _write_windowed(tmp_path / "beauty.exr", (0, 0, 1, 1), (0, 0, 5, 1))
    matte = _write_windowed(tmp_path / "matte.exr", (4, 0, 5, 1), (0, 0, 5, 1), alpha=1)
    values: dict[str, Any] = {
        "channel_r": _channel(beauty),
        "channel_a": _channel(matte, "A"),
        "background": helpers._image_artifact(Image.new("RGB", (6, 2), (0, 0, 255))),
    }
    if ocio:
        values.update(color_mode="ocio", color_params=MagicMock())
    with patch.object(channel_node, "apply_color_management", side_effect=lambda rgb, *_: (rgb + 0.25, "lift")):
        image = _render_with_display_node(
            channel_node.DisplayEXRChannel,
            tmp_path,
            **values,
            channel_alignment="Match EXR Coordinates",
            window_mode="Crop Overscan",
        )
    assert not image[:, 4:].any()
    assert (image[:, 2:4, 2] == 255).all()


def test_disjoint_coordinate_output_still_validates_colour(engine, tmp_path: Path) -> None:
    path = _write_windowed(tmp_path / "disjoint.exr", (-10, 0, -9, 1), (0, 0, 3, 1))
    with patch("griptape_nodes_openexr.exr.ocio_helpers.find_colorspace_transform_request_type", return_value=None):
        node = channel_node.DisplayEXRChannel("disjoint")
    node.set_parameter_value("channel_r", _channel(path))
    node.set_parameter_value("channel_alignment", "Match EXR Coordinates")
    node.set_parameter_value("window_mode", "Crop Overscan")
    node.set_parameter_value("tone_mapping", "invalid")
    asyncio.run(node.aprocess())
    assert "Unsupported tone mapping" in node.parameter_output_values["result_details"]


@pytest.mark.parametrize("phase", ["prepare", "finish"])
def test_framing_failure_clears_node_image(engine, tmp_path: Path, phase: str) -> None:
    path = _write_windowed(tmp_path / "input.exr", (0, 0, 3, 1), (0, 0, 3, 1))
    with patch("griptape_nodes_openexr.exr.ocio_helpers.find_colorspace_transform_request_type", return_value=None):
        node = channel_node.DisplayEXRChannel("framing_failure")
    node.set_parameter_value("channel_r", _channel(path))
    node.parameter_output_values["image"] = "previous output"
    target = "_prepare_framing" if phase == "prepare" else "apply_window_mode"
    with patch.object(channel_node, target, side_effect=ValueError("framing failed")):
        asyncio.run(node.aprocess())
    assert node.parameter_output_values["was_successful"] is False
    assert node.parameter_output_values["image"] is None
    assert node.parameter_output_values["result_details"] == "framing failed"


@pytest.mark.parametrize("policy", ["Match EXR Coordinates", "Require Matching Windows"])
def test_real_subsampled_source_is_rejected(engine, tmp_path: Path, policy: str) -> None:
    import OpenEXR

    path = tmp_path / "sampled.exr"
    channel = OpenEXR.Channel("Y", np.ones((4, 4), np.float32), xSampling=2, ySampling=2)
    OpenEXR.File({"compression": OpenEXR.NO_COMPRESSION}, {"Y": channel}).write(str(path))
    with patch("griptape_nodes_openexr.exr.ocio_helpers.find_colorspace_transform_request_type", return_value=None):
        node = channel_node.DisplayEXRChannel("sampled")
    node.set_parameter_value("channel_r", _channel(path, "Y"))
    node.set_parameter_value("channel_alignment", policy)
    asyncio.run(node.aprocess())
    assert node.parameter_output_values["was_successful"] is False
    assert "R channel is subsampled" in node.parameter_output_values["result_details"]


def test_manifest_registers_documented_default() -> None:
    import json

    from griptape_nodes_openexr.exr.render_limits import MAX_RENDER_PIXELS_DEFAULT

    manifest = json.loads((Path(__file__).parents[2] / "griptape-nodes-library.json").read_text())
    settings = next(item for item in manifest["settings"] if item["category"] == "openexr")
    assert settings["contents"]["max_render_pixels"] == MAX_RENDER_PIXELS_DEFAULT == 8192 * 8192


def test_missing_setting_uses_default(engine, tmp_path: Path) -> None:
    result = GetConfigValueResultFailure(result_details="missing setting")
    assert isinstance(result, GetConfigValueResultFailure)
    path = _write_windowed(tmp_path / "input.exr", (0, 0, 3, 1), (0, 0, 3, 1))
    with patch.object(GriptapeNodes, "handle_request", return_value=result):
        image = _render_with_display_node(channel_node.DisplayEXRChannel, tmp_path, channel_r=_channel(path))
    assert image.shape == (2, 4, 3)
