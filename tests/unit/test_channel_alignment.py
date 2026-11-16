"""Geometry and canvas guards for channel assembly."""

from unittest.mock import patch

import numpy as np
import pytest

from griptape_nodes_openexr.exr import channel_alignment as alignment
from griptape_nodes_openexr.exr.display_window import WINDOW_MODE_DATA, WINDOW_MODE_DISPLAY, WINDOW_MODE_OVERLAY
from griptape_nodes_openexr.exr.exr_io import ChannelSource
from griptape_nodes_openexr.exr.exr_types import WindowCoordinates


def _source(
    bounds: tuple[int, int, int, int], value: float = 1, display: tuple[int, int, int, int] = (0, 0, 3, 1)
) -> ChannelSource:
    window = WindowCoordinates(*bounds)
    return ChannelSource(np.full((window.height, window.width), value, np.float32), window, WindowCoordinates(*display))


@pytest.mark.parametrize("mode", [WINDOW_MODE_DATA, WINDOW_MODE_OVERLAY])
def test_coordinate_union_includes_alpha_and_leaves_gaps_uncovered(mode: str) -> None:
    # Given three disjoint windows, including alpha outside the RGB extent.
    sources = {"R": _source((-2, -1, -1, 0)), "G": _source((1, 0, 2, 1), 0.5), "A": _source((4, 0, 4, 1))}
    # When assembled in EXR coordinates.
    result = alignment.assemble_channels(sources, alignment.ALIGN_COORDINATES, "First connected RGB", mode, 100)
    # Then positions and zero-coverage gaps follow the source coordinates.
    assert result.framing.data_window == (-2, -1, 4, 1)
    assert result.planes["R"].shape == (3, 7)
    assert result.planes["R"][0, 0] == 1
    assert result.planes["G"][1, 3] == 0.5
    assert result.planes["A"][1, 6] == 1
    assert not result.rgb_coverage[:, 2].any()
    assert not result.rgb_coverage[:, 6].any()


def test_pixel_alignment_ignores_origins_and_uses_reference_geometry() -> None:
    sources = {"R": _source((-2, 0, 1, 1)), "G": _source((3, 0, 6, 1), 0.5)}
    result = alignment.assemble_channels(sources, alignment.ALIGN_PIXELS, "G", WINDOW_MODE_DATA, 8)
    assert result.reference_slot == "G"
    assert result.framing.data_window == sources["G"].data_window
    np.testing.assert_array_equal(result.planes["R"], sources["R"].pixels)


def test_coordinate_crop_avoids_allocating_distant_union() -> None:
    sources = {"R": _source((0, 0, 1, 1)), "G": _source((100000, 0, 100001, 1))}
    result = alignment.assemble_channels(sources, alignment.ALIGN_COORDINATES, "R", WINDOW_MODE_DISPLAY, 8)
    assert result.planes["R"].shape == (2, 4)
    assert result.framing.data_window == (0, 0, 3, 1)
    assert not result.planes["G"].any()


@pytest.mark.parametrize("policy", ["Match pixel positions", "Match EXR coordinates", "Require matching windows"])
def test_output_limit_checked_before_allocating(policy: str) -> None:
    sources = {"R": _source((0, 0, 3, 1))}
    with patch.object(alignment.np, "zeros") as allocate, pytest.raises(ValueError, match="openexr.max_render_pixels"):
        alignment.assemble_channels(sources, policy, "R", WINDOW_MODE_DISPLAY, 7)
    allocate.assert_not_called()


@pytest.mark.parametrize("display", [False, True])
def test_strict_mode_reports_mismatching_slot(display: bool) -> None:
    other = _source((0, 0, 3, 1), display=(1, 0, 4, 1)) if display else _source((-1, 0, 2, 1))
    with pytest.raises(ValueError, match="A.*windows.*R"):
        alignment.assemble_channels(
            {"R": _source((0, 0, 3, 1)), "A": other}, alignment.ALIGN_STRICT, "R", WINDOW_MODE_DATA, 100
        )


def test_pixel_mode_rejects_different_shapes() -> None:
    with pytest.raises(ValueError, match="dimensions do not match"):
        alignment.assemble_channels(
            {"R": _source((0, 0, 3, 1)), "G": _source((0, 0, 1, 1))}, alignment.ALIGN_PIXELS, "R", WINDOW_MODE_DATA, 100
        )


