---
description: Reads an EXR file, or one frame of an EXR sequence, and outputs a viewable 8-bit preview with the part and channel data attached.
when_to_use: Use it to open a render, a plate or a single AOV layer for viewing, or as the starting point for channel work and saving.
---

## When to use it

You have an EXR file or a numbered sequence and want to look at it, pick a part or layer, or pass its channels on. This is the node to start with for any new EXR reading workflow. It replaces [Load EXR](LoadEXR.md) and [Load EXR Sequence](LoadEXRSequence.md). See the [migration guide](../migration.md) for the few things those nodes still do that this one does not.

The `image` output is a rendered 8-bit PNG, because the standard image nodes cannot open an EXR. The float pixels travel on `part` and `channels`, so [Display EXR Channel](DisplayEXRChannel.md) and [Save EXR](SaveEXR.md) still work from the real values. Use `image` for viewing and image nodes, and `part` and `channels` for HDR data.

The node reads one frame each time it runs. To go through a whole sequence, drive `frame` from a loop. See [Frame sequences](../sequences.md#iterating-a-sequence).

After the node has run, the **Open in external viewer** button opens the source EXR in the viewer set in `openexr.viewer_executable`, or the operating system default. See [Configure an external viewer](../troubleshooting.md#configure-an-external-viewer).

## Example

You have a lighting render of shot SH010 at `/shots/sh010/lighting/sh010_v003.####.exr`, written with one layer per AOV.

1. Add **Read EXR** and set `path` to the pattern, or pick the sequence in the file browser.
1. Set `layer` to `diffuse` to see the diffuse pass on its own.
1. Raise `exposure` by 1 or 2 stops to check the shadows.
1. Connect `image` to a **Display Image** node. To pull a single pass into a comp check, connect `channels` to **Get From List** and pick the channels you need.

## Parameters

### path

Takes one of three things:

- A single `.exr` file. This reads as a one-frame sequence.
- A frame pattern using `####`, `%04d` or `@@@@`, such as `render.####.exr`. Frame numbers with no file on disk are left out.
- A connected `Sequence`, such as the output of **Scan Sequence**. It is used as it is, with its own entries and missing-frame policy.

Connect **Scan Sequence** when you want to choose how gaps are handled. A plain pattern uses the default of skipping absent frames.

### part_name

Appears after the file is scanned, and only when the file has more than one part. Each choice is the part number followed by its name, such as `0: rgba` or `1: depth`. Changing the part refreshes the `layer` choices.

### layer

Appears after the scan, and only when the selected part has more than one layer. A layer is the channel name up to the last dot, so `char.beauty.R` belongs to the layer `char.beauty`. Channels with no dot, such as `R`, `G`, `B`, belong to `(root)`.

The default is the layer the node would pick on its own: undotted `R`, `G`, `B` first, then the first layer that has all three. Without a full RGB set it falls back to the first three channels, then to a single channel shown as grey.

The preview shows the three colour channels of the chosen layer. When that layer has an `A` channel, the preview is RGBA. Alpha is not affected by `exposure` or the display transform.

### frame

The frame number as written in the file names, so a sequence that runs from 1001 to 1100 starts at 1001. It appears after the scan, and only for a sequence. A value outside the sequence is clamped to the nearest end, and the result details report this. A number inside the range that has no file fails, naming the frame. Frame 0 is treated as unset and reads the first frame.

While a connection drives `frame`, the slider is disabled but follows the current frame.

### window_mode

Chooses whether the preview shows every stored pixel, the display window only, or every pixel with the display window outlined. See [Display window](../rendering.md#display-window). Only the preview changes. `part` and `channels` keep the data window and the stored pixels.

### color_mode

`basic` applies `exposure` and the `tone_mapping` transform inside the library. `ocio` uses a connected `color_params` from the OpenColorIO library, set up as described in [OpenColorIO](../rendering.md#opencolorio). The default is `basic`, whether or not the OpenColorIO library is installed. Choosing `ocio` without a connected `color_params` fails the run.

### tone_mapping

The display transform used in `basic` mode. The four `Linear sRGB` and `raw` choices assume scene-linear data with Rec.709 primaries. The three ACES choices first convert ACEScg or ACES2065-1 primaries to Rec.709, then apply the named encoding. Values above 1.0 clip in every choice. See [Display transforms](../rendering.md#display-transforms).

### embed_color_profile

Writes an ICC profile that names the display transform into the PNG. Turn it on for files that other applications will open. See [Colour profiles](../rendering.md#colour-profiles). Hidden in `ocio` mode.

### output_file

The file name for the saved preview PNG, resolved through the project's output settings. For a sequence the frame number is passed to the project's output template, so a loop can write one file per frame.

### image_width

The width of the rendered `image`. In `Crop Overscan` mode this is the display window. In the other modes it is the data window. The `part` output keeps the data window's size whatever the mode.

### image_height

The height of the rendered `image`, on the same basis as `image_width`.

### channels

One descriptor per channel in the selected part, ordered by layer with `R`, `G`, `B`, `A` first inside each layer. The file stores channels alphabetically, so this order differs from the file's. Pick single channels out of the list with **Get From List**.
