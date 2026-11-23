---
description: Renders an EXR part to an 8-bit RGB or RGBA PNG with its own exposure and display transform.
when_to_use: Use it to preview a part descriptor, such as one from Read EXR or Save EXR, with display settings that differ from where it came from.
---

## When to use it

You have an EXR part descriptor and want a picture of it. Typical sources are the `part` output of [Read EXR](ReadEXR.md) and the `output_part` of [Save EXR](SaveEXR.md), which lets you check a file you have just written. To build an image from individual channels, or to pick channels yourself, use [Display EXR Channel](DisplayEXRChannel.md).

The node chooses the channels itself, in the same order as Read EXR: undotted `R`, `G`, `B`, then the first layer with all three, then the first three channels, then a single channel as grey. If the chosen set has a matching `A`, the PNG is RGBA. Alpha is not changed by `exposure` or the display transform. A part with several layers shows the first layer that qualifies, so use Read EXR's `layer` or Display EXR Channel to look at another one.

The pixels are read again from the file named in the part, so the file has to still be on disk. The node does not check `openexr.max_render_pixels`.

After the node has run, **Open in external viewer** opens the source EXR in the configured viewer. See [Configure an external viewer](../troubleshooting.md#configure-an-external-viewer).

## Example

A compositor writes a denoised beauty with **Save EXR** and wants to confirm the result before sending it to review. Connect Save EXR's `output_part` to **Display EXR Part**, set `exposure` to 0, and compare the PNG with the original. Then raise `exposure` to check shadow detail.

## Parameters

### part

The part is assumed to hold scene-linear data. A part stored as log or display-referred values needs the right source colour space, which only `ocio` mode can set. See [Rendering and colour management](../rendering.md).

### window_mode

Shows every stored pixel, only the display window, or every pixel with the display window outlined. The part's own data and display windows are used. See [Display window](../rendering.md#display-window).

### color_mode

`basic` uses `tone_mapping` and runs inside the library. `ocio` needs a connected `color_params`, and fails the run without one. The default is `basic`, whether or not the OpenColorIO library is installed. An OCIO error, such as a missing config or an invalid view, fails the node and does not fall back to `basic`. See [OpenColorIO](../rendering.md#opencolorio).

### tone_mapping

The display transform in `basic` mode. No choice tone maps highlights, so values above 1.0 clip to white. Lower `exposure` or use `ocio` with a tone-mapped view. The ACES choices change primaries to Rec.709 first, and the others assume Rec.709 primaries already. See [Display transforms](../rendering.md#display-transforms).

### embed_color_profile

Adds an ICC profile for the display transform to the PNG. Hidden in `ocio` mode. See [Colour profiles](../rendering.md#colour-profiles).
