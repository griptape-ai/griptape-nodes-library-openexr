"""Unit tests for channel auto-selection logic."""

from __future__ import annotations

from griptape_nodes_openexr.exr.channel_selection import (
    order_channels_for_display,
    select_alpha_channel,
    select_display_channels,
)
from griptape_nodes_openexr.exr.exr_types import EXRChannelInfo, PixelType


def _ordered_names(names: list[str]) -> list[str]:
    channels = [EXRChannelInfo(name=n, pixel_type=PixelType.HALF, x_sampling=1, y_sampling=1) for n in names]
    return [ch.name for ch in order_channels_for_display(channels)]


class TestExactRGB:
    def test_exact_rgb_selected(self) -> None:
        result = select_display_channels(["R", "G", "B", "A"])
        assert result == ["R", "G", "B"]

    def test_exact_rgb_without_alpha(self) -> None:
        result = select_display_channels(["R", "G", "B"])
        assert result == ["R", "G", "B"]

    def test_exact_rgb_priority_over_layered(self) -> None:
        # R/G/B present alongside beauty.R etc — exact wins
        result = select_display_channels(["beauty.R", "beauty.G", "beauty.B", "R", "G", "B"])
        assert result == ["R", "G", "B"]


class TestLayerPrefix:
    def test_single_layer_rgb(self) -> None:
        result = select_display_channels(["beauty.R", "beauty.G", "beauty.B"])
        assert result == ["beauty.R", "beauty.G", "beauty.B"]

    def test_most_common_layer_wins(self) -> None:
        # beauty has R/G/B; depth only has Z — beauty should win
        channels = ["beauty.R", "beauty.G", "beauty.B", "depth.Z"]
        result = select_display_channels(channels)
        assert result == ["beauty.R", "beauty.G", "beauty.B"]

    def test_layer_with_extra_channels(self) -> None:
        # beauty has R/G/B/A — should still find the RGB triple
        result = select_display_channels(["beauty.R", "beauty.G", "beauty.B", "beauty.A"])
        assert result == ["beauty.R", "beauty.G", "beauty.B"]

    def test_layer_prefix_priority_over_first_three(self) -> None:
        # Layer with R/G/B should win over first-3 fallback
        result = select_display_channels(["X", "Y", "diffuse.R", "diffuse.G", "diffuse.B"])
        assert result == ["diffuse.R", "diffuse.G", "diffuse.B"]


class TestFirstThree:
    def test_first_three_channels(self) -> None:
        result = select_display_channels(["X", "Y", "Z"])
        assert result == ["X", "Y", "Z"]

    def test_first_three_when_no_rgb_layer(self) -> None:
        result = select_display_channels(["normal.X", "normal.Y", "normal.Z"])
        # no .R/.G/.B suffix — falls back to first-3
        assert result == ["normal.X", "normal.Y", "normal.Z"]

    def test_first_three_from_longer_list(self) -> None:
        result = select_display_channels(["A", "B", "C", "D", "E"])
        assert result == ["A", "B", "C"]


class TestGrayscale:
    def test_single_channel_grayscale(self) -> None:
        result = select_display_channels(["Z"])
        assert result == ["Z"]

    def test_single_channel_named_y(self) -> None:
        result = select_display_channels(["Y"])
        assert result == ["Y"]


class TestEmpty:
    def test_empty_returns_none(self) -> None:
        result = select_display_channels([])
        assert result is None


class TestReturnOrder:
    def test_rgb_order_preserved_for_layer(self) -> None:
        # Channels may arrive in any order; R/G/B should be returned R, G, B
        result = select_display_channels(["beauty.B", "beauty.G", "beauty.R"])
        assert result == ["beauty.R", "beauty.G", "beauty.B"]

    def test_exact_rgb_order(self) -> None:
        result = select_display_channels(["B", "G", "R", "A"])
        assert result == ["R", "G", "B"]


class TestAlphaSelection:
    def test_exact_rgba_returns_a(self) -> None:
        assert select_alpha_channel(["R", "G", "B", "A"], ["R", "G", "B"]) == "A"

    def test_exact_rgb_no_alpha_returns_none(self) -> None:
        assert select_alpha_channel(["R", "G", "B"], ["R", "G", "B"]) is None

    def test_layer_rgba_returns_prefix_a(self) -> None:
        assert (
            select_alpha_channel(
                ["beauty.R", "beauty.G", "beauty.B", "beauty.A"],
                ["beauty.R", "beauty.G", "beauty.B"],
            )
            == "beauty.A"
        )

    def test_layer_rgb_no_alpha_returns_none(self) -> None:
        assert (
            select_alpha_channel(
                ["beauty.R", "beauty.G", "beauty.B"],
                ["beauty.R", "beauty.G", "beauty.B"],
            )
            is None
        )

    def test_first_three_fallback_returns_none(self) -> None:
        assert select_alpha_channel(["X", "Y", "Z", "A"], ["X", "Y", "Z"]) is None

    def test_grayscale_fallback_returns_none(self) -> None:
        assert select_alpha_channel(["Z"], ["Z"]) is None

    def test_mismatched_prefix_alpha_ignored(self) -> None:
        assert (
            select_alpha_channel(
                ["beauty.R", "beauty.G", "beauty.B", "diffuse.A"],
                ["beauty.R", "beauty.G", "beauty.B"],
            )
            is None
        )


class TestOrderChannelsForDisplay:
    def test_alphabetical_rgba_becomes_rgba(self) -> None:
        assert _ordered_names(["A", "B", "G", "R"]) == ["R", "G", "B", "A"]

    def test_extra_channels_follow_rgba_in_stored_order(self) -> None:
        assert _ordered_names(["A", "B", "G", "R", "Z", "N"]) == ["R", "G", "B", "A", "Z", "N"]

    def test_layers_keep_stored_order_and_sort_within(self) -> None:
        names = ["A", "B", "G", "R", "beauty.A", "beauty.B", "beauty.G", "beauty.R", "depth.Z"]
        assert _ordered_names(names) == [
            "R", "G", "B", "A",
            "beauty.R", "beauty.G", "beauty.B", "beauty.A",
            "depth.Z",
        ]  # fmt: skip

    def test_interleaved_layers_are_regrouped(self) -> None:
        names = ["A", "B", "Beauty.A", "Beauty.B", "Beauty.G", "Beauty.R", "G", "R"]
        assert _ordered_names(names) == ["R", "G", "B", "A", "Beauty.R", "Beauty.G", "Beauty.B", "Beauty.A"]

    def test_non_rgba_layer_unchanged(self) -> None:
        assert _ordered_names(["X", "Y", "Z"]) == ["X", "Y", "Z"]

    def test_empty(self) -> None:
        assert _ordered_names([]) == []
