"""Integration tests for the ReadEXR node against real EXR fixtures.

Unit coverage lives in tests/unit/test_read_exr.py and stubs the EXR reader.
These exercise the real decode path, where the pixels the node renders are the
only way to tell that the right part and layer were read.
"""

from __future__ import annotations

import asyncio
import json
from io import BytesIO
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import numpy as np
import pytest
from griptape.artifacts import ImageArtifact, ImageUrlArtifact
from griptape_nodes.common.sequences import Sequence
from griptape_nodes.retained_mode.engine import Engine
from griptape_nodes.retained_mode.events.artifact_events import (
    GetPreviewForArtifactRequest,
    GetPreviewForArtifactResultFailure,
)
from griptape_nodes.retained_mode.events.config_events import GetConfigValueRequest, GetConfigValueResultFailure
from griptape_nodes.retained_mode.events.connection_events import (
    ListConnectionsForNodeResultSuccess,
)
from griptape_nodes.retained_mode.events.os_events import (
    ScanSequencesRequest,
    ScanSequencesResultSuccess,
)
from griptape_nodes.retained_mode.griptape_nodes import GriptapeNodes
from PIL import Image

from griptape_nodes_openexr.exr.channel_selection import select_display_channels
from griptape_nodes_openexr.exr.display_window import (
    WINDOW_MODE_DATA,
    WINDOW_MODE_DISPLAY,
    WINDOW_MODE_OVERLAY,
    WindowFraming,
    apply_window_mode,
    window_size,
)
from griptape_nodes_openexr.exr.exr_header_artifact import EXRChannelArtifact, EXRPartArtifact
from griptape_nodes_openexr.exr.exr_io import scan_exr_header, write_exr_channels
from griptape_nodes_openexr.exr.ocio_helpers import COLOR_MODE_BASIC, COLOR_MODE_OCIO
from griptape_nodes_openexr.exr.tone_mapping import (
    TONE_CHOICES,
    TONE_RAW,
    TONE_REC709,
    TONE_REC1886,
    TONE_SRGB,
)

DATA = Path(__file__).parents[1] / "data"
SINGLE_PART_RGBA = DATA / "single_part_rgba.exr"
SINGLE_PART_AOVS = DATA / "single_part_aovs.exr"
MULTI_PART = DATA / "multi_part.exr"

_NODE_MODULE = "griptape_nodes_openexr.nodes.read_exr"
# Decode and colour management live in the shared rendering helper, so that is
# where they are patched from.
_RENDER_MODULE = "griptape_nodes_openexr.exr.rendering"
_OCIO_MODULE = "griptape_nodes_openexr.exr.ocio_helpers"


def _sequence_dict(paths: list[Path], *, start: int) -> dict:
    """A Sequence-shaped dict pointing several frame slots at real fixtures on disk.

    There are no multi-frame EXR fixtures, so a sequence is faked by reusing the
    single-file fixtures across frame numbers. The node only ever opens
    `entry.path`, so this exercises the real decode per frame.
    """
    numbers = [start + i for i in range(len(paths))]
    entries = [{"number": n, "padded_number": f"{n:04d}", "path": str(p)} for n, p in zip(numbers, paths, strict=True)]
    return {
        "entries": entries,
        "first": numbers[0],
        "last": numbers[-1],
        "discovered_first": numbers[0],
        "discovered_last": numbers[-1],
        "padding": 4,
        "pattern": "frame.####.exr",
        "directory": str(paths[0].parent),
        "policy": "skip",
        "dropped_negative_number_count": 0,
        "present_numbers": numbers,
        "missing_numbers": [],
    }


class _Engine:
    """Answers the handful of requests ReadEXR makes, writing output to tmp_path."""

    def __init__(self, tmp_path: Path) -> None:
        self.tmp_path = tmp_path
        self.written: list[Path] = []

    def handle_request(self, request: Any) -> Any:
        if isinstance(request, GetConfigValueRequest):
            return GetConfigValueResultFailure(result_details="setting absent in test engine")
        if isinstance(request, ScanSequencesRequest):
            sequence = Sequence.model_validate(_sequence_dict([Path(request.path)], start=1))
            return ScanSequencesResultSuccess(
                sequences=[sequence], has_entries=True, directory_had_matching_files=True, result_details=""
            )
        if isinstance(request, GetPreviewForArtifactRequest):
            # The engine's preview machinery is the engine's test; here it only has
            # to degrade cleanly.
            return GetPreviewForArtifactResultFailure(result_details="no provider registered in tests")
        return ListConnectionsForNodeResultSuccess(incoming_connections=[], outgoing_connections=[], result_details="")

    def project_file_parameter(self, **_kwargs: Any) -> MagicMock:
        component = MagicMock()

        def _build_file(**extra_vars: Any) -> MagicMock:
            index = extra_vars.get("_index")
            suffix = f".{index}" if index is not None else ""
            path = self.tmp_path / f"read_exr{suffix}.png"
            destination = MagicMock()

            def _write_bytes(content: bytes) -> MagicMock:
                path.write_bytes(content)
                self.written.append(path)
                return MagicMock(location=str(path))

            destination.write_bytes.side_effect = _write_bytes
            return destination

        component.build_file.side_effect = _build_file
        return component


@pytest.fixture
def engine(tmp_path: Path):
    bench = _Engine(tmp_path)
    with (
        patch(f"{_NODE_MODULE}.ProjectFileParameter", side_effect=bench.project_file_parameter),
        patch.object(GriptapeNodes, "handle_request", side_effect=bench.handle_request),
        patch.object(
            Engine, "handle_request", autospec=True, side_effect=lambda _self, request: bench.handle_request(request)
        ),
    ):
        yield bench


def _make_node():
    from griptape_nodes_openexr.nodes.read_exr import ReadEXR

    with patch(f"{_OCIO_MODULE}.find_colorspace_transform_request_type", return_value=None):
        return ReadEXR("test_read_exr")


def _was_successful(node) -> bool | None:
    return node.parameter_output_values.get("was_successful")


def _result_details(node) -> str:
    return node.parameter_output_values.get("result_details", "") or ""


