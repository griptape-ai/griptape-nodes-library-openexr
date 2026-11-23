---
description: Combines one to four EXR channels into an 8-bit RGB or RGBA PNG, with channel alignment options and optional alpha compositing over a background.
when_to_use: Use it to look at a single AOV or data channel, to assemble a picture from channels taken from different parts or files, or to preview a matte over a plate.
---

## When to use it

You want to choose exactly which channels make the picture, instead of letting the node pick them. Connect up to four channels to the red, green, blue and alpha slots. Each slot is optional, and the channels can come from different parts or different files. At least one of `channel_r`, `channel_g` and `channel_b` must be connected. A missing colour slot is filled with zero.

Get channel descriptors from the `channels` output of [Read EXR](ReadEXR.md) with **Get From List**. To show a whole part with automatic channel choice, use [Display EXR Part](DisplayEXRPart.md).

After the node has run, **Open in external viewer** opens the source EXR of the first connected colour channel. See [Configure an external viewer](../troubleshooting.md#configure-an-external-viewer).

The size of the render is checked against `openexr.max_render_pixels`, including the intermediate canvas. See [Render limits and memory](../troubleshooting.md#render-limits-and-memory).

## Example

Look at a depth pass: connect the depth channel to `channel_r` and leave the rest empty. The pass shows as a red image. Lower `exposure` if the far values clip.

To check a character against its background, connect the beauty's `R`, `G`, `B` and the matte's channel to `channel_a`, then connect the plate to `background`.

## Parameters

### channel_r

The channel for the red plane. The channel can be any EXR channel, such as a depth value or a normal component, and is shown as red. The same applies to `channel_g` and `channel_b` for their planes.

### channel_a

The channel used as alpha. With no `background`, the PNG is RGBA and the alpha is stored as it is, without exposure or a display transform. With a `background`, the result is opaque RGB. Connecting alpha does not count as a colour channel for the "at least one" rule.

### window_mode

Shows every stored pixel, the frame only, or every pixel with the frame outlined. The frame comes from the source chosen in `frame_reference`. With `Match EXR Coordinates`, `Full` and `Show Overscan Boundary` cover the bounding rectangle of all connected data windows, alpha included. See [Display window](../rendering.md#display-window).

### channel_alignment

Decides how channels with different windows or sizes are placed. It never resamples a channel.

- `Match Pixel Positions` is the default. It lines up the top-left stored pixels of all channels and ignores the other channels' data-window offsets. All channels must have the same array size, or the run fails. The reference source supplies the origin and the intended frame.
- `Match EXR Coordinates` places each channel by its own data window, so channels of different sizes and origins can be combined. Areas a channel does not cover are filled with zero. `Crop Overscan` renders the reference display window directly.
- `Require Matching Windows` fails if any connected channel, alpha included, has a different data window or display window from the reference. The error names the slot.

The last two need channels with one sample per pixel. A subsampled channel fails with an error that names its slot.

For example, take a beauty whose data window starts at (-8, -8) and a matte that starts at (0, 0). `Match EXR Coordinates` lines up their positions in the frame. `Match Pixel Positions` lines up their stored arrays instead, which shifts the matte by 8 pixels. The result details report the policy and the reference used.

### frame_reference

Picks the channel whose display window defines the intended frame. `First Connected RGB` uses `R`, then `G`, then `B`. A slot you choose explicitly has to be connected. With `Match Pixel Positions`, the same source also supplies the origin. Use `A` when a matte defines the frame, and `R` when the beauty should.

### color_mode

`basic` uses `tone_mapping`. `ocio` needs a connected `color_params`. The default is `basic`, whether or not the OpenColorIO library is installed. See [OpenColorIO](../rendering.md#opencolorio).

### tone_mapping

The display transform in `basic` mode. Highlights above 1.0 clip. See [Display transforms](../rendering.md#display-transforms).

### embed_color_profile

Adds an ICC profile for the display transform to the PNG. Hidden in `ocio` mode. See [Colour profiles](../rendering.md#colour-profiles).

### background

An image placed behind the channels where alpha is below 1. It is used only when `channel_a` is connected, and is ignored otherwise. The image is stretched to the size of the rendered picture, with no regard for its aspect ratio, and is read as sRGB.

In `basic` mode the blend happens in linear light, then the display transform is applied. With `Linear sRGB to sRGB` the background looks unchanged. `rec709` and `rec1886` shift it slightly, and `raw` makes it look much darker, unless the viewer applies an embedded profile. With an ACES choice the background is converted into the ACES primaries first, so the gamut conversion returns it unchanged. In `ocio` mode the blend happens after the OCIO transform, on the display-encoded result.
