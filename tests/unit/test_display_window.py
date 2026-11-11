"""Unit tests for the shared display-window framing."""

from __future__ import annotations

import numpy as np
import pytest

from griptape_nodes_openexr.exr.display_window import (
    WINDOW_MODE_CHOICES,
    WINDOW_MODE_DATA,
    WINDOW_MODE_DEFAULT,
    WINDOW_MODE_DISPLAY,
    WINDOW_MODE_OVERLAY,
    WindowFraming,
    apply_window_mode,
    window_size,
)
from griptape_nodes_openexr.exr.exr_types import WindowCoordinates


def _image(window: WindowCoordinates, channels: int = 3) -> np.ndarray:
    """A data-window image whose every pixel is distinct and nonzero, so a misplaced copy shows."""
    height, width = window.ymax - window.ymin + 1, window.xmax - window.xmin + 1
    ys, xs = np.mgrid[0:height, 0:width]
    image = np.zeros((height, width, channels), dtype=np.uint8)
    image[..., 0] = 1 + xs
    image[..., 1] = 1 + ys
    image[..., 2] = 7
    if channels == 4:  # noqa: PLR2004
        image[..., 3] = 200
    return image


def _framing(mode: str, data: tuple[int, int, int, int], display: tuple[int, int, int, int]) -> WindowFraming:
    return WindowFraming(mode, WindowCoordinates(*data), WindowCoordinates(*display))


def _pixel_at(image: np.ndarray, window: WindowCoordinates, x: int, y: int) -> np.ndarray:
    """The pixel of an image of `window` at EXR coordinate (x, y)."""
    return image[y - window.ymin, x - window.xmin]


class TestChoices:
    def test_data_window_is_the_default(self) -> None:
        assert WINDOW_MODE_DEFAULT == WINDOW_MODE_DATA

    def test_three_modes_are_offered(self) -> None:
        assert WINDOW_MODE_CHOICES == [WINDOW_MODE_DATA, WINDOW_MODE_DISPLAY, WINDOW_MODE_OVERLAY]

    def test_unknown_mode_is_reported(self) -> None:
        framing = _framing("letterbox", (0, 0, 3, 3), (0, 0, 3, 3))
        with pytest.raises(ValueError, match="Unknown window_mode 'letterbox'"):
            apply_window_mode(_image(framing.data_window), framing)


class TestDataWindow:
    def test_returns_the_image_unchanged(self) -> None:
        framing = _framing(WINDOW_MODE_DATA, (-8, -8, 71, 71), (0, 0, 63, 63))
        image = _image(framing.data_window)
        assert apply_window_mode(image, framing) is image

    def test_size_is_the_data_window(self) -> None:
        assert window_size(_framing(WINDOW_MODE_DATA, (-8, -4, 71, 67), (0, 0, 63, 63))) == (80, 72)


