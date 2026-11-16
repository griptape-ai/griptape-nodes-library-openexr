"""Metadata-aware slot decoding uses the pixel open for window geometry."""

from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np
import OpenEXR
import pytest

from griptape_nodes_openexr.exr import exr_io

DATA = Path(__file__).parents[1] / "data"


def _slot(name: str, file: Path, part: int = 0) -> MagicMock:
    artifact = MagicMock()
    artifact.file_path = str(file)
    artifact.part_index = part
    artifact.channel.name = name
    return artifact


def test_multipart_sources_share_one_decode() -> None:
    # Given repeated channels and different parts in one file.
    file = DATA / "multi_part.exr"
    slots = {"R": _slot("R", file), "G": _slot("Z", file, 1), "B": _slot("R", file)}
    with patch.object(exr_io.OpenEXR, "File", wraps=OpenEXR.File) as opened:
        # When pixels and windows are requested together.
        sources = exr_io.load_slot_sources(slots)
    # Then no header-only open is needed and each slot retains its source.
    assert opened.call_count == 1
    assert list(sources) == ["R", "G", "B"]
    for slot, source in sources.items():
        assert source.pixels.dtype == np.float32
        assert source.pixels.shape == (source.data_window.height, source.data_window.width)
        expected = exr_io.load_exr_channels(str(file), slots[slot].part_index, [slots[slot].channel.name])
        np.testing.assert_array_equal(source.pixels, expected[slots[slot].channel.name])
    assert sources["R"].pixels is sources["B"].pixels


def test_different_files_keep_their_windows(tmp_path: Path) -> None:
    files = []
    for index, origin in enumerate([-2, 3]):
        path = tmp_path / f"source{index}.exr"
        header = {
            "dataWindow": (np.array([origin, -1], np.int32), np.array([origin + 3, 1], np.int32)),
            "displayWindow": (np.array([0, 0], np.int32), np.array([7, 3], np.int32)),
        }
        OpenEXR.File(header, {"Y": np.ones((3, 4), np.float16)}).write(str(path))
        files.append(path)
    with patch.object(exr_io.OpenEXR, "File", wraps=OpenEXR.File) as opened:
        sources = exr_io.load_slot_sources({"R": _slot("Y", files[0]), "A": _slot("Y", files[1])})
    assert opened.call_count == 2
    assert sources["R"].data_window == (-2, -1, 1, 1)
    assert sources["A"].data_window == (3, -1, 6, 1)
    assert sources["A"].display_window == (0, 0, 7, 3)


@pytest.mark.parametrize(("name", "part", "message"), [("missing", 0, "Unknown channel"), ("R", -1, "out of range")])
def test_errors_name_the_slot(name: str, part: int, message: str) -> None:
    with pytest.raises(ValueError, match=f"Failed to load G channel: .*{message}"):
        exr_io.load_slot_sources({"G": _slot(name, DATA / "single_part_rgba.exr", part)})


def test_sampling_metadata_is_read_from_decoded_channel(tmp_path: Path) -> None:
    path = tmp_path / "sampled.exr"
    plane = np.ones((4, 4), np.float32)
    channel = OpenEXR.Channel("Y", plane, xSampling=2, ySampling=2)
    OpenEXR.File({"compression": OpenEXR.NO_COMPRESSION}, {"Y": channel}).write(str(path))
    sources = exr_io.load_slot_sources({"R": _slot("Y", path)})
    assert sources["R"].x_sampling == 2
    assert sources["R"].y_sampling == 2


def test_missing_file_names_the_slot() -> None:
    with pytest.raises(RuntimeError, match="Failed to load A channel"):
        exr_io.load_slot_sources({"A": _slot("A", Path("/nonexistent/source.exr"))})
