"""Resolve uniform even-sized encoder inputs without resizing source pixels."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from PIL import Image

from griptape_nodes_openexr.exr.exr_types import WindowCoordinates
from griptape_nodes_openexr.exr.render_limits import check_canvas

ODD_PAD = "Pad to even"
ODD_REQUIRE = "Require even"
ODD_CHOICES = [ODD_PAD, ODD_REQUIRE]
SIZES_REQUIRE = "Require matching sizes"
SIZES_PAD = "Pad to largest"
SIZES_CHOICES = [SIZES_REQUIRE, SIZES_PAD]


@dataclass(frozen=True)
class PreparedFrames:
    """Uniform staged inputs and their resolved encoder canvas."""

    frames: list[Path]
    size: tuple[int, int]
    padded_frames: int


def validate_policies(odd_dimensions: str, frame_sizes: str) -> None:
    """Reject unknown choices before scheduling frame conversions."""
    for name, value, choices in [
        ("odd_dimensions", odd_dimensions, ODD_CHOICES),
        ("frame_sizes", frame_sizes, SIZES_CHOICES),
    ]:
        if value not in choices:
            msg = f"Unknown {name} '{value}'. Choose one of: {', '.join(choices)}"
            raise ValueError(msg)


def prepare_frames(  # noqa: PLR0913
    results: Sequence[Path | None],
    source_paths: Sequence[str],
    temp_dir: Path,
    odd_dimensions: str,
    frame_sizes: str,
    replace_black: bool,
    max_pixels: int,
) -> PreparedFrames:
    """Check rendered sizes, then pad and replace failed frames one at a time.

    Successful frames define the canvas. Matching compares original rendered
    dimensions before even padding. Padding uses display-encoded black on the
    right/bottom, without cropping, scaling or EXR-coordinate alignment.
    """
    validate_policies(odd_dimensions, frame_sizes)
    sizes: dict[int, tuple[int, int]] = {}
    for index, path in enumerate(results):
        if path is not None:
            with Image.open(path) as image:
                sizes[index] = image.size
    if not sizes:
        msg = "No converted frames available for encoding"
        raise ValueError(msg)
    first_index = next(iter(sizes))
    first_size = sizes[first_index]
    if frame_sizes == SIZES_REQUIRE:
        for index, size in sizes.items():
            if size != first_size:
                msg = (
                    f"frame_sizes requires matching sizes: frame {first_index} '{source_paths[first_index]}' "
                    f"is {first_size[0]}x{first_size[1]}, frame {index} '{source_paths[index]}' "
                    f"is {size[0]}x{size[1]}. Choose Pad to largest or Crop Overscan with a shared display window."
                )
                raise ValueError(msg)
    width = max(size[0] for size in sizes.values())
    height = max(size[1] for size in sizes.values())
    if odd_dimensions == ODD_REQUIRE and (width % 2 or height % 2):
        msg = f"Encoder canvas {width}x{height} must have even dimensions. Choose Pad to even."
        raise ValueError(msg)
    if odd_dimensions == ODD_PAD:
        width += width % 2
        height += height % 2
    check_canvas(WindowCoordinates(0, 0, width - 1, height - 1), max_pixels)
    size = (width, height)
    frames: list[Path] = []
    padded = 0
    for index, path in enumerate(results):
        if path is None and not replace_black:
            continue
        if path is not None and sizes[index] == size:
            frames.append(path)
            continue
        target = temp_dir / f"canvas_{index:04d}.png"
        with Image.new("RGB", size) as canvas:
            if path is not None:
                with Image.open(path) as source:
                    canvas.paste(source, (0, 0))
                padded += 1
            canvas.save(target)
        if path is not None:
            path.unlink()
        frames.append(target)
    for path in frames:
        with Image.open(path) as image:
            if image.size != size:
                msg = f"Staged frame '{path}' does not match encoder canvas {width}x{height}"
                raise ValueError(msg)
    return PreparedFrames(frames, size, padded)