class TestDisplayWindow:
    def test_matching_windows_leave_the_image_as_is(self) -> None:
        framing = _framing(WINDOW_MODE_DISPLAY, (0, 0, 15, 9), (0, 0, 15, 9))
        image = _image(framing.data_window)
        np.testing.assert_array_equal(apply_window_mode(image, framing), image)

    def test_overscan_is_cropped_away(self) -> None:
        framing = _framing(WINDOW_MODE_DISPLAY, (-8, -4, 23, 19), (0, 0, 15, 15))
        image = _image(framing.data_window)
        out = apply_window_mode(image, framing)

        assert out.shape == (16, 16, 3)
        np.testing.assert_array_equal(out, image[4:20, 8:24])

    def test_data_smaller_than_display_is_padded_with_transparent_black(self) -> None:
        framing = _framing(WINDOW_MODE_DISPLAY, (4, 2, 7, 5), (0, 0, 11, 7))
        image = _image(framing.data_window, channels=4)
        out = apply_window_mode(image, framing)

        assert out.shape == (8, 12, 4)
        np.testing.assert_array_equal(out[2:6, 4:8], image)
        padding = np.ones((8, 12), dtype=bool)
        padding[2:6, 4:8] = False
        assert not out[padding].any(), "padding should be black with zero alpha"

    def test_rgb_padding_is_black(self) -> None:
        framing = _framing(WINDOW_MODE_DISPLAY, (2, 2, 3, 3), (0, 0, 5, 5))
        out = apply_window_mode(_image(framing.data_window), framing)
        assert out.shape == (6, 6, 3)
        assert not out[0].any()
        assert out[2, 2].any()

    def test_partially_overlapping_windows_keep_only_the_overlap(self) -> None:
        # Data covers the display window's right half and spills past its bottom.
        framing = _framing(WINDOW_MODE_DISPLAY, (8, 4, 23, 23), (0, 0, 15, 15))
        image = _image(framing.data_window)
        out = apply_window_mode(image, framing)

        assert out.shape == (16, 16, 3)
        np.testing.assert_array_equal(out[4:16, 8:16], image[0:12, 0:8])
        assert not out[:, :8].any()
        assert not out[:4].any()

    def test_disjoint_windows_give_an_all_black_frame(self) -> None:
        framing = _framing(WINDOW_MODE_DISPLAY, (100, 100, 109, 109), (0, 0, 15, 15))
        out = apply_window_mode(_image(framing.data_window), framing)
        assert out.shape == (16, 16, 3)
        assert not out.any()

    @pytest.mark.parametrize(
        ("data", "display"),
        [
            ((-20, -10, 9, 9), (-16, -8, 3, 1)),
            ((100, 50, 139, 89), (110, 60, 129, 79)),
            ((-5, 3, 14, 22), (0, 0, 9, 9)),
        ],
        ids=["negative-origins", "positive-origins", "mixed"],
    )
    def test_each_pixel_lands_at_its_exr_coordinate(
        self, data: tuple[int, int, int, int], display: tuple[int, int, int, int]
    ) -> None:
        framing = _framing(WINDOW_MODE_DISPLAY, data, display)
        image = _image(framing.data_window)
        out = apply_window_mode(image, framing)
        dw, disp = framing.data_window, framing.display_window

        assert out.shape[:2] == (disp.ymax - disp.ymin + 1, disp.xmax - disp.xmin + 1)
        for y in range(disp.ymin, disp.ymax + 1):
            for x in range(disp.xmin, disp.xmax + 1):
                inside = dw.xmin <= x <= dw.xmax and dw.ymin <= y <= dw.ymax
                expected = _pixel_at(image, dw, x, y) if inside else np.zeros(3, dtype=np.uint8)
                np.testing.assert_array_equal(_pixel_at(out, disp, x, y), expected)

    def test_size_is_the_display_window(self) -> None:
        assert window_size(_framing(WINDOW_MODE_DISPLAY, (-8, -4, 71, 67), (0, 0, 63, 47))) == (64, 48)


