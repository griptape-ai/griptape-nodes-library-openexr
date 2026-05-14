"""Unit tests for _normalize_windows()."""

from griptape_nodes_openexr.exr.exr_types import WindowCoordinates, _normalize_windows


def test_zero_origin_no_op() -> None:
    data = WindowCoordinates(0, 0, 1919, 1079)
    display = WindowCoordinates(0, 0, 1919, 1079)
    result = _normalize_windows(data, display)
    assert result.data == data
    assert result.display == display


def test_positive_display_offset() -> None:
    # Display window starts at (10, 20)
    display = WindowCoordinates(10, 20, 1929, 1099)
    data = WindowCoordinates(10, 20, 1929, 1099)
    result = _normalize_windows(data, display)
    assert result.display == WindowCoordinates(0, 0, 1919, 1079)
    assert result.data == WindowCoordinates(0, 0, 1919, 1079)


def test_negative_display_offset() -> None:
    # Display window starts at (-8, -8) — common overscan pattern
    display = WindowCoordinates(-8, -8, 1927, 1087)
    data = WindowCoordinates(0, 0, 1919, 1079)
    result = _normalize_windows(data, display)
    assert result.display == WindowCoordinates(0, 0, 1935, 1095)
    assert result.data == WindowCoordinates(8, 8, 1927, 1087)


def test_data_window_smaller_than_display() -> None:
    # Data window is a crop within a larger display
    display = WindowCoordinates(0, 0, 3839, 2159)
    data = WindowCoordinates(512, 256, 3327, 1903)
    result = _normalize_windows(data, display)
    # No offset needed — display already at origin
    assert result.display == display
    assert result.data == data