def _rendered(node) -> np.ndarray:
    artifact = node.parameter_output_values["image"]
    assert isinstance(artifact, ImageUrlArtifact)
    with Image.open(artifact.value) as image:
        return np.array(image)


async def _run(node) -> None:
    await node.aprocess()


class TestSinglePartFile:
    def test_one_node_yields_image_part_and_channels(self, engine: _Engine) -> None:
        import asyncio

        node = _make_node()
        node.set_parameter_value("path", str(SINGLE_PART_RGBA))
        asyncio.run(_run(node))

        assert _was_successful(node) is True, _result_details(node)
        assert isinstance(node.parameter_output_values["image"], ImageUrlArtifact)
        assert isinstance(node.parameter_output_values["part"], EXRPartArtifact)
        channels = node.parameter_output_values["channels"]
        assert {channel.channel.name for channel in channels} == {"R", "G", "B", "A"}
        assert all(isinstance(channel, EXRChannelArtifact) for channel in channels)

    def test_rendered_image_is_a_readable_png(self, engine: _Engine) -> None:
        import asyncio

        node = _make_node()
        node.set_parameter_value("path", str(SINGLE_PART_RGBA))
        asyncio.run(_run(node))

        pixels = _rendered(node)
        # single_part_rgba carries an alpha channel, so the render is RGBA.
        assert pixels.shape == (64, 64, 4)
        assert pixels.dtype == np.uint8

    def test_header_scalars_match_the_file(self, engine: _Engine) -> None:
        import asyncio

        expected = scan_exr_header(str(SINGLE_PART_RGBA))
        node = _make_node()
        node.set_parameter_value("path", str(SINGLE_PART_RGBA))
        asyncio.run(_run(node))

        assert node.parameter_output_values["image_width"] == expected.parts[0].width
        assert node.parameter_output_values["image_height"] == expected.parts[0].height
        assert node.parameter_output_values["part_count"] == len(expected.parts)
        assert node.parameter_output_values["channel_count"] == len(expected.parts[0].channels)


_METADATA_DEFAULTS = {
    "compression": "",
    "storage_type": "",
    "pixel_aspect_ratio": 1.0,
    "data_window": "",
    "display_window": "",
    "time_code": "",
    "software": "",
    "owner": "",
    "chromaticities": "",
    "custom_attributes": "{}",
}


def _metadata(node) -> dict[str, Any]:
    return {name: node.parameter_output_values[name] for name in _METADATA_DEFAULTS}


def _write_metadata_parts(path: Path, owners: list[str], windows: list[tuple[int, int, int, int]]) -> Path:
    """Write distinct selected-part metadata, sharing the multipart display window."""
    import OpenEXR

    parts = []
    for index, (owner, window) in enumerate(zip(owners, windows, strict=True)):
        xmin, ymin, xmax, ymax = window
        header = {
            "type": OpenEXR.scanlineimage,
            "compression": OpenEXR.ZIP_COMPRESSION,
            "owner": owner,
            "software": "metadata integration test",
            "pixelAspectRatio": 1.25,
            "timeCode": f"01:02:03:{index:02d}",
            "shot": f"shot-{owner}",
            "take": index + 1,
            "dataWindow": (np.array(window[:2], np.int32), np.array(window[2:], np.int32)),
            "displayWindow": (np.array([0, 0], np.int32), np.array([15, 11], np.int32)),
        }
        pixels = np.full((ymax - ymin + 1, xmax - xmin + 1), 0.5, np.float32)
        parts.append(OpenEXR.Part(header, dict.fromkeys("RGB", pixels), f"part-{index}"))
    with OpenEXR.File(parts) as exr:
        exr.write(str(path))
    return path


