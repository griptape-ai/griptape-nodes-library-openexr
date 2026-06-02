"""Unit tests for write_exr_channels() helper and SaveEXR node."""

from __future__ import annotations

import asyncio
import io
from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np
import OpenEXR
import pytest
from PIL import Image

from griptape_nodes_openexr.exr.exr_header_artifact import EXRChannelArtifact
from griptape_nodes_openexr.exr.exr_io import load_exr_channels, write_exr_channels
from griptape_nodes_openexr.exr.exr_types import EXRChannelInfo, PixelType

DATA = Path(__file__).parents[1] / "data"
SINGLE_PART_RGBA = DATA / "single_part_rgba.exr"

H, W = 8, 12


def _ramp() -> np.ndarray:
    return np.linspace(0.0, 1.0, H * W, dtype=np.float32).reshape(H, W)


def _make_channel_artifact(channel_name: str) -> EXRChannelArtifact:
    return EXRChannelArtifact(
        file_path=str(SINGLE_PART_RGBA),
        part_index=0,
        channel=EXRChannelInfo(
            name=channel_name,
            pixel_type=PixelType.HALF,
            x_sampling=1,
            y_sampling=1,
        ),
    )


def _make_png_bytes(width: int = W, height: int = H) -> bytes:
    """Create a synthetic RGB PNG as bytes."""
    arr = np.zeros((height, width, 3), dtype=np.uint8)
    arr[..., 0] = 255  # red channel saturated
    buf = io.BytesIO()
    Image.fromarray(arr, "RGB").save(buf, format="PNG")
    return buf.getvalue()


def _make_image_artifact(width: int = W, height: int = H):
    """Return a minimal ImageArtifact-like object with PNG bytes."""
    from griptape.artifacts import ImageArtifact

    return ImageArtifact(_make_png_bytes(width, height), format="png", width=width, height=height)


def _was_successful(node) -> bool | None:
    return node.parameter_output_values.get("was_successful")


def _result_details(node) -> str:
    return node.parameter_output_values.get("result_details", "") or ""


# ---------------------------------------------------------------------------
# write_exr_channels() — low-level helper
# ---------------------------------------------------------------------------


class TestWriteExrChannelsHelper:
    def test_writes_file(self, tmp_path: Path) -> None:
        out = str(tmp_path / "out.exr")
        write_exr_channels(out, {"R": _ramp()})
        assert Path(out).exists()

    def test_channels_round_trip(self, tmp_path: Path) -> None:
        out = str(tmp_path / "out.exr")
        r = _ramp()
        g = _ramp() * 0.5
        write_exr_channels(out, {"R": r, "G": g})
        loaded = load_exr_channels(out, 0, ["R", "G"])
        assert set(loaded.keys()) == {"R", "G"}
        assert loaded["R"].shape == (H, W)

    def test_half_pixel_type_default(self, tmp_path: Path) -> None:
        out = str(tmp_path / "out.exr")
        write_exr_channels(out, {"R": _ramp()})
        with OpenEXR.File(out, separate_channels=True) as f:
            ch = f.parts[0].channels["R"]
            assert ch.pixels.dtype == np.float16

    def test_float_pixel_type(self, tmp_path: Path) -> None:
        out = str(tmp_path / "out.exr")
        write_exr_channels(out, {"R": _ramp()}, pixel_type="float")
        with OpenEXR.File(out, separate_channels=True) as f:
            ch = f.parts[0].channels["R"]
            assert ch.pixels.dtype == np.float32

    def test_non_contiguous_array_written_correctly(self, tmp_path: Path) -> None:
        """Non-contiguous slice of a (H, W, 3) array must not corrupt channel data."""
        out = str(tmp_path / "out.exr")
        base = np.zeros((H, W, 3), dtype=np.float32)
        base[..., 0] = 1.0  # R=1, G=0, B=0; arr[..., 0] is non-contiguous
        write_exr_channels(out, {"R": base[..., 0]}, pixel_type="float")
        loaded = load_exr_channels(out, 0, ["R"])
        np.testing.assert_allclose(loaded["R"], 1.0, atol=1e-6)

    def test_zip_compression(self, tmp_path: Path) -> None:
        out = str(tmp_path / "out.exr")
        write_exr_channels(out, {"R": _ramp()}, compression=OpenEXR.ZIP_COMPRESSION)
        assert Path(out).exists()

    def test_zips_compression(self, tmp_path: Path) -> None:
        out = str(tmp_path / "out.exr")
        write_exr_channels(out, {"R": _ramp()}, compression=OpenEXR.ZIPS_COMPRESSION)
        assert Path(out).exists()

    def test_piz_compression(self, tmp_path: Path) -> None:
        out = str(tmp_path / "out.exr")
        write_exr_channels(out, {"R": _ramp()}, compression=OpenEXR.PIZ_COMPRESSION)
        assert Path(out).exists()

    def test_dwaa_compression(self, tmp_path: Path) -> None:
        out = str(tmp_path / "out.exr")
        write_exr_channels(out, {"R": _ramp()}, compression=OpenEXR.DWAA_COMPRESSION)
        assert Path(out).exists()

    def test_no_compression(self, tmp_path: Path) -> None:
        out = str(tmp_path / "out.exr")
        write_exr_channels(out, {"R": _ramp()}, compression=OpenEXR.NO_COMPRESSION)
        assert Path(out).exists()

    def test_single_channel_values_preserved(self, tmp_path: Path) -> None:
        out = str(tmp_path / "out.exr")
        r = _ramp()
        write_exr_channels(out, {"R": r}, pixel_type="float")
        loaded = load_exr_channels(out, 0, ["R"])
        np.testing.assert_allclose(loaded["R"], r, atol=1e-6)

    def test_empty_path_raises(self) -> None:
        with pytest.raises((ValueError, RuntimeError)):
            write_exr_channels("", {"R": _ramp()})


