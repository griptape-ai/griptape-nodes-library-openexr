"""Channel auto-selection for EXR display."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from griptape_nodes_openexr.exr.exr_header_artifact import EXRChannelArtifact
    from griptape_nodes_openexr.exr.exr_types import EXRChannelInfo

_RGBA_RANK = {"R": 0, "G": 1, "B": 2, "A": 3}
_SLOTS = ("R", "G", "B", "A")
_RGB_CHANNEL_COUNT = 3


def _role(channel_name: str) -> str:
    """Return a channel's suffix normalized for matching against R, G, B, A.

    The EXR convention is uppercase, but files from some tools use lowercase or
    pad names with whitespace, which EXR allows.
    """
    return channel_name.rpartition(".")[2].strip().upper()


def _layer_key(channel_name: str) -> str | None:
    """Return the layer a channel belongs to, None for undotted names so "R" and ".R" stay distinct."""
    return channel_name.rpartition(".")[0] if "." in channel_name else None


_RoleTable = dict[str, str]


def _index_layers(channel_names: list[str]) -> dict[str | None, tuple[_RoleTable, _RoleTable]]:
    """Map each layer, in first-seen order, to (exact, normalized) tables of role -> original name.

    The first channel to claim a role wins, except that in the normalized table
    an exact name always beats a case- or whitespace-variant.
    """
    layers: dict[str | None, tuple[_RoleTable, _RoleTable]] = {}
    for name in channel_names:
        exact, normalized = layers.setdefault(_layer_key(name), ({}, {}))
        exact.setdefault(name.rpartition(".")[2], name)
        normalized.setdefault(_role(name), name)
    for exact, normalized in layers.values():
        normalized.update({role: exact[role] for role in _RGBA_RANK if role in exact})
    return layers


def _rgb(roles: _RoleTable) -> list[str] | None:
    """Return the R, G, B names from a role table in that order, or None if any is missing."""
    r, g, b = roles.get("R"), roles.get("G"), roles.get("B")
    if r is None or g is None or b is None:
        return None
    return [r, g, b]


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
    2. <prefix>.R, <prefix>.G, <prefix>.B from the first layer that has all three
    3. First 3 channels in order
    4. Single channel (grayscale)

    Args:
        channel_names: All channel names for the part.

    Returns:
        List of 1 or 3 channel names to display, or None if channel_names is empty.
    """
    if not channel_names:
        return None

    layers = _index_layers(channel_names)
    root = layers.pop(None, None)
    for table in (0, 1):  # exact, then normalized
        candidates = [root[table]] if root else []
        candidates += [tables[table] for tables in layers.values()]
        for roles in candidates:
            rgb = _rgb(roles)
            if rgb is not None:
                return rgb

    # Priority 3: first 3 channels
    if len(channel_names) >= _RGB_CHANNEL_COUNT:
        return list(channel_names[:_RGB_CHANNEL_COUNT])

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
    if len(rgb_selected) != _RGB_CHANNEL_COUNT:
        return None

    tables = _index_layers(channel_names).get(_layer_key(rgb_selected[0]))
    if tables is None or _rgb(tables[1]) != rgb_selected:
        return None
    return tables[1].get("A")


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


def slots_from_channel_list(
    channels: Sequence[EXRChannelArtifact], *, grayscale_fills_rgb: bool = True
) -> dict[str, EXRChannelArtifact]:
    """Assign a channel list to R, G, B and A slots by channel name.

    RGB comes from `select_display_channels` and alpha from `select_alpha_channel`,
    the rules Read EXR uses to pick its default layer. A single-channel pick goes in
    R, and also in G and B when `grayscale_fills_rgb` is set, giving a grayscale image.
    When two entries share a name, the first one wins.

    Args:
        channels: Channel descriptors, such as Read EXR's `channels` output.
        grayscale_fills_rgb: Whether a single-channel pick fills G and B as well as R.

    Returns:
        Slot name to channel, in R, G, B, A order. Empty when `channels` is empty.
    """
    by_name: dict[str, EXRChannelArtifact] = {}
    for artifact in channels:
        by_name.setdefault(artifact.channel.name, artifact)
    names = list(by_name)
    selected = select_display_channels(names)
    if selected is None:
        return {}
    alpha = select_alpha_channel(names, selected)
    rgb = selected * _RGB_CHANNEL_COUNT if len(selected) == 1 and grayscale_fills_rgb else selected
    slots = {slot: by_name[name] for slot, name in zip("RGB", rgb, strict=False)}
    if alpha is not None:
        slots["A"] = by_name[alpha]
    return slots


def resolve_channel_slots(
    channels: Sequence[EXRChannelArtifact] | None,
    connected: Mapping[str, EXRChannelArtifact | None],
    *,
    grayscale_fills_rgb: bool = True,
) -> dict[str, EXRChannelArtifact]:
    """Combine a channel list with individually connected slots; a connected slot wins.

    Args:
        channels: The node's channel-list input, or None when it is not connected.
        connected: Slot name (R, G, B, A) to the channel connected to that slot, or None.
        grayscale_fills_rgb: Passed to `slots_from_channel_list`.

    Returns:
        Slot name to channel for every filled slot, in R, G, B, A order.
    """
    listed = slots_from_channel_list(channels or [], grayscale_fills_rgb=grayscale_fills_rgb)
    resolved = {slot: connected.get(slot) or listed.get(slot) for slot in _SLOTS}
    return {slot: artifact for slot, artifact in resolved.items() if artifact is not None}