class TestHeaderMetadata:
    def test_populated_fixture_before_and_during_execution(self, engine: _Engine) -> None:
        node = _make_node()
        with patch(f"{_RENDER_MODULE}.load_exr_channels", side_effect=AssertionError("edit decoded pixels")):
            node.set_parameter_value("path", str(DATA / "custom_attributes.exr"))
            asyncio.run(node.wait_for_pending_scan())
        live = _metadata(node)
        assert {name: value for name, value in live.items() if name != "chromaticities"} == {
            "compression": "ZIP_COMPRESSION",
            "storage_type": "scanlineimage",
            "pixel_aspect_ratio": 1.0,
            "data_window": "0,0 - 63,63",
            "display_window": "0,0 - 63,63",
            "time_code": "",
            "software": "generate_fixtures.py v1.0",
            "owner": "VFX Studio",
            "custom_attributes": "{}",
        }
        assert json.loads(live["chromaticities"]) == pytest.approx(
            {
                "red_x": 0.64,
                "red_y": 0.33,
                "green_x": 0.30,
                "green_y": 0.60,
                "blue_x": 0.15,
                "blue_y": 0.06,
                "white_x": 0.3127,
                "white_y": 0.3290,
            }
        )

        asyncio.run(_run(node))
        assert _was_successful(node) is True, _result_details(node)
        assert _metadata(node) == live
        node.parameter_output_values.update(_METADATA_DEFAULTS)
        asyncio.run(_run(node))
        assert _was_successful(node) is True, _result_details(node)
        assert _metadata(node) == live  # Cached execution must also publish authoritative metadata.
        assert (node.parameter_output_values["image_width"], node.parameter_output_values["image_height"]) == (64, 64)
        assert (node.parameter_output_values["part_count"], node.parameter_output_values["channel_count"]) == (1, 4)

    def test_render_cache_hit_publishes_fresh_metadata(self, engine: _Engine, tmp_path: Path) -> None:
        import os

        window = (0, 0, 15, 11)
        path = _write_metadata_parts(tmp_path / "metadata.exr", ["before-owner"], [window])
        node = _make_node()
        node.set_parameter_value("path", str(path))
        asyncio.run(_run(node))
        assert _was_successful(node) is True, _result_details(node)
        assert node.parameter_output_values["owner"] == "before-owner"
        image = node.parameter_output_values["image"]
        writes = len(engine.written)
        stamp = path.stat()

        # Change only same-length metadata and preserve the render key's file stamp.
        # Execution must read fresh headers even when it can reuse the rendered pixels.
        _write_metadata_parts(path, ["after--owner"], [window])
        assert path.stat().st_size == stamp.st_size
        os.utime(path, ns=(stamp.st_atime_ns, stamp.st_mtime_ns))
        assert scan_exr_header(str(path)).parts[0].header.owner == "after--owner"

        with patch(f"{_RENDER_MODULE}.load_exr_channels", side_effect=AssertionError("cache hit decoded pixels")):
            asyncio.run(_run(node))

        assert _was_successful(node) is True, _result_details(node)
        assert "Reused cached render" in _result_details(node)
        assert len(engine.written) == writes
        assert node.parameter_output_values["image"] is image
        assert node.parameter_output_values["owner"] == "after--owner"
        assert json.loads(node.parameter_output_values["custom_attributes"]) == {"shot": "shot-after--owner", "take": 1}
        assert node.parameter_output_values["part"].header.owner == "after--owner"

    def test_source_transition_removes_absent_optional_attributes(self, engine: _Engine) -> None:
        node = _make_node()
        node.set_parameter_value("path", str(DATA / "custom_attributes.exr"))
        asyncio.run(node.wait_for_pending_scan())
        assert node.parameter_output_values["owner"] == "VFX Studio"
        node.set_parameter_value("path", str(DATA / "nuke_metadata.exr"))
        asyncio.run(node.wait_for_pending_scan())
        assert json.loads(node.parameter_output_values["custom_attributes"])["nuke/version"] == "15.0v1"
        node.set_parameter_value("path", str(SINGLE_PART_RGBA))
        asyncio.run(node.wait_for_pending_scan())
        for name in ("time_code", "software", "owner", "chromaticities", "custom_attributes"):
            assert node.parameter_output_values[name] == _METADATA_DEFAULTS[name]
        live = _metadata(node)
        asyncio.run(_run(node))
        assert _was_successful(node) is True, _result_details(node)
        assert _metadata(node) == live

        node.set_parameter_value("path", "")
        asyncio.run(node.wait_for_pending_scan())
        assert _metadata(node) == _METADATA_DEFAULTS

    def test_multipart_selection_updates_metadata_without_rendering(self, engine: _Engine, tmp_path: Path) -> None:
        path = _write_metadata_parts(
            tmp_path / "parts.exr",
            ["beauty-owner", "matte-owner"],
            [
                (-8, -4, 23, 15),
                (4, 2, 11, 7),
            ],
        )
        node = _make_node()
        node.set_parameter_value("path", str(path))
        asyncio.run(node.wait_for_pending_scan())
        assert node.parameter_output_values["owner"] == "beauty-owner"
        assert node.parameter_output_values["data_window"] == "-8,-4 - 23,15"
        with patch(f"{_RENDER_MODULE}.load_exr_channels", side_effect=AssertionError("edit decoded pixels")):
            node.set_parameter_value("part_name", "1: part-1")
            asyncio.run(node.wait_for_pending_scan())
        assert node.parameter_output_values["owner"] == "matte-owner"
        assert node.parameter_output_values["data_window"] == "4,2 - 11,7"
        assert node.parameter_output_values["display_window"] == "0,0 - 15,11"
        assert node.parameter_output_values["time_code"] == "01:02:03:01"
        assert node.parameter_output_values["pixel_aspect_ratio"] == 1.25
        assert json.loads(node.parameter_output_values["custom_attributes"]) == {"shot": "shot-matte-owner", "take": 2}
        live = _metadata(node)
        node.set_parameter_value("window_mode", WINDOW_MODE_DISPLAY)
        asyncio.run(_run(node))
        assert _was_successful(node) is True, _result_details(node)
        assert _metadata(node) == live
        assert _rendered(node).shape == (12, 16, 3)
        assert (node.parameter_output_values["image_width"], node.parameter_output_values["image_height"]) == (16, 12)
        assert (node.parameter_output_values["part_count"], node.parameter_output_values["channel_count"]) == (2, 3)
        assert (node.parameter_output_values["part"].width, node.parameter_output_values["part"].height) == (8, 6)

    def test_sequence_metadata_follows_selected_frame(self, engine: _Engine, tmp_path: Path) -> None:
        paths = [
            _write_metadata_parts(tmp_path / "frame.1001.exr", ["first-owner"], [(-8, -4, 23, 15)]),
            _write_metadata_parts(tmp_path / "frame.1002.exr", ["second-owner"], [(4, 2, 11, 7)]),
        ]
        node = _make_node()
        node.set_parameter_value("path", _sequence_dict(paths, start=1001))
        for frame, owner, window, size in (
            (1002, "second-owner", "4,2 - 11,7", (8, 6)),
            (1001, "first-owner", "-8,-4 - 23,15", (32, 20)),
        ):
            with patch(f"{_RENDER_MODULE}.load_exr_channels", side_effect=AssertionError("edit decoded pixels")):
                node.set_parameter_value("frame", frame)
                asyncio.run(node.wait_for_pending_scan())
            assert node.parameter_output_values["owner"] == owner
            assert node.parameter_output_values["data_window"] == window
            assert json.loads(node.parameter_output_values["custom_attributes"]) == {"shot": f"shot-{owner}", "take": 1}
            live = _metadata(node)
            asyncio.run(_run(node))
            assert _was_successful(node) is True, _result_details(node)
            assert _metadata(node) == live
            assert (node.parameter_output_values["image_width"], node.parameter_output_values["image_height"]) == size
            assert node.parameter_output_values["part"].frame_number == frame
            assert (node.parameter_output_values["part_count"], node.parameter_output_values["channel_count"]) == (1, 3)
        assert (node.parameter_output_values["first_frame"], node.parameter_output_values["last_frame"]) == (1001, 1002)
        assert node.parameter_output_values["frame_count"] == 2

    def test_event_loop_edits_refresh_metadata_in_background(self, engine: _Engine, tmp_path: Path) -> None:
        paths = [
            _write_metadata_parts(
                tmp_path / "frame.1001.exr",
                ["first-owner", "first-matte"],
                [
                    (-8, -4, 23, 15),
                    (4, 2, 11, 7),
                ],
            ),
            _write_metadata_parts(
                tmp_path / "frame.1002.exr",
                ["second-owner", "second-matte"],
                [
                    (0, 0, 15, 11),
                    (2, 1, 7, 4),
                ],
            ),
        ]
        node = _make_node()

        async def edit() -> None:
            node.set_parameter_value("path", _sequence_dict(paths, start=1001))
            await node.wait_for_pending_scan()
            assert node.parameter_output_values["owner"] == "first-owner"
            node.set_parameter_value("frame", 1002)
            await node.wait_for_pending_scan()
            assert node.parameter_output_values["owner"] == "second-owner"
            node.set_parameter_value("part_name", "1: part-1")
            await node.wait_for_pending_scan()
            assert node.parameter_output_values["owner"] == "second-matte"
            assert node.parameter_output_values["data_window"] == "2,1 - 7,4"
            node.set_parameter_value("path", str(SINGLE_PART_RGBA))
            await node.wait_for_pending_scan()
            assert node.parameter_output_values["owner"] == ""
            assert node.parameter_output_values["custom_attributes"] == "{}"

        with patch(f"{_RENDER_MODULE}.load_exr_channels", side_effect=AssertionError("edit decoded pixels")):
            asyncio.run(edit())

    def test_failed_execution_clears_live_metadata(self, engine: _Engine) -> None:
        node = _make_node()
        node.set_parameter_value("path", str(DATA / "custom_attributes.exr"))
        asyncio.run(_run(node))
        assert _was_successful(node) is True, _result_details(node)
        assert node.parameter_output_values["owner"] == "VFX Studio"
        node.set_parameter_value("color_mode", COLOR_MODE_OCIO)
        asyncio.run(_run(node))
        assert _was_successful(node) is False
        assert "color_params" in _result_details(node)
        assert _metadata(node) == _METADATA_DEFAULTS


