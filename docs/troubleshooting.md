# Troubleshooting

## Highlights Are White Or The Image Looks Dark

Basic display transforms clip scene values above 1.0. Lower `exposure` to bring
highlights into range, or use OpenColorIO with a tone-mapped view such as ACES.
`raw` applies no display encoding and looks dark on screen.
See [display transforms](rendering.md#display-transforms).

If a PNG looks different in another application, check `embed_color_profile`.
Without a profile, viewers assume sRGB even when the pixels use another curve.
See [colour profiles](rendering.md#colour-profiles).

## Channel Types All Say Half

Load EXR defaults to a header-only scan, which labels every channel `half`. Set
`openexr.header_only=false` to load pixels and report accurate types. Load EXR
Sequence always scans headers only. Read EXR ignores this setting and renders pixels.

## A Sequence Is Shorter Than Expected

Path discovery skips absent frame numbers. EXR Sequence to Preview's
`missing_frames` setting applies to discovered entries that fail conversion.
Connect Scan Sequence to control discovery policy. See
[discovery and failed frames](sequences.md#discovery-and-failed-frames).

Read EXR reports an error when the requested frame is absent from the connected
sequence. An out-of-range frame clamps to the sequence bounds.

## Video Dimensions Fail Validation

H.264 output requires even dimensions. Choose `odd_dimensions=Pad to Even` to
add black pixels. If rendered frame sizes differ, choose `frame_sizes=Pad to Largest`
or use `Crop Overscan` with a shared display window. Padding does not align
differing EXR coordinate origins. See [sequence canvas policies](sequences.md#sequence-canvas-policies).

## Render Limits And Memory

`openexr.max_render_pixels` limits Display EXR Channel and EXR Sequence to Preview
output and intermediate canvases, including video padding. The default is
`67108864`, enough for 8192x8192 pixels. Set a positive integer in the engine's
`openexr` settings. For example, `134217728` permits twice the default pixel count.

The setting is read on every execution. Oversized renders fail with the required
dimensions and setting name; images are never resized to fit. This is a
pixel-count limit with no separate width or height cap.

Read EXR, Display EXR Part and source decoding are outside this guard. The OpenEXR
binding still decodes the full source file. Float32 processing and concurrent
buffers need additional memory: one 8192x8192 RGBA float32 buffer occupies 1 GiB.
Video padding processes one frame at a time, but each conversion worker needs
its own buffers. Reduce `threads` if concurrent conversions use too much memory.

## Configure An External Viewer

Set `openexr.viewer_executable` to the full executable path, such as `/usr/bin/djv`
or `/Applications/Nuke15.0v1/Nuke15.0v1.app/Contents/MacOS/Nuke15.0v1`. Leave it
empty to use the OS file association for EXR files.

`openexr.viewer_args` accepts arguments such as `--hdr --linear`, parsed with
shell-style quoting. Arguments are passed before the source file path. Run the
node before clicking **Open in external viewer**. The viewer launches without
blocking the canvas.