# ---------------------------------------------------------------------------
# SaveEXR node — shared fixture for mocking ProjectFileParameter
# ---------------------------------------------------------------------------


@pytest.fixture()
def mock_project_file(tmp_path: Path):
    """Patch ProjectFileParameter and GriptapeNodes so SaveEXR runs without the engine."""
    from griptape_nodes.retained_mode.events.os_events import (
        DeleteFileRequest,
        ReadFileRequest,
        ReadFileResultSuccess,
        WriteFileRequest,
        WriteFileResultSuccess,
    )
    from griptape_nodes.retained_mode.griptape_nodes import GriptapeNodes

    output_path = tmp_path / "output.exr"

    dest = MagicMock()
    dest.resolve.return_value = str(output_path)

    instance = MagicMock()
    instance.build_file.return_value = dest

    def _handle_request(request):
        if isinstance(request, WriteFileRequest):
            assert isinstance(request.file_path, str) and isinstance(request.content, bytes)
            Path(request.file_path).write_bytes(request.content)
            return WriteFileResultSuccess(
                final_file_path=request.file_path, bytes_written=len(request.content), result_details=""
            )
        if isinstance(request, ReadFileRequest):
            assert request.file_path is not None
            data = Path(request.file_path).read_bytes()
            return ReadFileResultSuccess(
                content=data,
                file_size=len(data),
                mime_type="application/octet-stream",
                encoding=None,
                result_details="",
            )
        if isinstance(request, DeleteFileRequest):
            assert request.path is not None
            Path(request.path).unlink(missing_ok=True)
            return MagicMock()
        return MagicMock()

    with (
        patch("griptape_nodes_openexr.nodes.save_exr.ProjectFileParameter", return_value=instance),
        patch.object(GriptapeNodes, "handle_request", side_effect=_handle_request),
    ):
        yield output_path


def _make_node(mock_project_file):  # noqa: ANN001
    from griptape_nodes_openexr.nodes.save_exr import SaveEXR

    return SaveEXR("test_save_exr")


# ---------------------------------------------------------------------------
# SaveEXR node — Mode A (ImageArtifact input)
# ---------------------------------------------------------------------------


