"""MP4 and MOV colour signalling, verified against the bundled ffmpeg binary.

The unit tests assert the command we build; these assert ffmpeg writes matching
stream headers, container colr boxes, and YUV pixel values in both basic and
OCIO modes.
"""

from __future__ import annotations

import json
import struct
import subprocess
from pathlib import Path

import pytest
from static_ffmpeg import run  # type: ignore[import-untyped]

from griptape_nodes_openexr.exr.tone_mapping import TONE_CHOICES
from griptape_nodes_openexr.nodes.exr_sequence_to_preview import (
    _MOVFLAGS,
    _PIX_FMT,
    color_trc_for,
    video_colour_args,
)


@pytest.fixture(scope="module")
def ffmpeg_tools() -> tuple[str, str]:
    # static_ffmpeg is a runtime dependency, so a failed fetch fails the test
    # rather than skipping it: a skip here would pass CI with nothing checked.
    ffmpeg, ffprobe = run.get_or_fetch_platform_executables_else_raise()
    return ffmpeg, ffprobe


def _encode(ffmpeg: str, out: Path, tone: str, *, ocio: bool = False) -> None:
    """Encode a short clip with the exact colour and container flags the node uses."""
    cmd = [
        ffmpeg,
        "-loglevel",
        "error",
        "-f",
        "lavfi",
        "-i",
        "testsrc=duration=0.1:size=64x64:rate=10",
        "-c:v",
        "libx264",
        *video_colour_args(tone, ocio=ocio),
        "-pix_fmt",
        _PIX_FMT,
        "-movflags",
        _MOVFLAGS,
        "-y",
        str(out),
    ]
    subprocess.run(cmd, check=True, capture_output=True, timeout=120)  # noqa: S603


def _stream_tags(ffprobe: str, path: Path) -> dict:
    result = subprocess.run(  # noqa: S603
        [ffprobe, "-v", "error", "-show_streams", "-of", "json", str(path)],
        check=True,
        capture_output=True,
        text=True,
        timeout=60,
    )
    return json.loads(result.stdout)["streams"][0]


def _colr_cicp(path: Path) -> tuple[bytes, int, int, int]:
    """Read (colour_type, primaries, transfer, matrix) from an MP4/MOV colr box."""
    raw = path.read_bytes()
    idx = raw.find(b"colr")
    assert idx >= 4, f"No colr box found in {path.name}"
    colour_type = raw[idx + 4 : idx + 8]
    primaries, transfer, matrix = struct.unpack(">HHH", raw[idx + 8 : idx + 14])
    return colour_type, primaries, transfer, matrix


@pytest.mark.parametrize("tone", TONE_CHOICES)
@pytest.mark.parametrize("ext", ["mp4", "mov"])
def test_basic_mode_writes_stream_tags_and_colr_box(
    tone: str, ext: str, tmp_path: Path, ffmpeg_tools: tuple[str, str]
) -> None:
    ffmpeg, ffprobe = ffmpeg_tools
    slug = tone.lower().replace(" ", "_")
    out = tmp_path / f"{slug}.{ext}"
    _encode(ffmpeg, out, tone, ocio=False)

    stream = _stream_tags(ffprobe, out)
    assert stream.get("color_transfer") == color_trc_for(tone)
    assert stream.get("color_primaries") == "bt709"
    assert stream.get("color_space") == "bt709"
    assert stream.get("color_range") == "tv"

    colour_type, primaries, transfer, matrix = _colr_cicp(out)
    assert colour_type == (b"nclx" if ext == "mp4" else b"nclc")
    assert primaries == 1  # BT.709
    assert transfer != 2  # explicit transfer code, never unspecified
    assert matrix == 1  # BT.709


@pytest.mark.parametrize("ext", ["mp4", "mov"])
def test_ocio_mode_tags_bt709_matrix_and_leaves_primaries_and_transfer_unspecified(
    ext: str, tmp_path: Path, ffmpeg_tools: tuple[str, str]
) -> None:
    ffmpeg, ffprobe = ffmpeg_tools
    out = tmp_path / f"ocio.{ext}"
    _encode(ffmpeg, out, "raw", ocio=True)

    stream = _stream_tags(ffprobe, out)
    assert stream.get("color_space") == "bt709"
    assert stream.get("color_range") == "tv"
    assert stream.get("color_primaries") is None
    assert stream.get("color_transfer") is None

    colour_type, primaries, transfer, matrix = _colr_cicp(out)
    assert colour_type == (b"nclx" if ext == "mp4" else b"nclc")
    assert (primaries, transfer, matrix) == (2, 2, 1)


def test_transforms_with_different_curves_get_different_tags(tmp_path: Path, ffmpeg_tools: tuple[str, str]) -> None:
    ffmpeg, ffprobe = ffmpeg_tools
    tags = {}
    for tone in ("sRGB", "rec709", "raw"):
        out = tmp_path / f"{tone}.mp4"
        _encode(ffmpeg, out, tone)
        tags[tone] = _stream_tags(ffprobe, out)["color_transfer"]

    assert len({tags["sRGB"], tags["rec709"], tags["raw"]}) == 3


def _red_frame_yuv(ffmpeg: str, out: Path, extra_args: list[str]) -> tuple[int, int, int]:
    """Encode one pure-red frame and read back its YUV sample."""
    subprocess.run(  # noqa: S603
        [
            ffmpeg,
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            "color=c=red:size=2x2:rate=1:duration=0.1",
            "-c:v",
            "rawvideo",
            *extra_args,
            "-pix_fmt",
            _PIX_FMT,
            "-frames:v",
            "1",
            "-y",
            str(out),
        ],
        check=True,
        capture_output=True,
        timeout=60,
    )
    raw = out.read_bytes()
    return raw[0], raw[4], raw[5]


@pytest.mark.parametrize("ocio", [False, True])
def test_matrix_conversion_uses_bt709_in_both_modes(ocio: bool, tmp_path: Path, ffmpeg_tools: tuple[str, str]) -> None:
    ffmpeg, _ = ffmpeg_tools
    untagged_yuv = _red_frame_yuv(ffmpeg, tmp_path / "untagged.yuv", [])
    colour_args = video_colour_args("sRGB", ocio=ocio)
    vf_args = colour_args[:2]  # ["-vf", "..."]
    converted_yuv = _red_frame_yuv(ffmpeg, tmp_path / f"converted_{ocio}.yuv", vf_args)

    assert untagged_yuv != converted_yuv
    # BT.709 limited-range red (255,0,0) -> Y=63, U=102, V=240.
    expected = (63, 102, 240)
    assert all(abs(a - b) <= 2 for a, b in zip(converted_yuv, expected, strict=True))
