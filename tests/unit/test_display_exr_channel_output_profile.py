"""Unit tests for the embed/OCIO/background profile rule in display_exr_channel."""

from __future__ import annotations

from griptape_nodes_openexr.exr.icc_profiles import profile_for_tone_mapping
from griptape_nodes_openexr.nodes.display_exr_channel import _output_profile


class TestOutputProfile:
    def test_no_embed_gives_no_profile(self) -> None:
        assert _output_profile("rec709", embed=False, ocio=False, composited=False) is None

    def test_ocio_gives_no_profile(self) -> None:
        assert _output_profile("rec709", embed=True, ocio=True, composited=False) is None

    def test_no_profile_over_a_composited_background(self) -> None:
        assert _output_profile("rec709", embed=True, ocio=False, composited=True) is None

    def test_profile_when_embedding_without_background(self) -> None:
        assert _output_profile("rec709", embed=True, ocio=False, composited=False) == profile_for_tone_mapping("rec709")