@pytest.mark.parametrize("policy", ["Match EXR coordinates", "Require matching windows"])
def test_subsampled_source_reports_slot(policy: str) -> None:
    source = _source((0, 0, 3, 1))
    source.pixels.resize((1, 4))
    with pytest.raises(ValueError, match="R.*data window"):
        alignment.assemble_channels({"R": source}, policy, "R", WINDOW_MODE_DATA, 100)


@pytest.mark.parametrize("policy", [alignment.ALIGN_COORDINATES, alignment.ALIGN_STRICT])
def test_sampling_metadata_rejects_even_full_sized_arrays(policy: str) -> None:
    source = _source((0, 0, 3, 1))
    sampled = ChannelSource(source.pixels, source.data_window, source.display_window, x_sampling=2)
    with pytest.raises(ValueError, match="R.*subsampled"):
        alignment.assemble_channels({"R": sampled}, policy, "R", WINDOW_MODE_DATA, 100)


def test_pixel_mode_preserves_dense_binding_layout_for_sampled_sources() -> None:
    source = _source((0, 0, 3, 1))
    sampled = ChannelSource(source.pixels, source.data_window, source.display_window, x_sampling=2)
    result = alignment.assemble_channels({"R": sampled}, alignment.ALIGN_PIXELS, "R", WINDOW_MODE_DATA, 100)
    np.testing.assert_array_equal(result.planes["R"], source.pixels)


def test_disjoint_intermediate_is_checked_before_assembly() -> None:
    sources = {"R": _source((-10, 0, -6, 1))}
    with patch.object(alignment.np, "ones") as allocate, pytest.raises(ValueError, match="openexr.max_render_pixels"):
        alignment.assemble_channels(sources, alignment.ALIGN_PIXELS, "R", WINDOW_MODE_DISPLAY, 8)
    allocate.assert_not_called()


@pytest.mark.parametrize("mode", [WINDOW_MODE_DATA, WINDOW_MODE_OVERLAY])
def test_distant_union_is_rejected_before_allocating(mode: str) -> None:
    sources = {"R": _source((0, 0, 1, 1)), "A": _source((10000, 0, 10001, 1))}
    with patch.object(alignment.np, "zeros") as allocate, pytest.raises(ValueError, match="openexr.max_render_pixels"):
        alignment.assemble_channels(sources, alignment.ALIGN_COORDINATES, "R", mode, 100)
    allocate.assert_not_called()


def test_automatic_reference_uses_rgb_order() -> None:
    result = alignment.assemble_channels(
        {"B": _source((0, 0, 3, 1)), "G": _source((0, 0, 3, 1))},
        alignment.ALIGN_PIXELS,
        "First connected RGB",
        WINDOW_MODE_DATA,
        8,
    )
    assert result.reference_slot == "G"


def test_disjoint_crop_has_nonempty_zero_coverage_processing_region() -> None:
    result = alignment.assemble_channels(
        {"R": _source((-10, 0, -9, 1))}, alignment.ALIGN_COORDINATES, "R", WINDOW_MODE_DISPLAY, 8
    )
    assert result.planes["R"].size > 0
    assert not result.rgb_coverage.any()


@pytest.mark.parametrize("reference", ["R", "G", "B", "A"])
def test_explicit_reference_uses_its_display_window(reference: str) -> None:
    sources = {slot: _source((0, 0, 3, 1), display=(index, 0, index + 3, 1)) for index, slot in enumerate("RGBA")}
    result = alignment.assemble_channels(sources, alignment.ALIGN_COORDINATES, reference, WINDOW_MODE_DISPLAY, 100)
    assert result.framing.display_window == sources[reference].display_window


@pytest.mark.parametrize(
    ("policy", "reference", "mode", "message"),
    [
        ("unknown", "R", WINDOW_MODE_DATA, "Unknown channel_alignment"),
        ("Match pixel positions", "unknown", WINDOW_MODE_DATA, "Unknown frame_reference"),
        ("Match pixel positions", "A", WINDOW_MODE_DISPLAY, "A.*not connected"),
        ("Match pixel positions", "R", "unknown", "Unknown window_mode"),
    ],
)
def test_invalid_choices(policy: str, reference: str, mode: str, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        alignment.assemble_channels({"R": _source((0, 0, 3, 1))}, policy, reference, mode, 100)