class TestMultiPartFile:
    def test_part_dropdown_lists_the_real_part_names(self, engine: _Engine) -> None:
        node = _make_node()
        node.set_parameter_value("path", str(MULTI_PART))
        asyncio.run(node.wait_for_pending_scan())
        assert node._part_choices() == ["0: rgba", "1: depth", "2: normal"]

    def test_selecting_depth_renders_that_part(self, engine: _Engine) -> None:
        import asyncio

        node = _make_node()
        node.set_parameter_value("path", str(MULTI_PART))
        node.set_parameter_value("part_name", "1: depth")
        asyncio.run(_run(node))

        assert _was_successful(node) is True, _result_details(node)
        part = node.parameter_output_values["part"]
        assert part.name == "depth"
        assert part.part_index == 1
        # depth is a single Z channel, broadcast to grey, so no alpha plane.
        assert _rendered(node).shape == (64, 64, 3)

    def test_depth_and_rgba_render_differently(self, engine: _Engine) -> None:
        import asyncio

        node = _make_node()
        node.set_parameter_value("path", str(MULTI_PART))
        asyncio.run(_run(node))
        rgba = _rendered(node)[..., :3]

        node.set_parameter_value("part_name", "1: depth")
        asyncio.run(_run(node))
        depth = _rendered(node)

        assert not np.array_equal(rgba, depth)


class TestLayers:
    def test_layer_dropdown_lists_the_aov_prefixes(self, engine: _Engine) -> None:
        node = _make_node()
        node.set_parameter_value("path", str(SINGLE_PART_AOVS))
        asyncio.run(node.wait_for_pending_scan())
        # Order follows the channel order the file reports, which OpenEXR sorts.
        assert node._layer_choices() == ["beauty", "depth", "diffuse", "normal"]

    def test_default_layer_matches_select_display_channels(self, engine: _Engine) -> None:
        node = _make_node()
        node.set_parameter_value("path", str(SINGLE_PART_AOVS))
        asyncio.run(node.wait_for_pending_scan())

        channel_names = [channel.name for channel in scan_exr_header(str(SINGLE_PART_AOVS)).parts[0].channels]
        selected = select_display_channels(channel_names)
        assert selected is not None
        assert node.get_parameter_value("layer") == selected[0].rpartition(".")[0]

    def test_choosing_a_layer_changes_the_render(self, engine: _Engine) -> None:
        import asyncio

        node = _make_node()
        node.set_parameter_value("path", str(SINGLE_PART_AOVS))
        asyncio.run(_run(node))
        beauty = _rendered(node)[..., :3]

        node.set_parameter_value("layer", "diffuse")
        asyncio.run(_run(node))
        diffuse = _rendered(node)[..., :3]

        assert not np.array_equal(beauty, diffuse)


class TestEmbedColorProfile:
    """The profile must change the file's metadata and nothing else."""

    def _render_with(self, engine: _Engine, *, embed: bool) -> bytes:
        import asyncio

        node = _make_node()
        node.set_parameter_value("path", str(SINGLE_PART_RGBA))
        node.set_parameter_value("tone_mapping", TONE_REC1886)
        node.set_parameter_value("embed_color_profile", embed)
        asyncio.run(_run(node))
        artifact = node.parameter_output_values["image"]
        assert isinstance(artifact, ImageUrlArtifact)
        return Path(artifact.value).read_bytes()

    def test_pixels_are_identical_either_way(self, engine: _Engine) -> None:
        # The profile describes the pixels; embedding it must not alter them.
        untagged = np.array(Image.open(BytesIO(self._render_with(engine, embed=False))))
        tagged = np.array(Image.open(BytesIO(self._render_with(engine, embed=True))))
        np.testing.assert_array_equal(tagged, untagged)

    def test_profile_present_only_when_enabled(self, engine: _Engine) -> None:
        assert Image.open(BytesIO(self._render_with(engine, embed=False))).info.get("icc_profile") is None
        assert Image.open(BytesIO(self._render_with(engine, embed=True))).info.get("icc_profile") is not None

    def test_toggling_busts_the_render_cache(self, engine: _Engine) -> None:
        # Toggle the flag on one node instance, so a stale RenderKey would
        # actually be served from self._render rather than starting from an
        # empty cache every time, as two fresh nodes would.
        import asyncio

        node = _make_node()
        node.set_parameter_value("path", str(SINGLE_PART_RGBA))
        node.set_parameter_value("tone_mapping", TONE_REC1886)

        node.set_parameter_value("embed_color_profile", True)
        asyncio.run(_run(node))
        tagged_path = node.parameter_output_values["image"].value
        tagged_bytes = Path(tagged_path).read_bytes()

        node.set_parameter_value("embed_color_profile", False)
        asyncio.run(_run(node))
        untagged_path = node.parameter_output_values["image"].value
        untagged_bytes = Path(untagged_path).read_bytes()

        assert tagged_bytes != untagged_bytes
        assert Image.open(BytesIO(tagged_bytes)).info.get("icc_profile") is not None
        assert Image.open(BytesIO(untagged_bytes)).info.get("icc_profile") is None


