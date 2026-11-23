---
description: Encodes an EXR frame sequence to an MP4 or MOV preview video, with exposure and a display transform or OpenColorIO view applied to every frame.
when_to_use: Use it to make a review video from a render, such as a daily or a quick check of a lighting pass.
---

## When to use it

You have a numbered EXR sequence and want one video to play back or send for review. The node converts every frame to 8-bit with the same exposure and colour settings, then encodes H.264 with ffmpeg. Use [Read EXR](ReadEXR.md) instead when you want to examine single frames.

Enter a path or frame pattern in `sequence`, pick one in the file browser, or connect a `Sequence` from **Scan Sequence** or **Scan Split Sequence**. A connected sequence wins over a typed path and keeps its entries and missing-frame policy. See [Frame sequences](../sequences.md#discovery-and-failed-frames).

The video shows RGB only. The node picks the channels itself, in the same order as Read EXR, and alpha is not encoded. Every frame must be readable at the same part. Encoding stops with an error if ffmpeg takes longer than 10 minutes.

## Example

Set `sequence` to `/shots/sh010/lighting/sh010_v003.####.exr` and `frame_rate` to `24`. Leave `format` as `mp4` and run. The video is saved to the project's output location. If the render has a slate frame at a different size, see `frame_sizes` below.

## Parameters

### sequence

Accepts `####`, `%04d` and `@@@@` patterns, a single file, or a connected `Sequence`. A single file gives a one-frame video. A pattern skips frame numbers that are absent, so gaps shorten the video. Connect **Scan Sequence** to choose another discovery policy. An invalid pattern, or one that matches no files, fails with a message in the result details.

### part_index

The part to read from every frame, counting from 0. A frame that does not have this part fails to convert and is handled by `missing_frames`.

### window_mode

Each frame is framed by its own data and display window. See [Display window](../rendering.md#display-window). Use `Crop Overscan` when the display window stays constant but the data window changes from frame to frame, so all frames come out at one size.

### odd_dimensions

H.264 with `yuv420p` needs even width and height. `Pad to Even` adds at most one black column on the right and one black row at the bottom. `Require Even` stops before encoding and reports the odd size. See [Sequence canvas policies](../sequences.md#sequence-canvas-policies).

### frame_sizes

`Require Matching Sizes` fails when rendered frames differ in size, and names the paths and sizes involved. `Pad to Largest` pads every frame with black on the right and bottom up to the largest width and height. It does not resize, crop or align different EXR origins. The canvas, including padding, is checked against `openexr.max_render_pixels`.

### missing_frames

Applies to frames that fail to convert after discovery. A size mismatch under `Require Matching Sizes`, an odd size under `Require Even`, and a canvas over the render limit always stop the run, whatever this is set to. With `black`, the replacement frames use the final canvas size.

### color_mode

`basic` uses `tone_mapping`. `ocio` needs a connected `color_params`. The default is `basic`. See [OpenColorIO](../rendering.md#opencolorio). The video has no `embed_color_profile` option. It is tagged for video players instead. See [Colour profiles](../rendering.md#colour-profiles).

### tone_mapping

The display transform in `basic` mode. The video is tagged with Rec.709 primaries, the BT.709 matrix and a transfer characteristic matching the transform. `rec1886` is tagged as BT.709 because video has no tag for gamma 2.4. In `ocio` mode only the BT.709 matrix is tagged. See [Display transforms](../rendering.md#display-transforms).

### format

`mp4` and `mov` both contain H.264. The file name in `output_file` is not changed to match, so rename it when you choose `mov`.

### processing_speed

`fast` encodes quickly at lower quality. `balanced` is the default. `quality` is slower and gives higher quality. Use `fast` for quick checks and `quality` for a video you will share.

### output_file

The file name for the video, `preview.mp4` by default.

### threads

How many frames are converted at once, from 1 to 16. Each worker holds its own float buffers, so lower this if a large sequence uses too much memory. See [Render limits and memory](../troubleshooting.md#render-limits-and-memory).

### frames_processed

Counts frames finished so far, including ones that failed, and updates while the node runs.
