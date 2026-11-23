---
description: Deprecated. Loads an EXR file and exposes its header values, parts and channels as separate outputs. Use Read EXR for new workflows.
when_to_use: Use it only to keep an existing workflow running, or when you need header values or named part and channel connections that Read EXR does not provide yet.
---

## When to use it

This node is deprecated. Use [Read EXR](ReadEXR.md) for new file reading workflows. Load EXR stays available while the missing features are moved across, with no removal date. Existing workflows keep their parameters and connections and show a deprecation warning. The [migration guide](../migration.md) lists what Read EXR covers and when to keep Load EXR.

By default Load EXR reads only the file's structure, not its pixels. It reports header values such as compression, storage type, windows, time code, software, owner, chromaticities and custom attributes. It gives you a list of every part, and one output for each channel, ready to connect to [Display EXR Part](DisplayEXRPart.md), [Display EXR Channel](DisplayEXRChannel.md) or [Save EXR](SaveEXR.md).

After the node has run, **Open in external viewer** opens the file in the configured viewer. See [Configure an external viewer](../troubleshooting.md#configure-an-external-viewer).

## Parameters

### file_path

The header is scanned when the path changes, which creates the part and channel outputs. Single-part files get a flat **Channels** group. Multi-part files get one group per part, each holding the part and its channels, and no flat channel list. Channel outputs are ordered by layer with `R`, `G`, `B`, `A` first, not in the file's alphabetical order.

If the file has changed since the workflow was saved, the result details warn that channel connections may no longer be valid.

By default only the header is read, and the node shows a warning that every channel is labelled `half`. Set `openexr.header_only` to `false` and select the file again to read the true pixel types. This setting does not affect [Read EXR](ReadEXR.md), whose channel descriptors always say `half`. Load EXR with `header_only` set to `false` is the only way to see true pixel types.

### image_width

The width of the first part's data window. `image_height` is the height on the same basis. For the display window, use `display_window`. Read EXR reports the size of its rendered image instead.

### channel_count

The total number of channels across all parts. Read EXR reports only the part it read.