class TestColorManagement:
    def test_exposure_brightens_the_render(self, engine: _Engine) -> None:
        import asyncio

        node = _make_node()
        node.set_parameter_value("path", str(SINGLE_PART_RGBA))
        node.set_parameter_value("tone_mapping", TONE_SRGB)
        asyncio.run(_run(node))
        base = _rendered(node)[..., :3].astype(np.int32)

        node.set_parameter_value("exposure", 2.0)
        asyncio.run(_run(node))
        brighter = _rendered(node)[..., :3].astype(np.int32)

        assert brighter.sum() > base.sum()

    def test_tone_mapping_changes_the_render(self, engine: _Engine) -> None:
        import asyncio

        node = _make_node()
        node.set_parameter_value("path", str(SINGLE_PART_RGBA))
        # Set both ends explicitly: sRGB is the default, so relying on it for
        # one side would compare a render against itself.
        node.set_parameter_value("tone_mapping", TONE_REC709)
        asyncio.run(_run(node))
        rec709 = _rendered(node)

        node.set_parameter_value("tone_mapping", TONE_SRGB)
        asyncio.run(_run(node))
        srgb = _rendered(node)

        assert not np.array_equal(rec709, srgb)

    def test_each_display_transform_renders_differently(self, engine: _Engine) -> None:
        """Every basic-mode choice must reach the render, not silently collapse."""
        import asyncio

        node = _make_node()
        node.set_parameter_value("path", str(SINGLE_PART_RGBA))

        renders = {}
        for tone in TONE_CHOICES:
            node.set_parameter_value("tone_mapping", tone)
            asyncio.run(_run(node))
            renders[tone] = _rendered(node)[..., :3].astype(np.int32)

        # rec709 is darker than sRGB, which is darker than rec1886.
        assert renders[TONE_REC709].sum() < renders[TONE_SRGB].sum()
        assert renders[TONE_SRGB].sum() < renders[TONE_REC1886].sum()
        # raw skips the transfer function entirely, so it is darkest of all.
        assert renders[TONE_RAW].sum() < renders[TONE_REC709].sum()

    def test_ocio_mode_without_color_params_fails_loudly(self, engine: _Engine) -> None:
        import asyncio

        node = _make_node()
        node.set_parameter_value("path", str(SINGLE_PART_RGBA))
        node.set_parameter_value("color_mode", COLOR_MODE_OCIO)
        asyncio.run(_run(node))

        assert _was_successful(node) is False
        assert "color_params" in _result_details(node)
        assert node.parameter_output_values["image"] is None

    def test_basic_mode_is_the_default_without_ocio(self, engine: _Engine) -> None:
        node = _make_node()
        assert node.get_parameter_value("color_mode") == COLOR_MODE_BASIC


class TestViewerButton:
    def test_fails_before_the_node_has_run(self, engine: _Engine) -> None:
        node = _make_node()
        result = node._on_open_viewer_click(MagicMock(), MagicMock())
        assert result is not None
        assert result.success is False

    def test_succeeds_after_a_run(self, engine: _Engine) -> None:
        import asyncio

        node = _make_node()
        node.set_parameter_value("path", str(SINGLE_PART_RGBA))
        asyncio.run(_run(node))

        with patch(f"{_NODE_MODULE}.handle_viewer_button_click", return_value=None) as launch:
            node._on_open_viewer_click(MagicMock(), MagicMock())
        launch.assert_called_once_with(node.name, str(SINGLE_PART_RGBA))


def _write_frames(tmp_path: Path, numbers: range) -> list[Path]:
    """Write a real EXR per frame, each a flat grey whose value encodes its frame.

    Same layout throughout. The per-frame grey level is what makes "the right
    frame was decoded" visible in the output pixels.
    """
    paths = []
    for offset, number in enumerate(numbers):
        value = np.full((8, 8), 0.1 * (offset + 1), dtype=np.float32)
        path = tmp_path / f"frame.{number:04d}.exr"
        write_exr_channels(str(path), {"R": value, "G": value, "B": value}, pixel_type="float")
        paths.append(path)
    return paths


