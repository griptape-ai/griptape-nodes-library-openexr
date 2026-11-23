---
title: OpenEXR Library
description: Nodes for reading, previewing and writing OpenEXR images and frame sequences in VFX and HDR workflows.
when_to_use: Use it when a workflow handles EXR renders, plates or AOVs and needs to view them, work with their channels, make a review video or save EXR files.
---

The OpenEXR library reads [OpenEXR](https://openexr.com/) files and frame sequences, shows their parts and layers as viewable images, assembles individual channels into pictures, encodes sequences as review videos, and saves images or float channel data back to EXR. Its nodes are in the **EXR** category.

Pictures made from EXR data are 8-bit PNGs. The float pixels stay on the part and channel outputs, so channel work and saving keep the HDR values. [Read EXR](nodes/ReadEXR.md) is the node to start with.

## Requirements and install

- Griptape Nodes engine 0.104.0 or later.
- The library installs its own Python dependencies: `numpy`, `OpenEXR`, `pillow` and `static-ffmpeg`.
- The [OpenColorIO library](https://github.com/griptape-ai/griptape-nodes-library-opencolorio) is optional. Install it for pipeline-specific colour transforms. Everything else works without it.

To install, open **Manage > Library Management** in Griptape Nodes, click **Add Library**, paste `https://github.com/griptape-ai/griptape-nodes-library-openexr`, click **Install**, then **Download** in the confirmation dialog. Where library installation is not available, ask your administrator to install it.

## Nodes

- [Read EXR](nodes/ReadEXR.md): open a file or one frame of a sequence, pick a part or layer, and get a preview plus the part and channel data.
- [Display EXR Part](nodes/DisplayEXRPart.md): render a part descriptor with its own exposure and display transform.
- [Display EXR Channel](nodes/DisplayEXRChannel.md): build a picture from one to four chosen channels, align them, and composite over a background.
- [Save EXR](nodes/SaveEXR.md): write channels or an 8-bit image to a single-part EXR.
- [EXR Sequence to Preview](nodes/EXRSequenceToPreview.md): encode a sequence as an MP4 or MOV review video.
- [Load EXR](nodes/LoadEXR.md) and [Load EXR Sequence](nodes/LoadEXRSequence.md): deprecated. They remain for workflows that need header values, descriptor lists or batch validation. See the [migration guide](migration.md).

## Guides

- [Rendering and colour management](rendering.md): display transforms, data and display windows, colour profiles and OpenColorIO.
- [Frame sequences](sequences.md): iterating frames, discovery and failed frames, and video sizing.
- [Migration](migration.md): moving from the deprecated load nodes.
- [Troubleshooting](troubleshooting.md): display, sequence, render-limit and external-viewer problems.
- [Node reference](nodes.md): every parameter and output in one page.

## Library settings

Set these in the engine's `openexr` category.

- `openexr.header_only` (default `true`): Load EXR scans headers only and labels every channel `half`. Read EXR ignores this setting. Its `part` and `channels` outputs always come from a header scan and label every channel `half` too, so Load EXR with `header_only` set to `false` is the only way to see true pixel types.
- `openexr.max_render_pixels` (default `67108864`): the largest canvas Display EXR Channel and EXR Sequence to Preview will render, including video padding. See [Render limits and memory](troubleshooting.md#render-limits-and-memory).
- `openexr.viewer_executable` (default empty): path to an external HDR viewer. Empty uses the operating system default for EXR files.
- `openexr.viewer_args` (default empty): arguments placed before the file path.

The library also registers an EXR artifact provider, so the editor can show previews of `.exr` files.
