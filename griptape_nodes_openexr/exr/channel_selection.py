"""Channel auto-selection for EXR display."""

from __future__ import annotations

from collections import Counter
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Sequence

    from griptape_nodes_openexr.exr.exr_types import EXRChannelInfo

_RGBA_RANK = {"R": 0, "G": 1, "B": 2, "A": 3}


def order_channels_for_display(channels: Sequence[EXRChannelInfo]) -> list[EXRChannelInfo]:
    """Order channels for display: grouped by layer, R, G, B, A first within each layer.

    EXR files store channels alphabetically (A, B, G, R). Layers keep their
    stored order; within a layer, non-RGBA channels follow RGBA in stored order.

    Args:
        channels: Channels in the order the file stores them.

    Returns:
        A new list in display order.
    """
    layer_order: dict[str, int] = {}
    for ch in channels:
        layer_order.setdefault(ch.name.rpartition(".")[0], len(layer_order))

    def sort_key(indexed: tuple[int, EXRChannelInfo]) -> tuple[int, int, int]:
        index, ch = indexed
        layer, _, suffix = ch.name.rpartition(".")
        return (layer_order[layer], _RGBA_RANK.get(suffix, len(_RGBA_RANK)), index)

    return [ch for _, ch in sorted(enumerate(channels), key=sort_key)]

# Layer name used for channels with no dot-separated prefix (R, G, B, A and friends).
# EXR calls these the root layer; the dropdown needs a label for them.
ROOT_LAYER = "(root)"


def select_display_channels(channel_names: list[str]) -> list[str] | None:
    """Select up to 3 channels to display as RGB, in priority order.

    Priority:
    1. Exact names R, G, B
    2. <prefix>.R, <prefix>.G, <prefix>.B from the most common layer prefix
    3. First 3 channels in order
    4. Single channel (grayscale)

    Args:
        channel_names: All channel names for the part.

    Returns:
        List of 1 or 3 channel names to display, or None if channel_names is empty.
    """
    if not channel_names:
        return None

    name_set = set(channel_names)

    # Priority 1: exact R, G, B
    if {"R", "G", "B"} <= name_set:
        return ["R", "G", "B"]

    # Priority 2: <prefix>.R, <prefix>.G, <prefix>.B from most common prefix
    rgb_result = _find_layer_rgb(channel_names)
    if rgb_result is not None:
        return rgb_result

    # Priority 3: first 3 channels
    if len(channel_names) >= 3:  # noqa: PLR2004
        return list(channel_names[:3])

    # Priority 4: single channel grayscale
    return [channel_names[0]]


def select_alpha_channel(channel_names: list[str], rgb_selected: list[str]) -> str | None:
    """Return the alpha channel paired with the selected RGB channels, or None.

    Only resolves alpha for exact-RGB and layer-prefix selections — first-3
    and grayscale fallbacks have no unambiguous alpha counterpart.

    Args:
        channel_names: All channel names for the part.
        rgb_selected: The RGB channel names returned by select_display_channels.

    Returns:
        Alpha channel name if found, otherwise None.
    """
    name_set = set(channel_names)

    if rgb_selected == ["R", "G", "B"]:
        return "A" if "A" in name_set else None

    if len(rgb_selected) == 3:  # noqa: PLR2004
        first = rgb_selected[0]
        if "." in first:
            prefix, _, suffix = first.rpartition(".")
            if suffix == "R" and rgb_selected[1:] == [f"{prefix}.G", f"{prefix}.B"]:
                candidate = f"{prefix}.A"
                return candidate if candidate in name_set else None

    return None


def layer_prefix(channel_name: str) -> str:
    """Return the layer a channel belongs to, or ROOT_LAYER when it has no prefix.

    The layer is everything before the final dot, so "char.beauty.R" belongs to
    "char.beauty" rather than "char".
    """
    prefix, _, _ = channel_name.rpartition(".")
    return prefix if prefix else ROOT_LAYER


def list_layers(channel_names: list[str]) -> list[str]:
    """List the distinct layers present in a part's channel names, in first-seen order.

    Args:
        channel_names: All channel names for the part.

    Returns:
        Layer names, with ROOT_LAYER standing in for undotted channels. Empty
        when channel_names is empty.
    """
    layers: list[str] = []
    seen: set[str] = set()
    for name in channel_names:
        layer = layer_prefix(name)
        if layer not in seen:
            seen.add(layer)
            layers.append(layer)
    return layers


def channels_in_layer(channel_names: list[str], layer: str) -> list[str]:
    """Return the channels belonging to `layer`, in their original order.

    Args:
        channel_names: All channel names for the part.
        layer: A layer name as returned by `list_layers`.

    Returns:
        The subset of channel_names in that layer, empty if the layer is unknown.
    """
    return [name for name in channel_names if layer_prefix(name) == layer]


def default_layer(channel_names: list[str]) -> str | None:
    """Return the layer `select_display_channels` would render by default.

    Keeps the layer dropdown's initial value consistent with what the node
    renders when the user has not chosen a layer.

    Args:
        channel_names: All channel names for the part.

    Returns:
        A layer name from `list_layers`, or None when channel_names is empty.
    """
    selected = select_display_channels(channel_names)
    if not selected:
        return None
    return layer_prefix(selected[0])


def _find_layer_rgb(channel_names: list[str]) -> list[str] | None:
    """Find <prefix>.R, <prefix>.G, <prefix>.B from the most common prefix."""
    # Collect prefixes from channels that have a dot-separated suffix of R, G, or B
    rgb_suffixes = {"R", "G", "B"}
    prefix_counts: Counter[str] = Counter()

    for name in channel_names:
        if "." in name:
            prefix, _, suffix = name.rpartition(".")
            if suffix in rgb_suffixes:
                prefix_counts[prefix] += 1

    if not prefix_counts:
        return None

    # Pick the prefix that has the most R/G/B channels (ties broken by first seen)
    best_prefix = prefix_counts.most_common(1)[0][0]
    r = f"{best_prefix}.R"
    g = f"{best_prefix}.G"
    b = f"{best_prefix}.B"

    name_set = set(channel_names)
    if {r, g, b} <= name_set:
        return [r, g, b]

    return None