class TestSequenceOverRealFiles:
    def test_named_frame_decodes_that_entry(self, engine: _Engine, tmp_path: Path) -> None:
        import asyncio

        paths = _write_frames(tmp_path, range(1001, 1004))
        node = _make_node()
        node.set_parameter_value("path", _sequence_dict(paths, start=1001))

        node.set_parameter_value("frame", 1001)
        asyncio.run(_run(node))
        assert _was_successful(node) is True, _result_details(node)
        first = _rendered(node)[..., :3].astype(np.int32)

        node.set_parameter_value("frame", 1003)
        asyncio.run(_run(node))
        assert _was_successful(node) is True, _result_details(node)
        third = _rendered(node)[..., :3].astype(np.int32)

        part = node.parameter_output_values["part"]
        assert part.frame_number == 1003
        assert part.file_path == str(paths[2])
        # Each frame is a brighter grey than the last.
        assert third.sum() > first.sum()

    def test_a_frame_with_its_own_data_window_is_read_at_its_own_size(self, engine: _Engine, tmp_path: Path) -> None:
        import asyncio

        # CG renders auto-crop the data window per frame, so frames of one sequence
        # differ in size. With alpha selected, rendering against frame 1's header
        # would reshape this frame's alpha to the wrong size.
        paths = []
        for number, (height, width) in zip((1001, 1002), ((8, 8), (4, 6)), strict=True):
            value = np.full((height, width), 0.5, dtype=np.float32)
            path = tmp_path / f"frame.{number:04d}.exr"
            write_exr_channels(str(path), dict.fromkeys("RGBA", value), pixel_type="float")
            paths.append(path)
        node = _make_node()
        node.set_parameter_value("path", _sequence_dict(paths, start=1001))
        node.set_parameter_value("frame", 1002)
        asyncio.run(_run(node))

        assert _was_successful(node) is True, _result_details(node)
        assert _rendered(node).shape == (4, 6, 4)
        assert node.parameter_output_values["image_width"] == 6
        assert node.parameter_output_values["image_height"] == 4
        part = node.parameter_output_values["part"]
        assert (part.width, part.height) == (6, 4)
        assert part.file_path == str(paths[1])

    def test_bounds_outputs_describe_the_sequence(self, engine: _Engine, tmp_path: Path) -> None:
        import asyncio

        paths = _write_frames(tmp_path, range(1001, 1004))
        node = _make_node()
        node.set_parameter_value("path", _sequence_dict(paths, start=1001))
        asyncio.run(_run(node))

        assert node.parameter_output_values["first_frame"] == 1001
        assert node.parameter_output_values["last_frame"] == 1003
        assert node.parameter_output_values["frame_count"] == 3

    def test_output_filename_carries_the_frame_number(self, engine: _Engine, tmp_path: Path) -> None:
        import asyncio

        paths = _write_frames(tmp_path, range(1001, 1004))
        node = _make_node()
        node.set_parameter_value("path", _sequence_dict(paths, start=1001))
        node.set_parameter_value("frame", 1002)
        asyncio.run(_run(node))

        assert engine.written[-1].name == "read_exr.1002.png"

    def test_a_frame_rewritten_in_place_is_decoded_again(self, engine: _Engine, tmp_path: Path) -> None:
        import asyncio
        import os
        import time

        paths = _write_frames(tmp_path, range(1001, 1004))
        node = _make_node()
        node.set_parameter_value("path", _sequence_dict(paths, start=1001))
        node.set_parameter_value("frame", 1002)
        asyncio.run(_run(node))
        assert _was_successful(node) is True, _result_details(node)
        before = _rendered(node)[..., :3].astype(np.int32).sum()

        # The same filename, re-rendered brighter, which is what an artist iterating on
        # a shot leaves behind.
        brighter = np.full((8, 8), 0.9, dtype=np.float32)
        write_exr_channels(str(paths[1]), {"R": brighter, "G": brighter, "B": brighter}, pixel_type="float")
        stamp = time.time_ns() + 1_000_000_000
        os.utime(paths[1], ns=(stamp, stamp))

        asyncio.run(_run(node))

        assert _was_successful(node) is True, _result_details(node)
        after = _rendered(node)[..., :3].astype(np.int32).sum()
        assert after > before, "served a cached render of the old pixels"


def _write_windowed(
    path: Path,
    data: tuple[int, int, int, int],
    display: tuple[int, int, int, int],
    *,
    alpha: float | None = None,
    half: bool = False,
    tiled: bool = False,
) -> Path:
    """Write an EXR with the given data and display windows, every pixel distinct."""
    import OpenEXR

    x0, y0, x1, y1 = data
    height, width = y1 - y0 + 1, x1 - x0 + 1
    ys, xs = np.mgrid[0:height, 0:width].astype(np.float32)
    channels = {"R": (xs + 1) / width, "G": (ys + 1) / height, "B": np.full((height, width), 0.25, np.float32)}
    if alpha is not None:
        channels["A"] = np.full((height, width), alpha, dtype=np.float32)
    header = {
        "compression": OpenEXR.NO_COMPRESSION,
        "type": OpenEXR.scanlineimage,
        "dataWindow": (np.array([x0, y0], dtype=np.int32), np.array([x1, y1], dtype=np.int32)),
        "displayWindow": (np.array(display[:2], dtype=np.int32), np.array(display[2:], dtype=np.int32)),
    }
    if half:
        channels = {name: plane.astype(np.float16) for name, plane in channels.items()}
    if tiled:
        header["type"] = OpenEXR.tiledimage
        tiles = OpenEXR.TileDescription()
        tiles.xSize = tiles.ySize = 8
        header["tiles"] = tiles
    OpenEXR.File(header, channels).write(str(path))
    return path


def _part_artifact(path: Path) -> EXRPartArtifact:
    part = scan_exr_header(str(path)).parts[0]
    return EXRPartArtifact(
        file_path=str(path),
        part_index=0,
        name=part.header.name,
        width=part.width,
        height=part.height,
        header=part.header,
        channels=part.channels,
    )


def _render_with_display_node(node_class: type, tmp_path: Path, **values: Any) -> np.ndarray:
    """Run Display EXR Part or Display EXR Channel and return its rendered pixels."""
    import asyncio

    with patch(f"{_OCIO_MODULE}.find_colorspace_transform_request_type", return_value=None):
        node = node_class("test_display")
    out = tmp_path / f"{node_class.__name__}.png"
    destination = MagicMock()
    destination.write_bytes.side_effect = lambda content: (out.write_bytes(content), MagicMock(location=str(out)))[1]
    node._output_file = MagicMock()
    node._output_file.build_file.return_value = destination
    node.set_parameter_value("color_mode", COLOR_MODE_BASIC)
    for name, value in values.items():
        node.set_parameter_value(name, value)
    with patch.object(node, "publish_update_to_parameter"):
        asyncio.run(node.aprocess())
    assert node.parameter_output_values.get("was_successful") is True, node.parameter_output_values.get(
        "result_details"
    )
    with Image.open(out) as image:
        return np.array(image)


def _image_artifact(image: Image.Image) -> ImageArtifact:
    buffer = BytesIO()
    image.save(buffer, format="PNG")
    return ImageArtifact(buffer.getvalue(), format="png", width=image.width, height=image.height)


_WINDOW_CASES = {
    "matching": ((0, 0, 15, 11), (0, 0, 15, 11)),
    "overscan": ((-8, -4, 23, 15), (0, 0, 15, 11)),
    "data-smaller-than-display": ((4, 2, 11, 7), (0, 0, 15, 11)),
    "partial-overlap": ((8, -4, 27, 7), (0, 0, 15, 11)),
    "negative-origins": ((-30, -20, -5, -3), (-24, -16, -9, -7)),
    "disjoint": ((-8, -4, -1, 3), (0, 4, 15, 15)),
    "shifted-equal-size": ((-8, -4, 7, 7), (0, 0, 15, 11)),
    "matching-negative-origin": ((-16, -12, -1, -1), (-16, -12, -1, -1)),
}


