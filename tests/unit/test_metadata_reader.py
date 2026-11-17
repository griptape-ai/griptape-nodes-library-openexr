"""Disk metadata reads without engine path resolution or node state."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from pathlib import Path
from unittest.mock import Mock, patch

import numpy as np
import OpenEXR
import pytest
from griptape_nodes.retained_mode.engine import has_current_engine, reset_root_engine

from griptape_nodes_openexr.exr import exr_io
from griptape_nodes_openexr.exr.exr_io import scan_exr_header_from_path
from griptape_nodes_openexr.exr.exr_types import EXRData, PixelType
from griptape_nodes_openexr.exr.metadata_reader import read_metadata

DATA = Path(__file__).parents[1] / "data"


@pytest.fixture(autouse=True)
def no_engine_resolution() -> Iterator[None]:
    """Any attempt to resolve an engine path fails the disk-reader tests."""
    reset_root_engine()
    assert not has_current_engine()
    with patch.object(exr_io, "File", side_effect=AssertionError("Engine path resolution is forbidden")):
        yield
    assert not has_current_engine()


def test_reads_absolute_single_part_path(tmp_path: Path) -> None:
    path = tmp_path / "metadata.exr"
    pixels = np.zeros((4, 6), dtype=np.float32)
    OpenEXR.File(
        {"owner": "artist", "shot": "010", "weights": np.array([1.0, 2.0, 3.0], dtype=np.float32)},
        {"R": pixels},
    ).write(str(path))

    data = read_metadata(str(path))

    assert isinstance(data, EXRData)
    assert len(data.parts) == 1
    part = data.parts[0]
    assert (part.width, part.height) == (6, 4)
    assert part.header.owner == "artist"
    assert part.header.custom == {"shot": "010", "weights": [1.0, 2.0, 3.0]}
    assert [channel.name for channel in part.channels] == ["R"]
    assert part.channels[0].pixel_type == PixelType.HALF


def test_reads_all_multipart_headers() -> None:
    data = read_metadata(str(DATA / "multi_part.exr"))

    assert [part.name for part in data.parts] == ["rgba", "depth", "normal"]
    assert [channel.name for channel in data.parts[0].channels] == ["A", "B", "G", "R"]
    assert [channel.name for channel in data.parts[1].channels] == ["Z"]
    assert all((part.width, part.height) == (64, 64) for part in data.parts)


def test_absent_optional_attributes_preserve_defaults() -> None:
    header = read_metadata(str(DATA / "single_part_rgba.exr")).parts[0].header

    assert header.owner == header.software == header.comments == header.capture_date == ""
    assert header.chromaticities is None
    assert header.time_code is None
    assert header.tile_description is None
    assert header.custom == {}


@pytest.mark.parametrize("path", ["", "relative.exr", "{{workspace}}/shot.exr"])
@pytest.mark.parametrize("reader", [read_metadata, scan_exr_header_from_path])
def test_rejects_unresolved_paths(path: str, reader: Callable[[str], EXRData]) -> None:
    with pytest.raises(ValueError, match="empty|absolute"):
        reader(path)


@pytest.mark.parametrize("reader", [read_metadata, scan_exr_header_from_path])
def test_missing_file_reports_path_and_cause(tmp_path: Path, reader: Callable[[str], EXRData]) -> None:
    path = tmp_path / "missing.exr"

    with pytest.raises(RuntimeError, match="Failed to open EXR file") as error:
        reader(str(path))

    assert str(path) in str(error.value)
    assert error.value.__cause__ is not None


@pytest.mark.parametrize("reader", [read_metadata, scan_exr_header_from_path])
def test_corrupt_file_reports_path_and_cause(tmp_path: Path, reader: Callable[[str], EXRData]) -> None:
    path = tmp_path / "corrupt.exr"
    path.write_bytes(b"not an EXR")

    with pytest.raises(RuntimeError, match="Failed to open EXR file") as error:
        reader(str(path))

    assert str(path) in str(error.value)
    assert error.value.__cause__ is not None


@pytest.mark.parametrize("header_only", [True, False])
def test_disk_scanner_preserves_pixel_type_modes(header_only: bool) -> None:
    data = scan_exr_header_from_path(str(DATA / "pixel_types.exr"), header_only=header_only)
    types = {channel.name: channel.pixel_type for channel in data.parts[0].channels}

    assert types == (
        {"float_ch": PixelType.HALF, "half_ch": PixelType.HALF, "uint_ch": PixelType.HALF}
        if header_only
        else {"float_ch": PixelType.FLOAT, "half_ch": PixelType.HALF, "uint_ch": PixelType.UINT}
    )


def test_metadata_reader_delegates_header_only() -> None:
    expected = EXRData(parts=[])
    path = str(DATA / "single_part_rgba.exr")
    with patch("griptape_nodes_openexr.exr.metadata_reader.scan_exr_header_from_path", return_value=expected) as scan:
        assert read_metadata(path) is expected
    scan.assert_called_once_with(path, header_only=True)


@pytest.mark.parametrize("header_only", [True, False])
def test_compatibility_wrapper_resolves_engine_path_and_delegates(header_only: bool) -> None:
    resolved = str(DATA / "single_part_rgba.exr")
    expected = EXRData(parts=[])
    with (
        patch.object(exr_io, "File") as file,
        patch.object(exr_io, "scan_exr_header_from_path", return_value=expected) as scan,
    ):
        file.return_value.resolve.return_value = resolved
        assert exr_io.scan_exr_header("{{workspace}}/shot.exr", header_only=header_only) is expected
    file.assert_called_once_with("{{workspace}}/shot.exr")
    file.return_value.resolve.assert_called_once_with()
    scan.assert_called_once_with(resolved, header_only=header_only)


def test_compatibility_wrapper_rejects_empty_path_before_resolution() -> None:
    with pytest.raises(ValueError, match="file_path must not be empty"):
        exr_io.scan_exr_header("")


@pytest.mark.parametrize("fixture", sorted(path.name for path in DATA.glob("*.exr")))
@pytest.mark.parametrize("header_only", [True, False])
def test_extraction_preserves_existing_conversions(fixture: str, header_only: bool) -> None:
    path = str(DATA / fixture)
    with patch.object(exr_io, "File") as file:
        file.return_value.resolve.return_value = path
        expected = exr_io.scan_exr_header(path, header_only=header_only)
    assert scan_exr_header_from_path(path, header_only=header_only) == expected


def test_no_parts_preserves_value_error() -> None:
    with patch.object(exr_io.OpenEXR, "File") as file:
        file.return_value.__enter__.return_value.parts = []
        with pytest.raises(ValueError, match="EXR file has no parts"):
            read_metadata(str(DATA / "single_part_rgba.exr"))


def test_missing_required_attribute_preserves_wrapped_error() -> None:
    part = Mock()
    part.header = {}
    with patch.object(exr_io.OpenEXR, "File") as file:
        file.return_value.__enter__.return_value.parts = [part]
        with pytest.raises(RuntimeError, match="Required EXR header attribute 'dataWindow' is missing") as error:
            read_metadata(str(DATA / "single_part_rgba.exr"))
    assert isinstance(error.value.__cause__, ValueError)


def test_each_metadata_request_reads_disk_again(tmp_path: Path) -> None:
    path = tmp_path / "rewritten.exr"
    pixels = np.zeros((2, 3), dtype=np.float32)
    OpenEXR.File({"owner": "first"}, {"R": pixels}).write(str(path))
    first = read_metadata(str(path))
    OpenEXR.File({"owner": "second"}, {"R": pixels}).write(str(path))
    second = read_metadata(str(path))

    assert first.parts[0].header.owner == "first"
    assert second.parts[0].header.owner == "second"