class TestSaveEXRModeA:
    def test_no_input_fails(self, mock_project_file: Path) -> None:
        node = _make_node(mock_project_file)
        asyncio.run(node.aprocess())
        assert _was_successful(node) is False

    def test_mode_a_succeeds(self, mock_project_file: Path) -> None:
        node = _make_node(mock_project_file)
        node.set_parameter_value("image_in", _make_image_artifact())
        asyncio.run(node.aprocess())
        assert _was_successful(node) is True, _result_details(node)

    def test_mode_a_writes_rgb_channels(self, mock_project_file: Path) -> None:
        node = _make_node(mock_project_file)
        node.set_parameter_value("image_in", _make_image_artifact())
        asyncio.run(node.aprocess())
        loaded = load_exr_channels(str(mock_project_file), 0, ["R", "G", "B"])
        assert set(loaded.keys()) == {"R", "G", "B"}

    def test_mode_a_normalizes_values(self, mock_project_file: Path) -> None:
        node = _make_node(mock_project_file)
        node.set_parameter_value("image_in", _make_image_artifact())
        asyncio.run(node.aprocess())
        loaded = load_exr_channels(str(mock_project_file), 0, ["R"])
        # _make_image_artifact has R=255 → should be ≈ 1.0 (within float16 tolerance)
        np.testing.assert_allclose(loaded["R"], 1.0, atol=0.01)

    def test_mode_a_output_part_artifact(self, mock_project_file: Path) -> None:
        from griptape_nodes_openexr.exr.exr_header_artifact import EXRPartArtifact

        node = _make_node(mock_project_file)
        node.set_parameter_value("image_in", _make_image_artifact())
        asyncio.run(node.aprocess())
        part = node.parameter_output_values.get("output_part")
        assert isinstance(part, EXRPartArtifact)
        assert part.part_index == 0
        assert {ch.name for ch in part.channels} == {"R", "G", "B"}

    def test_mode_a_output_dimensions(self, mock_project_file: Path) -> None:
        from griptape_nodes_openexr.exr.exr_header_artifact import EXRPartArtifact

        node = _make_node(mock_project_file)
        node.set_parameter_value("image_in", _make_image_artifact(width=W, height=H))
        asyncio.run(node.aprocess())
        part = node.parameter_output_values.get("output_part")
        assert isinstance(part, EXRPartArtifact)
        assert part.width == W
        assert part.height == H

    def test_mode_a_accepts_image_url_artifact(self, mock_project_file: Path, tmp_path: Path) -> None:
        from griptape.artifacts import ImageUrlArtifact

        png_path = tmp_path / "test.png"
        png_path.write_bytes(_make_png_bytes())

        node = _make_node(mock_project_file)
        node.set_parameter_value("image_in", ImageUrlArtifact(str(png_path)))
        asyncio.run(node.aprocess())
        assert _was_successful(node) is True, _result_details(node)
        loaded = load_exr_channels(str(mock_project_file), 0, ["R", "G", "B"])
        assert set(loaded.keys()) == {"R", "G", "B"}


# ---------------------------------------------------------------------------
# SaveEXR node — Mode B (EXRChannelArtifact inputs)
# ---------------------------------------------------------------------------


class TestSaveEXRModeB:
    def test_mode_b_single_channel_succeeds(self, mock_project_file: Path) -> None:
        node = _make_node(mock_project_file)
        node.set_parameter_value("channel_r", _make_channel_artifact("R"))
        asyncio.run(node.aprocess())
        assert _was_successful(node) is True, _result_details(node)

    def test_mode_b_writes_channel_named_r(self, mock_project_file: Path) -> None:
        node = _make_node(mock_project_file)
        node.set_parameter_value("channel_r", _make_channel_artifact("R"))
        asyncio.run(node.aprocess())
        loaded = load_exr_channels(str(mock_project_file), 0)
        assert "R" in loaded

    def test_mode_b_rgb_channels(self, mock_project_file: Path) -> None:
        node = _make_node(mock_project_file)
        node.set_parameter_value("channel_r", _make_channel_artifact("R"))
        node.set_parameter_value("channel_g", _make_channel_artifact("G"))
        node.set_parameter_value("channel_b", _make_channel_artifact("B"))
        asyncio.run(node.aprocess())
        loaded = load_exr_channels(str(mock_project_file), 0, ["R", "G", "B"])
        assert set(loaded.keys()) == {"R", "G", "B"}

    def test_mode_b_rgba_channels(self, mock_project_file: Path) -> None:
        node = _make_node(mock_project_file)
        node.set_parameter_value("channel_r", _make_channel_artifact("R"))
        node.set_parameter_value("channel_g", _make_channel_artifact("G"))
        node.set_parameter_value("channel_b", _make_channel_artifact("B"))
        node.set_parameter_value("channel_a", _make_channel_artifact("A"))
        asyncio.run(node.aprocess())
        loaded = load_exr_channels(str(mock_project_file), 0, ["R", "G", "B", "A"])
        assert set(loaded.keys()) == {"R", "G", "B", "A"}

    def test_mode_b_output_part_artifact(self, mock_project_file: Path) -> None:
        from griptape_nodes_openexr.exr.exr_header_artifact import EXRPartArtifact

        node = _make_node(mock_project_file)
        node.set_parameter_value("channel_r", _make_channel_artifact("R"))
        asyncio.run(node.aprocess())
        part = node.parameter_output_values.get("output_part")
        assert isinstance(part, EXRPartArtifact)

    def test_mode_b_output_part_file_path(self, mock_project_file: Path) -> None:
        from griptape_nodes_openexr.exr.exr_header_artifact import EXRPartArtifact

        node = _make_node(mock_project_file)
        node.set_parameter_value("channel_r", _make_channel_artifact("R"))
        asyncio.run(node.aprocess())
        part = node.parameter_output_values.get("output_part")
        assert isinstance(part, EXRPartArtifact)
        assert part.file_path == str(mock_project_file)