@pytest.mark.parametrize("alpha", [None, 0.75])
def test_channel_processing_uses_intersection(engine: _Engine, tmp_path: Path, alpha: float | None) -> None:
    from griptape_nodes_openexr.nodes import display_exr_channel as channel_node

    # Given real half-float tiled EXR channels with overscan.
    path = _write_windowed(tmp_path / "crop.exr", *_WINDOW_CASES["overscan"], alpha=alpha, half=True, tiled=True)
    part = _part_artifact(path)
    values = {f"channel_{channel.name.lower()}": EXRChannelArtifact(str(path), 0, channel) for channel in part.channels}
    with (
        patch.object(channel_node, "apply_exposure", wraps=channel_node.apply_exposure) as exposure,
        patch.object(channel_node, "apply_color_management", wraps=channel_node.apply_color_management) as colour,
    ):
        # When the channel node renders the display window without a background.
        pixels = _render_with_display_node(
            channel_node.DisplayEXRChannel, tmp_path, window_mode=WINDOW_MODE_DISPLAY, **values
        )
    # Then full-data planes never reach exposure or colour processing.
    assert exposure.call_args.args[0].shape == (12, 16, 3)
    assert colour.call_args.args[0].shape == (12, 16, 3)
    assert pixels.shape == (12, 16, 3 if alpha is None else 4)