class TestOverlay:
    def test_keeps_the_data_window_size_and_leaves_the_input_untouched(self) -> None:
        framing = _framing(WINDOW_MODE_OVERLAY, (-8, -8, 23, 23), (0, 0, 15, 15))
        image = _image(framing.data_window)
        before = image.copy()
        out = apply_window_mode(image, framing)

        assert out.shape == image.shape
        np.testing.assert_array_equal(image, before)

    def test_outlines_the_display_window_edges_only(self) -> None:
        framing = _framing(WINDOW_MODE_OVERLAY, (-8, -8, 23, 23), (0, 0, 15, 15))
        image = _image(framing.data_window)
        out = apply_window_mode(image, framing)

        changed = (out != image).any(axis=-1)
        # The display window spans image rows and columns 8..23.
        edge = np.zeros_like(changed)
        edge[8, 8:24] = edge[23, 8:24] = True
        edge[8:24, 8] = edge[8:24, 23] = True
        assert not changed[~edge].any(), "pixels off the outline changed"
        assert changed[edge].sum() > edge.sum() // 2
        # Outline pixels are drawn in the two outline colours.
        assert set(np.unique(out[edge])) <= {0, 255}

    def test_outline_uses_coordinates_relative_to_a_negative_data_origin(self) -> None:
        framing = _framing(WINDOW_MODE_OVERLAY, (-30, -20, 9, 9), (-26, -12, 3, 1))
        image = _image(framing.data_window)
        out = apply_window_mode(image, framing)

        changed = (out != image).any(axis=-1)
        rows, cols = np.nonzero(changed)
        assert (rows.min(), rows.max()) == (8, 21)
        assert (cols.min(), cols.max()) == (4, 33)

    def test_edges_outside_the_data_window_are_clipped(self) -> None:
        # Display extends past the data window on the left and bottom.
        framing = _framing(WINDOW_MODE_OVERLAY, (0, 0, 31, 31), (-10, 4, 20, 40))
        image = _image(framing.data_window)
        out = apply_window_mode(image, framing)

        assert out.shape == image.shape
        changed = (out != image).any(axis=-1)
        rows, cols = np.nonzero(changed)
        # Only the top edge (row 4) and right edge (column 20) fall inside the image.
        assert set(rows[cols != 20]) == {4}  # noqa: PLR2004
        assert set(cols[rows != 4]) == {20}  # noqa: PLR2004
        assert cols.min() == 0
        assert rows.max() == 31  # noqa: PLR2004

    def test_display_window_entirely_outside_draws_nothing(self) -> None:
        framing = _framing(WINDOW_MODE_OVERLAY, (0, 0, 15, 15), (100, 100, 199, 199))
        image = _image(framing.data_window)
        np.testing.assert_array_equal(apply_window_mode(image, framing), image)

    def test_rgba_outline_is_opaque(self) -> None:
        framing = _framing(WINDOW_MODE_OVERLAY, (-4, -4, 11, 11), (0, 0, 7, 7))
        image = _image(framing.data_window, channels=4)
        image[..., 3] = 0
        out = apply_window_mode(image, framing)

        assert out[4, 4, 3] == 255  # noqa: PLR2004
        assert out[0, 0, 3] == 0

    def test_matching_windows_outline_the_image_border(self) -> None:
        framing = _framing(WINDOW_MODE_OVERLAY, (0, 0, 15, 15), (0, 0, 15, 15))
        image = _image(framing.data_window)
        changed = (apply_window_mode(image, framing) != image).any(axis=-1)
        assert changed[0].any()
        assert changed[-1].any()
        assert not changed[1:-1, 1:-1].any()

    def test_outline_thickens_on_large_images(self) -> None:
        framing = _framing(WINDOW_MODE_OVERLAY, (-64, -64, 2111, 1279), (0, 0, 2047, 1215))
        image = np.full((1344, 2176, 3), 128, dtype=np.uint8)
        changed = (apply_window_mode(image, framing) != image).any(axis=-1)
        # 2176 // 512 = 4 pixel outline, drawn inside the display window from row 64.
        assert changed[64:68, 64:2112].any(axis=1).all()
        assert not changed[68, 100:2000].any()

    def test_size_is_the_data_window(self) -> None:
        assert window_size(_framing(WINDOW_MODE_OVERLAY, (-8, -4, 71, 67), (0, 0, 63, 63))) == (80, 72)


class TestShapeMismatch:
    @pytest.mark.parametrize("mode", [WINDOW_MODE_DISPLAY, WINDOW_MODE_OVERLAY])
    def test_an_image_not_the_size_of_the_data_window_is_rejected(self, mode: str) -> None:
        framing = _framing(mode, (0, 0, 15, 15), (0, 0, 15, 15))
        with pytest.raises(ValueError, match=r"size of the data window \(16x16\), got 8x8"):
            apply_window_mode(np.zeros((8, 8, 3), dtype=np.uint8), framing)