# ---------------------------------------------------------------------------
# SaveEXR node — mode priority and error cases
# ---------------------------------------------------------------------------


class TestSaveEXRModePriority:
    def test_channel_takes_priority_over_image(self, mock_project_file: Path) -> None:
        """Mode B takes priority when any channel slot is connected."""
        node = _make_node(mock_project_file)
        node.set_parameter_value("image_in", _make_image_artifact())
        node.set_parameter_value("channel_r", _make_channel_artifact("R"))
        asyncio.run(node.aprocess())
        assert _was_successful(node) is True, _result_details(node)
        # Mode B produces a single "R" channel, not the RGB triple of Mode A
        loaded = load_exr_channels(str(mock_project_file), 0)
        assert list(loaded.keys()) == ["R"]

    def test_no_input_fails_with_message(self, mock_project_file: Path) -> None:
        node = _make_node(mock_project_file)
        asyncio.run(node.aprocess())
        assert _was_successful(node) is False
        assert _result_details(node) != ""

    def test_no_input_output_part_is_none(self, mock_project_file: Path) -> None:
        node = _make_node(mock_project_file)
        asyncio.run(node.aprocess())
        assert node.parameter_output_values.get("output_part") is None


class TestSaveEXRShapeMismatch:
    def test_mismatched_channel_shapes_fails(self, mock_project_file: Path, tmp_path: Path) -> None:
        """channel_r and channel_g from different-sized images → failure."""
        # Create a small EXR to use as a mismatched source
        small_exr = str(tmp_path / "small.exr")
        write_exr_channels(small_exr, {"R": np.zeros((4, 4), dtype=np.float32)})

        node = _make_node(mock_project_file)
        # channel_r from single_part_rgba.exr (64×64), channel_g from small.exr (4×4)
        node.set_parameter_value("channel_r", _make_channel_artifact("R"))
        node.set_parameter_value(
            "channel_g",
            EXRChannelArtifact(
                file_path=small_exr,
                part_index=0,
                channel=EXRChannelInfo("R", PixelType.HALF, 1, 1),
            ),
        )
        asyncio.run(node.aprocess())
        assert _was_successful(node) is False


# ---------------------------------------------------------------------------
# SaveEXR node — compression and pixel_type parameters
# ---------------------------------------------------------------------------


class TestSaveEXRParameters:
    @pytest.mark.parametrize("compression", ["ZIP", "ZIPS", "PIZ", "DWAA", "NONE"])
    def test_compression_options_all_succeed(self, mock_project_file: Path, compression: str) -> None:
        node = _make_node(mock_project_file)
        node.set_parameter_value("image_in", _make_image_artifact())
        node.set_parameter_value("compression", compression)
        asyncio.run(node.aprocess())
        assert _was_successful(node) is True, _result_details(node)

    def test_pixel_type_half(self, mock_project_file: Path) -> None:
        node = _make_node(mock_project_file)
        node.set_parameter_value("image_in", _make_image_artifact())
        node.set_parameter_value("pixel_type", "HALF")
        asyncio.run(node.aprocess())
        with OpenEXR.File(str(mock_project_file), separate_channels=True) as f:
            assert f.parts[0].channels["R"].pixels.dtype == np.float16

    def test_pixel_type_float(self, mock_project_file: Path) -> None:
        node = _make_node(mock_project_file)
        node.set_parameter_value("image_in", _make_image_artifact())
        node.set_parameter_value("pixel_type", "FLOAT")
        asyncio.run(node.aprocess())
        with OpenEXR.File(str(mock_project_file), separate_channels=True) as f:
            assert f.parts[0].channels["R"].pixels.dtype == np.float32


