"""Unit tests for DisplayEXRPart.aprocess, run through the real render path on fixture files."""

from __future__ import annotations

import asyncio
import dataclasses
from pathlib import Path
from typing import Any, cast
from unittest.mock import MagicMock, patch

import numpy as np
import pytest
from griptape.artifacts import ImageUrlArtifact
from PIL import Image

from griptape_nodes_openexr.exr.exr_header_artifact import EXRPartArtifact
from griptape_nodes_openexr.exr.exr_io import scan_exr_header
from griptape_nodes_openexr.exr.ocio_helpers import COLOR_MODE_BASIC, COLOR_MODE_OCIO
from griptape_nodes_openexr.exr.tone_mapping import linear_to_srgb
from griptape_nodes_openexr.nodes.display_exr_part import DisplayEXRPart

DATA = Path(__file__).parents[1] / "data"
SINGLE_PART_RGBA = DATA / "single_part_rgba.exr"  # 64x64, R ramp, G ramp*0.5, B 0.2, A 1.0
MULTI_PART = DATA / "multi_part.exr"  # parts: rgba, depth (Z only), normal

_OCIO_MODULE = "griptape_nodes_openexr.exr.ocio_helpers"


def _part(path: Path, part_index: int = 0) -> EXRPartArtifact:
    part = scan_exr_header(str(path)).parts[part_index]
    return EXRPartArtifact(
        file_path=str(path),
        part_index=part_index,
        name=part.header.name or "",
        width=part.width,
        height=part.height,
        header=part.header,
        channels=part.channels,
    )