class TestWindowModesAcrossNodes:
    """Read EXR, Display EXR Part, Display EXR Channel and EXR Sequence to Preview frame alike."""

    @pytest.mark.parametrize("mode", [WINDOW_MODE_DATA, WINDOW_MODE_DISPLAY, WINDOW_MODE_OVERLAY])
    @pytest.mark.parametrize("case", list(_WINDOW_CASES))
    @pytest.mark.parametrize(("half", "tiled"), [(False, False), (True, False), (True, True)])
    @pytest.mark.parametrize("alpha", [None, 0.75])
    def test_every_node_renders_the_same_framed_image(
        self, engine: _Engine, tmp_path: Path, case: str, mode: str, half: bool, tiled: bool, alpha: float | None
    ) -> None:
        import asyncio

        from griptape_nodes_openexr.nodes.display_exr_channel import DisplayEXRChannel
        from griptape_nodes_openexr.nodes.display_exr_part import DisplayEXRPart
        from griptape_nodes_openexr.nodes.exr_sequence_to_preview import _convert_frame

        data, display = _WINDOW_CASES[case]
        path = _write_windowed(tmp_path / "windowed.exr", data, display, half=half, tiled=tiled, alpha=alpha)
        part = _part_artifact(path)

        read = _make_node()
        read.set_parameter_value("path", str(path))
        read.set_parameter_value("window_mode", mode)
        asyncio.run(_run(read))
        assert _was_successful(read) is True, _result_details(read)
        read_pixels = _rendered(read)

        part_pixels = _render_with_display_node(DisplayEXRPart, tmp_path, part=part, window_mode=mode)
        channels = {channel.name: EXRChannelArtifact(str(path), 0, channel) for channel in part.channels}
        channel_pixels = _render_with_display_node(
            DisplayEXRChannel,
            tmp_path,
            channel_r=channels["R"],
            channel_g=channels["G"],
            channel_b=channels["B"],
            channel_a=channels.get("A"),
            window_mode=mode,
        )
        frame_png = _convert_frame(str(path), 0, tmp_path, 0, 0.0, None, TONE_SRGB, mode)
        with Image.open(frame_png) as image:
            frame_pixels = np.array(image)

        expected_size = window_size(WindowFraming.for_header(mode, part.header))
        assert read_pixels.shape == (expected_size[1], expected_size[0], 3 if alpha is None else 4)
        np.testing.assert_array_equal(part_pixels, read_pixels)
        np.testing.assert_array_equal(channel_pixels, read_pixels)
        # Sequence previews deliberately omit alpha.
        np.testing.assert_array_equal(frame_pixels, read_pixels[..., :3])
        if mode == WINDOW_MODE_DISPLAY:
            # Build the oracle from full-data pixels and coordinates independently of framing helpers.
            reference = _render_with_display_node(DisplayEXRPart, tmp_path, part=part, window_mode=WINDOW_MODE_DATA)
            expected = np.zeros_like(read_pixels)
            xmin, xmax = max(data[0], display[0]), min(data[2], display[2])
            ymin, ymax = max(data[1], display[1]), min(data[3], display[3])
            if xmin <= xmax and ymin <= ymax:
                expected[ymin - display[1] : ymax - display[1] + 1, xmin - display[0] : xmax - display[0] + 1] = (
                    reference[ymin - data[1] : ymax - data[1] + 1, xmin - data[0] : xmax - data[0] + 1]
                )
            np.testing.assert_array_equal(read_pixels, expected)
        assert (read.parameter_output_values["image_width"], read.parameter_output_values["image_height"]) == (
            expected_size
        )

    @pytest.mark.parametrize("mode", [WINDOW_MODE_DISPLAY, WINDOW_MODE_OVERLAY])
    def test_framing_matches_the_data_window_render(self, engine: _Engine, tmp_path: Path, mode: str) -> None:
        import asyncio

        path = _write_windowed(tmp_path / "overscan.exr", *_WINDOW_CASES["overscan"])
        node = _make_node()
        node.set_parameter_value("path", str(path))
        asyncio.run(_run(node))
        data_render = _rendered(node)

        node.set_parameter_value("window_mode", mode)
        asyncio.run(_run(node))

        assert _was_successful(node) is True, _result_details(node)
        framing = WindowFraming.for_header(mode, scan_exr_header(str(path)).parts[0].header)
        np.testing.assert_array_equal(_rendered(node), apply_window_mode(data_render, framing))

    def test_source_data_is_passed_on_unchanged(self, engine: _Engine, tmp_path: Path) -> None:
        import asyncio

        path = _write_windowed(tmp_path / "overscan.exr", *_WINDOW_CASES["overscan"])
        node = _make_node()
        node.set_parameter_value("path", str(path))
        node.set_parameter_value("window_mode", WINDOW_MODE_DISPLAY)
        asyncio.run(_run(node))

        part = node.parameter_output_values["part"]
        assert (part.width, part.height) == (32, 20)
        assert part.header.data_window == (-8, -4, 23, 15)
        assert part.header.display_window == (0, 0, 15, 11)

    def test_a_new_node_renders_the_data_window(self, engine: _Engine, tmp_path: Path) -> None:
        import asyncio

        path = _write_windowed(tmp_path / "overscan.exr", *_WINDOW_CASES["overscan"])
        node = _make_node()
        assert node.get_parameter_value("window_mode") == WINDOW_MODE_DATA
        node.set_parameter_value("path", str(path))
        asyncio.run(_run(node))

        assert _rendered(node).shape == (20, 32, 3)

    def test_display_window_padding_is_transparent_with_alpha(self, engine: _Engine, tmp_path: Path) -> None:
        from griptape_nodes_openexr.nodes.display_exr_part import DisplayEXRPart

        path = _write_windowed(tmp_path / "small.exr", *_WINDOW_CASES["data-smaller-than-display"], alpha=1.0)
        pixels = _render_with_display_node(
            DisplayEXRPart, tmp_path, part=_part_artifact(path), window_mode=WINDOW_MODE_DISPLAY
        )

        assert pixels.shape == (12, 16, 4)
        assert (pixels[2:8, 4:12, 3] == 255).all()  # noqa: PLR2004
        assert not pixels[0].any()
        assert not pixels[:, 0].any()

    def test_each_sequence_frame_is_framed_by_its_own_windows(self, engine: _Engine, tmp_path: Path) -> None:
        import asyncio

        from griptape_nodes_openexr.nodes.exr_sequence_to_preview import _convert_frame

        # One display window, with the data window auto-cropped differently per frame.
        display = (0, 0, 15, 11)
        paths = [
            _write_windowed(tmp_path / "frame.1001.exr", (-8, -4, 23, 15), display),
            _write_windowed(tmp_path / "frame.1002.exr", (4, 2, 11, 7), display),
        ]

        node = _make_node()
        node.set_parameter_value("path", _sequence_dict(paths, start=1001))
        node.set_parameter_value("window_mode", WINDOW_MODE_DISPLAY)
        sizes = []
        for frame in (1001, 1002):
            node.set_parameter_value("frame", frame)
            asyncio.run(_run(node))
            assert _was_successful(node) is True, _result_details(node)
            sizes.append(_rendered(node).shape)
        assert sizes == [(12, 16, 3), (12, 16, 3)]

        converted = []
        for i, path in enumerate(paths):
            with Image.open(
                _convert_frame(str(path), i, tmp_path, 0, 0.0, None, TONE_SRGB, WINDOW_MODE_DISPLAY)
            ) as image:
                converted.append(np.array(image))
        assert [frame.shape for frame in converted] == [(12, 16, 3), (12, 16, 3)]
        # The second frame only covers columns 4..11, rows 2..7 of the display window.
        assert not converted[1][0].any()
        assert converted[1][2:8, 4:12].any(axis=-1).all()

    def test_changing_the_mode_renders_again(self, engine: _Engine, tmp_path: Path) -> None:
        import asyncio

        path = _write_windowed(tmp_path / "overscan.exr", *_WINDOW_CASES["overscan"])
        node = _make_node()
        node.set_parameter_value("path", str(path))
        asyncio.run(_run(node))
        node.set_parameter_value("window_mode", WINDOW_MODE_DISPLAY)
        asyncio.run(_run(node))

        assert "Reused cached render" not in _result_details(node)
        assert _rendered(node).shape == (12, 16, 3)

    def test_a_background_is_fitted_to_the_display_window(self, engine: _Engine, tmp_path: Path) -> None:
        from griptape_nodes_openexr.nodes.display_exr_channel import DisplayEXRChannel

        # Fully transparent overscan pixels, so the output is the background alone.
        path = _write_windowed(tmp_path / "overscan.exr", *_WINDOW_CASES["overscan"], alpha=0.0)
        ys, xs = np.mgrid[0:12, 0:16]
        plate = np.stack([xs * 16, ys * 20, np.full_like(xs, 90)], axis=-1).astype(np.uint8)
        plate_artifact = _image_artifact(Image.fromarray(plate, "RGB"))

        channels = {
            channel.name: EXRChannelArtifact(str(path), 0, channel) for channel in _part_artifact(path).channels
        }
        pixels = _render_with_display_node(
            DisplayEXRChannel,
            tmp_path,
            channel_r=channels["R"],
            channel_g=channels["G"],
            channel_b=channels["B"],
            channel_a=channels["A"],
            background=plate_artifact,
            window_mode=WINDOW_MODE_DISPLAY,
        )

        # Linear-light round trip through sRGB can shift a code value by one.
        assert pixels.shape == plate.shape
        assert np.abs(pixels.astype(np.int16) - plate).max() <= 1

    def test_a_background_shows_through_frame_areas_with_no_pixels(self, engine: _Engine, tmp_path: Path) -> None:
        from griptape_nodes_openexr.nodes.display_exr_channel import DisplayEXRChannel

        path = _write_windowed(tmp_path / "small.exr", *_WINDOW_CASES["data-smaller-than-display"], alpha=1.0)
        plate_artifact = _image_artifact(Image.new("RGB", (16, 12), (255, 0, 0)))

        channels = {
            channel.name: EXRChannelArtifact(str(path), 0, channel) for channel in _part_artifact(path).channels
        }
        pixels = _render_with_display_node(
            DisplayEXRChannel,
            tmp_path,
            channel_r=channels["R"],
            channel_g=channels["G"],
            channel_b=channels["B"],
            channel_a=channels["A"],
            background=plate_artifact,
            window_mode=WINDOW_MODE_DISPLAY,
        )

        assert pixels.shape == (12, 16, 3)
        assert (pixels[0] == (255, 0, 0)).all()
        assert (pixels[2:8, 4:12, 1] > 0).all(), "the opaque data window should cover the plate"
