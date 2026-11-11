"""The MP4 colour tags, verified against the real ffmpeg binary.

A video carries no ICC profile, so these tags are its only colour signalling.
The unit tests assert the command we build; these assert ffmpeg actually
honours it. That distinction matters here: this build accepts ffmpeg's own
-color_trc without error and then drops it, which only a real encode reveals.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
from static_ffmpeg import run  # type: ignore[import-untyped]

from griptape_nodes_openexr.exr.tone_mapping import TONE_CHOICES
from griptape_nodes_openexr.nodes.exr_sequence_to_preview import _PIX_FMT, color_trc_for


@pytest.fixture(scope="module")
def ffmpeg_tools() -> tuple[str, str]:
    # static_ffmpeg is a runtime dependency, so a failed fetch fails the test
    # rather than skipping it: a skip here would pass CI with nothing checked.
    ffmpeg, ffprobe = run.get_or_fetch_platform_executables_else_raise()
    return ffmpeg, ffprobe


def _encode(ffmpeg: str, out: Path, tone: str, *, convert_matrix: bool = True) -> None:
    """Encode a throwaway clip with the same colour flags the node uses."""
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
    ]
    if convert_matrix:
        cmd += ["-vf", "scale=out_color_matrix=bt709"]
    cmd += [
        "-pix_fmt",
        _PIX_FMT,
        "-x264-params",
        f"colorprim=bt709:transfer={color_trc_for(tone)}:colormatrix=bt709",
        "-color_range",
        "tv",
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


@pytest.mark.parametrize("tone", TONE_CHOICES)
def test_tags_are_written_to_the_file(tone: str, tmp_path: Path, ffmpeg_tools: tuple[str, str]) -> None:
    ffmpeg, ffprobe = ffmpeg_tools
    out = tmp_path / f"{tone.lower()}.mp4"
    _encode(ffmpeg, out, tone)

    stream = _stream_tags(ffprobe, out)
    # "unknown" is what a silently dropped flag looks like, so assert the
    # value rather than merely that the key exists.
    assert stream.get("color_transfer") == color_trc_for(tone)
    assert stream.get("color_primaries") == "bt709"
    assert stream.get("color_space") == "bt709"


def test_transforms_with_different_curves_get_different_tags(tmp_path: Path, ffmpeg_tools: tuple[str, str]) -> None:
    ffmpeg, ffprobe = ffmpeg_tools
    tags = {}
    for tone in ("sRGB", "rec709", "raw"):
        out = tmp_path / f"{tone}.mp4"
        _encode(ffmpeg, out, tone)
        tags[tone] = _stream_tags(ffprobe, out)["color_transfer"]

    assert len({tags["sRGB"], tags["rec709"], tags["raw"]}) == 3


def _red_frame_yuv(ffmpeg: str, out: Path, *, convert_matrix: bool) -> tuple[int, int, int]:
    """Encode one pure-red frame and read back its YUV sample via ffprobe."""
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
            *(["-vf", "scale=out_color_matrix=bt709"] if convert_matrix else []),
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
    # yuv420p, 2x2: Y plane is 4 bytes, then one U byte and one V byte.
    y, u, v = raw[0], raw[4], raw[5]
    return y, u, v


def test_matrix_conversion_matches_the_asserted_tag(tmp_path: Path, ffmpeg_tools: tuple[str, str]) -> None:
    # colormatrix=bt709 only tags the stream; ffmpeg's default RGB->YUV
    # conversion uses BT.601 regardless, so an untagged encode and a BT.709
    # encode of the same red frame must land on different YUV samples, and
    # the explicit conversion must match the known BT.709 coefficients.
    ffmpeg, _ = ffmpeg_tools
    untagged = tmp_path / "untagged.yuv"
    converted = tmp_path / "converted.yuv"

    untagged_yuv = _red_frame_yuv(ffmpeg, untagged, convert_matrix=False)
    converted_yuv = _red_frame_yuv(ffmpeg, converted, convert_matrix=True)

    assert untagged_yuv != converted_yuv
    # BT.709 full-range red (255,0,0) -> Y=63, U=102, V=240 (tv range); allow
    # a small margin for encoder rounding.
    expected = (63, 102, 240)
    assert all(abs(a - b) <= 2 for a, b in zip(converted_yuv, expected, strict=True))
