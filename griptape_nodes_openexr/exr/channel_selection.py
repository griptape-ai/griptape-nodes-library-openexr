"""Channel auto-selection for EXR display."""

from __future__ import annotations

from collections import Counter


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
