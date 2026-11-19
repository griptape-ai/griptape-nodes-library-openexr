# Frame sequences

## Iterating a sequence

**Read EXR** emits one frame per execution. Use a graph loop to iterate frames. Wire **For Loop Start**'s `index` output into `frame` and drive the loop from `first_frame` and `last_frame`:

```
Read EXR.first_frame -> For Loop Start.start
Read EXR.last_frame  -> For Loop Start.end
For Loop Start.index -> Read EXR.frame
```

While a connection drives `frame`, the slider is disabled but continues to show the current frame during playback. Output filenames include the frame number (`read_exr.1001.png`), producing numbered files during a loop.

An out-of-range `frame` clamps to the sequence bounds and reports this in the result details. A frame absent from the sequence's entries fails with an error naming the frame. The scan has already applied your `MissingItemPolicy`; Read EXR does not apply another missing-frame policy. Use `FILL_NEAREST` at the scan to fill gaps during a continuous frame-number loop. With `SKIP`, iterate available sequence entries; requesting a skipped frame fails.

## Discovery and failed frames

Read EXR's `path` and EXR Sequence to Preview's `sequence` accept `####`, `%04d` and `@@@@` frame patterns, a single file path, or a connected `Sequence`. Select a file or sequence through the File Browser, or type the path or pattern directly. **Scan Sequence** is optional. A connected sequence retains its entries and missing-frame policy. A single file produces a one-frame sequence or video.

Path discovery skips absent frame numbers, shortening the video. Connect **Scan Sequence** to choose a different discovery policy. In EXR Sequence to Preview, `missing_frames` controls discovered entries that fail conversion:

- `skip` (default) omits the failed frame.
- `error` fails the run.
- `black` inserts a black replacement to retain its timing.

Invalid patterns and paths with no matching frames report a failure in `result_details`. Canvas-policy failures always stop the run, regardless of `missing_frames`.

For the deprecated Load EXR Sequence node's batch mismatch validation and header scans, see the [node reference](nodes.md#load-exr-sequence) and [migration guide](migration.md).

## Sequence canvas policies

Both MP4 and MOV use H.264 with `yuv420p`, which requires even width and height. `odd_dimensions=Pad to Even` adds black pixels after colour processing, preserving the rendered image at the top-left. `Require Even` reports the odd dimensions and stops before encoding.

`frame_sizes=Require Matching Sizes` compares successful frames before even padding and reports conflicting source paths and dimensions. Choose `Pad to Largest` to preserve each frame without resizing or cropping. It uses the maximum width and maximum height across successful frames, then applies the odd-dimension policy. Padding is black on the right and bottom. This aligns local rendered canvases; it does not align differing EXR origins. Use `Crop Overscan` with a shared display window to align changing EXR crops by coordinates.

Failed-frame black replacements use the final canvas dimensions. Size-policy failures and renders above `openexr.max_render_pixels` fail the sequence even when `missing_frames=skip` or `black`. Result details report final dimensions, policies and how many successful frames were padded.

The shared [Library Settings](../README.md#library-settings) render limit applies to intermediate rendered frames and the final padded canvas. Padding processes one frame at a time, but concurrent EXR conversion workers still require memory for their own buffers.