class _Destination:
    """Stands in for the project file destination, writing to a real path."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def write_bytes(self, data: bytes) -> Any:
        self.path.write_bytes(data)
        return MagicMock(location=str(self.path))


def _make_node(tmp_path: Path, part: EXRPartArtifact | None, **values: Any):
    with patch(f"{_OCIO_MODULE}.find_colorspace_transform_request_type", return_value=None):
        node = DisplayEXRPart("test_display_part")

    node._output_file = MagicMock()
    node._output_file.build_file.return_value = _Destination(tmp_path / "display_exr.png")
    node.set_parameter_value("color_mode", COLOR_MODE_BASIC)
    node.set_parameter_value("tone_mapping", "sRGB")
    if part is not None:
        node.set_parameter_value("part", part)
    for name, value in values.items():
        node.set_parameter_value(name, value)
    return node


def _output_file(node) -> MagicMock:
    return cast("MagicMock", node._output_file)


def _run(node) -> list[Any]:
    """Run aprocess and return the values it published to the image output."""
    with patch.object(node, "publish_update_to_parameter") as publish:
        asyncio.run(node.aprocess())
    return [c.args[1] for c in publish.call_args_list if c.args[0] == "image"]


def _image_output(node) -> ImageUrlArtifact | None:
    return node.parameter_output_values.get("image")


def _assert_failed(node, details: str) -> None:
    assert node.parameter_output_values.get("was_successful") is False
    assert details in node.parameter_output_values.get("result_details", "")
    assert _image_output(node) is None


def _srgb8(value: float) -> int:
    return round(float(linear_to_srgb(np.array([value], dtype=np.float32))[0]) * 255)


class TestSuccess:
    def test_renders_rgba_part_to_png_and_emits_artifact(self, tmp_path: Path) -> None:
        node = _make_node(tmp_path, _part(SINGLE_PART_RGBA))
        publish = _run(node)

        assert node.parameter_output_values.get("was_successful") is True, node.parameter_output_values.get(
            "result_details"
        )
        artifact = _image_output(node)
        assert isinstance(artifact, ImageUrlArtifact)
        assert artifact.value == str(tmp_path / "display_exr.png")
        assert publish == [artifact]

        with Image.open(artifact.value) as img:
            assert img.size == (64, 64)
            assert img.mode == "RGBA"
            pixels = np.asarray(img)

        assert np.all(pixels[..., 3] == 255)
        assert np.all(pixels[..., 2] == _srgb8(0.2))
        assert pixels[0, 0, 0] == 0
        assert pixels[-1, -1, 0] == 255

    def test_status_details_name_the_render(self, tmp_path: Path) -> None:
        node = _make_node(tmp_path, _part(SINGLE_PART_RGBA))
        _run(node)

        details = node.parameter_output_values.get("result_details", "")
        assert "64×64" in details
        assert "alpha: A" in details
        assert "EV=+0.0" in details

    def test_exposure_is_applied(self, tmp_path: Path) -> None:
        # +1 EV doubles B from 0.2 to 0.4 linear before encoding.
        node = _make_node(tmp_path, _part(SINGLE_PART_RGBA), exposure=1.0)
        _run(node)

        with Image.open(tmp_path / "display_exr.png") as img:
            pixels = np.asarray(img)
        assert np.all(pixels[..., 2] == _srgb8(0.4))

    def test_tone_mapping_is_applied(self, tmp_path: Path) -> None:
        # 'raw' writes linear 0.2 unencoded: 51, where sRGB gives 124.
        node = _make_node(tmp_path, _part(SINGLE_PART_RGBA), tone_mapping="raw")
        _run(node)

        with Image.open(tmp_path / "display_exr.png") as img:
            pixels = np.asarray(img)
        assert np.all(pixels[..., 2] == 51)

    @pytest.mark.parametrize("embed", [True, False])
    def test_embed_color_profile(self, tmp_path: Path, embed: bool) -> None:
        node = _make_node(tmp_path, _part(SINGLE_PART_RGBA), embed_color_profile=embed)
        _run(node)

        with Image.open(tmp_path / "display_exr.png") as img:
            assert ("icc_profile" in img.info) is embed

    def test_single_channel_part_renders_grayscale_without_alpha(self, tmp_path: Path) -> None:
        node = _make_node(tmp_path, _part(MULTI_PART, part_index=1))
        _run(node)

        assert node.parameter_output_values.get("was_successful") is True
        with Image.open(tmp_path / "display_exr.png") as img:
            assert img.mode == "RGB"
            pixels = np.asarray(img)
        np.testing.assert_array_equal(pixels[..., 0], pixels[..., 1])
        np.testing.assert_array_equal(pixels[..., 0], pixels[..., 2])
        assert "alpha" not in node.parameter_output_values.get("result_details", "")

    def test_records_source_path_for_the_viewer(self, tmp_path: Path) -> None:
        node = _make_node(tmp_path, _part(SINGLE_PART_RGBA))
        _run(node)
        assert node._current_exr_path == str(SINGLE_PART_RGBA)


class TestFailures:
    def test_no_part(self, tmp_path: Path) -> None:
        node = _make_node(tmp_path, None)
        publish = _run(node)

        _assert_failed(node, "No part provided")
        assert publish == []
        assert node._current_exr_path is None

    def test_no_channels(self, tmp_path: Path) -> None:
        part = dataclasses.replace(_part(SINGLE_PART_RGBA), channels=[])
        node = _make_node(tmp_path, part)
        publish = _run(node)

        _assert_failed(node, "No channels found in part")
        assert publish == []
        _output_file(node).build_file.assert_not_called()
        assert node._current_exr_path is None

    def test_ocio_mode_without_color_params(self, tmp_path: Path) -> None:
        node = _make_node(tmp_path, _part(SINGLE_PART_RGBA), color_mode=COLOR_MODE_OCIO)
        publish = _run(node)

        _assert_failed(node, "OCIO mode requires a connected color_params artifact.")
        assert publish == []

    def test_render_failure(self, tmp_path: Path) -> None:
        part = dataclasses.replace(_part(SINGLE_PART_RGBA), file_path=str(tmp_path / "missing.exr"))
        node = _make_node(tmp_path, part)
        publish = _run(node)

        _assert_failed(node, "Failed to load channels")
        assert publish == []
        _output_file(node).build_file.assert_not_called()

    @pytest.mark.parametrize("stage", ["build_file", "write_bytes"])
    def test_save_failure(self, tmp_path: Path, stage: str) -> None:
        node = _make_node(tmp_path, _part(SINGLE_PART_RGBA))
        if stage == "build_file":
            _output_file(node).build_file.side_effect = OSError("disk full")
        else:
            dest = MagicMock()
            dest.write_bytes.side_effect = OSError("disk full")
            _output_file(node).build_file.return_value = dest
        publish = _run(node)

        _assert_failed(node, "Failed to save image: disk full")
        assert publish == []
        assert node._current_exr_path is None

    def test_missing_part_clears_a_previous_render(self, tmp_path: Path) -> None:
        node = _make_node(tmp_path, _part(SINGLE_PART_RGBA))
        _run(node)
        assert _image_output(node) is not None

        node.set_parameter_value("part", None)
        _run(node)

        _assert_failed(node, "No part provided")
        assert node._current_exr_path is None

    def test_save_failure_clears_a_previous_render(self, tmp_path: Path) -> None:
        node = _make_node(tmp_path, _part(SINGLE_PART_RGBA))
        _run(node)
        assert _image_output(node) is not None

        _output_file(node).build_file.side_effect = OSError("disk full")
        _run(node)

        _assert_failed(node, "Failed to save image")
        assert node._current_exr_path is None