# ---------------------------------------------------------------------------
# SaveEXR node — metadata parameters
# ---------------------------------------------------------------------------


class TestSaveEXRMetadata:
    def test_metadata_fields_stored_in_artifact(self, mock_project_file: Path) -> None:
        from griptape_nodes_openexr.exr.exr_header_artifact import EXRPartArtifact

        node = _make_node(mock_project_file)
        node.set_parameter_value("image_in", _make_image_artifact())
        node.set_parameter_value("metadata_part_name", "beauty")
        node.set_parameter_value("metadata_owner", "test-owner")
        node.set_parameter_value("metadata_comments", "test comment")
        node.set_parameter_value("metadata_software", "pytest")
        node.set_parameter_value("metadata_pixel_aspect_ratio", 2.0)
        asyncio.run(node.aprocess())

        assert _was_successful(node), _result_details(node)
        part = node.parameter_output_values.get("output_part")
        assert isinstance(part, EXRPartArtifact)
        assert part.name == "beauty"
        assert part.header.owner == "test-owner"
        assert part.header.comments == "test comment"
        assert part.header.software == "pytest"
        assert part.header.pixel_aspect_ratio == 2.0

    def test_metadata_custom_dict(self, mock_project_file: Path) -> None:
        from griptape_nodes_openexr.exr.exr_header_artifact import EXRPartArtifact

        node = _make_node(mock_project_file)
        node.set_parameter_value("image_in", _make_image_artifact())
        node.set_parameter_value("metadata_custom", {"shot": "001", "take": 3})
        asyncio.run(node.aprocess())

        assert _was_successful(node), _result_details(node)
        part = node.parameter_output_values.get("output_part")
        assert isinstance(part, EXRPartArtifact)
        assert part.header.custom == {"shot": "001", "take": 3}

    def test_metadata_custom_json_string(self, mock_project_file: Path) -> None:
        from griptape_nodes_openexr.exr.exr_header_artifact import EXRPartArtifact

        node = _make_node(mock_project_file)
        node.set_parameter_value("image_in", _make_image_artifact())
        node.set_parameter_value("metadata_custom", '{"key": "value"}')
        asyncio.run(node.aprocess())

        assert _was_successful(node), _result_details(node)
        part = node.parameter_output_values.get("output_part")
        assert isinstance(part, EXRPartArtifact)
        assert part.header.custom == {"key": "value"}

    def test_metadata_empty_strings_become_none(self, mock_project_file: Path) -> None:
        from griptape_nodes_openexr.exr.exr_header_artifact import EXRPartArtifact

        node = _make_node(mock_project_file)
        node.set_parameter_value("image_in", _make_image_artifact())
        asyncio.run(node.aprocess())

        part = node.parameter_output_values.get("output_part")
        assert isinstance(part, EXRPartArtifact)
        assert part.header.owner is None
        assert part.header.comments is None
        assert part.header.software is None

    def test_metadata_written_to_exr_file(self, mock_project_file: Path) -> None:
        """Metadata must be present in the actual EXR bytes, not just the artifact descriptor."""
        node = _make_node(mock_project_file)
        node.set_parameter_value("image_in", _make_image_artifact())
        node.set_parameter_value("metadata_owner", "file-owner")
        node.set_parameter_value("metadata_comments", "file-comment")
        node.set_parameter_value("metadata_software", "file-software")
        asyncio.run(node.aprocess())

        assert _was_successful(node), _result_details(node)
        with OpenEXR.File(str(mock_project_file)) as f:
            hdr = f.parts[0].header
            assert hdr.get("owner") == "file-owner"
            assert hdr.get("comments") == "file-comment"
            assert hdr.get("software") == "file-software"
