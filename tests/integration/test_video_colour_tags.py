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

from griptape_nodes_openexr.exr.tone_mapping import TONE_CHOICES
from griptape_nodes_openexr.nodes.exr_sequence_to_preview import _PIX_FMT, color_trc_for

run = pytest.importorskip("static_ffmpeg.run", reason="static_ffmpeg not installed")


@pytest.fixture(scope="module")
def ffmpeg_tools() -> tuple[str, str]:
    try:
        ffmpeg, ffprobe = run.get_or_fetch_platform_executables_else_raise()
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"ffmpeg unavailable: {exc}")
    return ffmpeg, ffprobe


def _encode(ffmpeg: str, out: Path, tone: str) -> None:
    """Encode a throwaway clip with the same colour flags the node uses."""
    subprocess.run(  # noqa: S603
        [
            ffmpeg,
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            "testsrc=duration=0.1:size=64x64:rate=10",
            "-c:v",
            "libx264",
            "-pix_fmt",
            _PIX_FMT,
            "-x264-params",
            f"colorprim=bt709:transfer={color_trc_for(tone)}:colormatrix=bt709",
            "-color_range",
            "tv",
            "-y",
            str(out),
        ],
        check=True,
        capture_output=True,
        timeout=120,
    )


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
