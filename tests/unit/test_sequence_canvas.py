"""Explicit encoder geometry preserves pixels and rejects silent resizing."""

from pathlib import Path
from unittest.mock import patch

import numpy as np
import pytest
from PIL import Image

from griptape_nodes_openexr.exr import sequence_canvas


def _png(path: Path, size: tuple[int, int]) -> Path:
    Image.new("RGB", size, (12, 34, 56)).save(path)
    return path


@pytest.mark.parametrize("size", [(3, 2), (2, 3), (3, 3)])
def test_odd_frames_pad_right_and_bottom(tmp_path: Path, size: tuple[int, int]) -> None:
    path = _png(tmp_path / "input.png", size)
    result = sequence_canvas.prepare_frames(
        [path], ["input.exr"], tmp_path, "Pad to Even", "Require Matching Sizes", False, 100
    )
    with Image.open(result.frames[0]) as image:
        pixels = np.array(image)
        assert image.size == (size[0] + size[0] % 2, size[1] + size[1] % 2)
    assert (pixels[: size[1], : size[0]] == (12, 34, 56)).all()
    if size[0] % 2:
        assert not pixels[:, -1].any()
    if size[1] % 2:
        assert not pixels[-1].any()
    assert result.padded_frames == 1


def test_even_matching_images_are_not_rewritten(tmp_path: Path) -> None:
    path = _png(tmp_path / "input.png", (4, 2))
    content = path.read_bytes()
    result = sequence_canvas.prepare_frames(
        [path], ["input.exr"], tmp_path, "Require Even", "Require Matching Sizes", False, 8
    )
    assert result.frames == [path]
    assert result.size == (4, 2)
    assert result.padded_frames == 0
    assert path.read_bytes() == content


def test_padding_to_largest_resolves_maxima_from_different_frames_and_fills_missing(tmp_path: Path) -> None:
    # Given a failed first frame and differing width/height maxima.
    first = _png(tmp_path / "first.png", (5, 2))
    second = _png(tmp_path / "second.png", (2, 5))
    # When padding and black replacement are requested.
    result = sequence_canvas.prepare_frames(
        [None, first, second],
        ["missing.exr", "wide.exr", "tall.exr"],
        tmp_path,
        "Pad to Even",
        "Pad to Largest",
        True,
        36,
    )
    # Then all encoder inputs use the final even canvas.
    assert result.size == (6, 6)
    assert len(result.frames) == 3
    for path in result.frames:
        with Image.open(path) as image:
            assert image.size == (6, 6)
    with Image.open(result.frames[0]) as image:
        assert not np.array(image).any()


def test_matching_policy_reports_source_frames_before_writing(tmp_path: Path) -> None:
    paths = [_png(tmp_path / "one.png", (4, 2)), _png(tmp_path / "two.png", (2, 4))]
    with (
        patch.object(sequence_canvas.Image, "new") as allocate,
        pytest.raises(ValueError, match="first.exr.*4x2.*second.exr.*2x4"),
    ):
        sequence_canvas.prepare_frames(
            paths, ["first.exr", "second.exr"], tmp_path, "Pad to Even", "Require Matching Sizes", False, 100
        )
    allocate.assert_not_called()


def test_matching_policy_compares_original_sizes_before_even_padding(tmp_path: Path) -> None:
    paths = [_png(tmp_path / "one.png", (3, 2)), _png(tmp_path / "two.png", (4, 2))]
    with pytest.raises(ValueError, match="frame_sizes"):
        sequence_canvas.prepare_frames(
            paths, ["one.exr", "two.exr"], tmp_path, "Pad to Even", "Require Matching Sizes", False, 100
        )


def test_require_even_reports_actionable_error(tmp_path: Path) -> None:
    path = _png(tmp_path / "odd.png", (3, 2))
    with pytest.raises(ValueError, match="3x2.*Pad to Even"):
        sequence_canvas.prepare_frames(
            [path], ["odd.exr"], tmp_path, "Require Even", "Require Matching Sizes", False, 100
        )


def test_limit_includes_even_padding_and_union_canvas(tmp_path: Path) -> None:
    path = _png(tmp_path / "odd.png", (3, 3))
    with (
        patch.object(sequence_canvas.Image, "new") as allocate,
        pytest.raises(ValueError, match="openexr.max_render_pixels"),
    ):
        sequence_canvas.prepare_frames([path], ["odd.exr"], tmp_path, "Pad to Even", "Require Matching Sizes", True, 9)
    allocate.assert_not_called()


def test_failed_frames_are_skipped_when_black_replacement_is_off(tmp_path: Path) -> None:
    path = _png(tmp_path / "good.png", (4, 2))
    result = sequence_canvas.prepare_frames(
        [None, path], ["missing.exr", "good.exr"], tmp_path, "Pad to Even", "Require Matching Sizes", False, 8
    )
    assert result.frames == [path]


@pytest.mark.parametrize(
    ("odd", "sizes", "message"), [("typo", "Pad to Largest", "odd_dimensions"), ("Pad to Even", "typo", "frame_sizes")]
)
def test_unknown_policies_are_rejected(odd: str, sizes: str, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        sequence_canvas.validate_policies(odd, sizes)
