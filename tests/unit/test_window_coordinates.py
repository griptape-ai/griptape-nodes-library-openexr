"""Dimensions of EXR windows with inclusive bounds."""

from __future__ import annotations

import pytest

from griptape_nodes_openexr.exr.exr_types import WindowCoordinates


@pytest.mark.parametrize(
    ("bounds", "expected"),
    [
        ((0, 0, 0, 0), (1, 1)),
        ((0, 0, 15, 11), (16, 12)),
        ((10, 20, 25, 31), (16, 12)),
        ((-16, -12, -1, -1), (16, 12)),
        ((-8, -4, 23, 15), (32, 20)),
    ],
)
def test_dimensions_use_inclusive_bounds(bounds: tuple[int, int, int, int], expected: tuple[int, int]) -> None:
    window = WindowCoordinates(*bounds)
    assert (window.width, window.height) == expected
    assert type(window.width) is int
    assert type(window.height) is int


def test_dimension_properties_preserve_tuple_fields() -> None:
    window = WindowCoordinates(-8, -4, 23, 15)
    assert tuple(window) == (-8, -4, 23, 15)
    assert window._asdict() == {"xmin": -8, "ymin": -4, "xmax": 23, "ymax": 15}
