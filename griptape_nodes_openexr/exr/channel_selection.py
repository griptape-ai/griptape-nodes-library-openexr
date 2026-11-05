"""Channel auto-selection for EXR display."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Sequence

    from griptape_nodes_openexr.exr.exr_types import EXRChannelInfo

_RGBA_RANK = {"R": 0, "G": 1, "B": 2, "A": 3}


def _role(channel_name: str) -> str:
    """Return a channel's suffix normalized for matching against R, G, B, A.

    The EXR convention is uppercase, but files from some tools use lowercase or
    pad names with whitespace, which EXR allows.
    """
    return channel_name.rpartition(".")[2].strip().upper()


def _find_role(candidates: list[str], role: str, *, normalize: bool) -> str | None:
    """Return the candidate whose suffix is `role`.

    An exact match wins; with `normalize`, a case- or whitespace-variant is the fallback.
    """
    exact = next((name for name in candidates if name.rpartition(".")[2] == role), None)
    if exact is not None or not normalize:
        return exact
    return next((name for name in candidates if _role(name) == role), None)


def _find_rgb(candidates: list[str], *, normalize: bool) -> list[str] | None:
    """Return the R, G, B channels among `candidates` in that order, or None if any is missing."""
    found = [_find_role(candidates, role, normalize=normalize) for role in ("R", "G", "B")]
    if None in found:
        return None
    return [name for name in found if name is not None]


def _same_layer(channel_names: list[str], channel_name: str) -> list[str]:
    """Return the channels in the same layer as `channel_name`, treating undotted and dotted names as distinct."""
    if "." not in channel_name:
        return [name for name in channel_names if "." not in name]
    prefix = channel_name.rpartition(".")[0]
    return [name for name in channel_names if "." in name and name.rpartition(".")[0] == prefix]


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
        layer = ch.name.rpartition(".")[0]
        return (layer_order[layer], _RGBA_RANK.get(_role(ch.name), len(_RGBA_RANK)), index)

    return [ch for _, ch in sorted(enumerate(channels), key=sort_key)]


# Layer name used for channels with no dot-separated prefix (R, G, B, A and friends).
# EXR calls these the root layer; the dropdown needs a label for them.
ROOT_LAYER = "(root)"


def select_display_channels(channel_names: list[str]) -> list[str] | None:
    """Select up to 3 channels to display as RGB, in priority order.

    Priorities 1 and 2 are tried with exact names first, then again matching
    R, G, B case-insensitively and ignoring surrounding whitespace, so a
    conforming layer always wins over a non-conforming one.

    Priority:
    1. Undotted names R, G, B
    2. <prefix>.R, <prefix>.G, <prefix>.B from the layer with the most of them
    3. First 3 channels in order
    4. Single channel (grayscale)

    Args:
        channel_names: All channel names for the part.

    Returns:
        List of 1 or 3 channel names to display, or None if channel_names is empty.
    """
    if not channel_names:
        return None

    root_names = [name for name in channel_names if "." not in name]
    for normalize in (False, True):
        # Priority 1: undotted R, G, B
        root_rgb = _find_rgb(root_names, normalize=normalize)
        if root_rgb is not None:
            return root_rgb

        # Priority 2: <prefix>.R, <prefix>.G, <prefix>.B from the layer with the most of them
        rgb_result = _find_layer_rgb(channel_names, normalize=normalize)
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
    if len(rgb_selected) != 3:  # noqa: PLR2004
        return None

    layer = _same_layer(channel_names, rgb_selected[0])
    if _find_rgb(layer, normalize=True) != rgb_selected:
        return None

    return _find_role(layer, "A", normalize=True)


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


def _find_layer_rgb(channel_names: list[str], *, normalize: bool) -> list[str] | None:
    """Find <prefix>.R, <prefix>.G, <prefix>.B from the prefix with the most of them."""
    # Collect the distinct R/G/B roles each dot-prefixed layer has
    rgb_roles = {"R", "G", "B"}
    prefix_roles: dict[str, set[str]] = {}

    for name in channel_names:
        if "." in name:
            prefix, _, suffix = name.rpartition(".")
            role = _role(name) if normalize else suffix
            if role in rgb_roles:
                prefix_roles.setdefault(prefix, set()).add(role)

    if not prefix_roles:
        return None

    # Pick the prefix that has the most R/G/B channels (ties broken by first seen)
    best_prefix = max(prefix_roles, key=lambda prefix: len(prefix_roles[prefix]))
    layer = [name for name in channel_names if "." in name and name.rpartition(".")[0] == best_prefix]
    return _find_rgb(layer, normalize=normalize)
