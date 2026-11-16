# OpenEXR Library for Griptape Nodes

Professional OpenEXR support for Griptape Nodes, designed for VFX and HDR pipelines. Read an EXR file or frame sequence straight to a viewable image, inspect a file's structure, load an EXR frame sequence, display individual parts or manually-assembled channels as tone-mapped images, convert a sequence to a preview video, and save images or channel data back to EXR.

Start with **Read EXR**. The other nodes are there when you need the structure, the lower-level sequence controls, or explicit per-channel wiring.

## Nodes

### Read EXR

Reads an EXR file or frame sequence and outputs a viewable image from a single node. This is the front door for most work: **Load EXR** -> **Get From List** -> **Display EXR Part** collapses into one node here. Modelled on Nuke's Read node.

The `path` input accepts either a path or a connected `Sequence`. A bare filename comes back from the scan as a one-frame sequence, so a single file and a 200-frame render use the same input and the same wiring.

An **Open in external viewer** button opens the source EXR in a configured HDR viewer (or the OS default) after the node has been run. See [Library Settings](#library-settings).

**Inputs**

| Parameter             | Type              | Description                                                                                                                              |
| --------------------- | ----------------- | ---------------------------------------------------------------------------------------------------------------------------------------- |
| `path`                | `Sequence \| str` | Path to an `.exr` file, or a frame pattern such as `render.####.exr`. A connected `Sequence` is used as-is.                              |
| `part_name`           | `str`             | Which part to read, as `"0: rgba"`, `"1: depth"`. Populated after the scan, hidden for single-part files.                                |
| `layer`               | `str`             | Which layer of the part to render. Populated after the scan, hidden when the part has one layer.                                         |
| `frame`               | `int`             | Absolute frame number, matching the filenames on disk. Hidden for a single file.                                                         |
| `window_mode`         | `str`             | `Full` (default), `Crop Overscan`, or `Show Overscan Boundary`. See [Display window](#display-window).                                   |
| `exposure`            | `float`           | Exposure in EV stops applied before tone mapping. Range -10 to +10, default `0.0`. In the Color Management group.                        |
| `color_mode`          | `str`             | `basic` (local tone mapping) or `ocio` (connected `OCIOColorParamsArtifact`). Default `basic`.                                           |
| `tone_mapping`        | `str`             | `sRGB`, `rec709`, `rec1886`, or `raw`. Default `sRGB`. See [Display transforms](#display-transforms). Used when `color_mode` is `basic`. |
| `embed_color_profile` | `bool`            | Embed an ICC profile describing the display transform. Default `false`. See [Colour profiles](#colour-profiles).                         |
| `output_file`         | `str`             | Filename for the saved PNG. Carries the frame number for a sequence, e.g. `read_exr.1001.png`.                                           |

**Outputs**

| Parameter                      | Type                       | Description                                                                                                                                           |
| ------------------------------ | -------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------- |
| `image`                        | `ImageUrlArtifact`         | 8-bit PNG (RGB or RGBA) of the current frame                                                                                                          |
| `part`                         | `EXRPartArtifact`          | Descriptor for the part that was read, carrying the float data on                                                                                     |
| `channels`                     | `list[EXRChannelArtifact]` | One descriptor per channel in that part                                                                                                               |
| `image_width` / `image_height` | `int`                      | Dimensions of the rendered `image`: the display window in `Crop Overscan` mode, otherwise the data window. `part` keeps the data window's dimensions. |
| `part_count` / `channel_count` | `int`                      | Parts in the file, channels in the part that was read                                                                                                 |
| `first_frame` / `last_frame`   | `int`                      | Sequence bounds; `0` for a single file                                                                                                                |
| `frame_count`                  | `int`                      | Frames in the sequence; `1` for a single file                                                                                                         |

The `image` output is a rendered 8-bit PNG rather than the `.exr` itself, because the standard library's image nodes cannot open an EXR and have no float or greater-than-8-bit path. The float data travels on `part` and `channels` instead, so a downstream **Display EXR Channel** still gets the real pixels.

#### Iterating a sequence

Outputs are scalar: the node emits one frame per execution and never a whole-sequence list. Iteration belongs to the graph. Wire **For Loop Start**'s `index` output into `frame` and drive the loop from `first_frame` and `last_frame`:

```
Read EXR.first_frame -> For Loop Start.start
Read EXR.last_frame  -> For Loop Start.end
For Loop Start.index -> Read EXR.frame
```

While a connection drives `frame`, the slider renders disabled but live, so it doubles as a frame readout during playback. Output filenames carry the frame number (`read_exr.1001.png`), so a loop leaves a clean numbered set.

An out-of-range `frame` clamps to the sequence bounds and says so in the result details rather than failing. A frame that is absent from the sequence's entries fails loudly, naming the frame: the scan already applied your `MissingItemPolicy`, so deciding it again here would override you. Set `SKIP` or `FILL_NEAREST` at the scan if you want gaps tolerated.

#### Relationship to the other nodes

All of these coexist. **Read EXR** is the recommended starting point; the others keep the jobs it deliberately does not do.

- **Load EXR** is the structural inspector: header-only, the full set of header outputs, dynamic per-part and per-channel groups.
- **Load EXR Sequence** is the lower-level sequence node: `on_mismatch`, a thread count, and a header-only pass over hundreds of frames with no rendering.
- **Display EXR Channel** is still the only way to map arbitrary channels onto R/G/B/A slots.

______________________________________________________________________

### Load EXR

Loads an EXR file and exposes its full structure as typed outputs. Pixel loading is off by default (`header_only=True`), making it fast even on large multi-part renders. This is configurable via the `openexr.header_only` engine setting. A header-only scan labels every channel `half`, and the node shows a warning saying so.

An **Open in external viewer** button lets you open the loaded file directly in a configured HDR viewer (or the OS default). See [Library Settings](#library-settings).

**Inputs**

| Parameter   | Description             |
| ----------- | ----------------------- |
| `file_path` | Path to the `.exr` file |

**Outputs**

| Parameter                        | Type                    | Description                                                |
| -------------------------------- | ----------------------- | ---------------------------------------------------------- |
| `image_width` / `image_height`   | `int`                   | Dimensions from data window                                |
| `part_count` / `channel_count`   | `int`                   | Counts                                                     |
| `compression`                    | `str`                   | e.g. `ZIP_COMPRESSION`, `DWAB_COMPRESSION`                 |
| `storage_type`                   | `str`                   | `scanlineimage`, `tiledimage`, `deepscanline`, `deeptiled` |
| `pixel_aspect_ratio`             | `float`                 |                                                            |
| `data_window` / `display_window` | `str`                   | `"xmin,ymin - xmax,ymax"`                                  |
| `time_code`                      | `str`                   | `HH:MM:SS:FF`, empty if absent                             |
| `software`                       | `str`                   | Authoring application, empty if absent                     |
| `owner`                          | `str`                   | Asset owner, empty if absent                               |
| `chromaticities`                 | `str`                   | JSON with `red_x/y`, `green_x/y`, `blue_x/y`, `white_x/y`  |
| `custom_attributes`              | `str`                   | JSON of all non-standard header attributes                 |
| `parts`                          | `list[EXRPartArtifact]` | Structured descriptor for every part in the file           |

Dynamic groups are also populated after scan:

- **Parts** — one `EXRPartArtifact` output per part, each with its own channel outputs (hidden for single-part files; channels appear directly in the Channels group instead)
- **Channels** — one `EXRChannelArtifact` per raw channel with name, pixel type, and sampling (single-part files only)

______________________________________________________________________

### Load EXR Sequence

Loads an EXR frame sequence (from `ScanSequence`/`ScanSplitSequence`) and exposes per-frame structure as a list. Unlike **Load EXR**, this node always scans header-only and ignores the `openexr.header_only` engine setting: the output `EXRPartArtifact` never carries pixel data, so there is nothing to gain from a full scan.

v1 is single-part: every frame is scanned at the same `part_index`. Multi-part-per-frame sequences (AOV stacks) are out of scope for now.

**Inputs**

| Parameter     | Type       | Description                                                                                                 |
| ------------- | ---------- | ----------------------------------------------------------------------------------------------------------- |
| `sequence`    | `Sequence` | Frame sequence from **Scan Sequence** / **Scan Split Sequence**                                             |
| `part_index`  | `int`      | Part index to read from every frame. Default `0`.                                                           |
| `on_mismatch` | `str`      | What to do when a frame fails to scan or its layout doesn't match the sequence: `skip` (default) or `fail`. |
| `threads`     | `int`      | Worker count for header scans. Range 1-16, default `4`. In the Advanced group.                              |

**Outputs**

| Parameter     | Type                    | Description                                               |
| ------------- | ----------------------- | --------------------------------------------------------- |
| `parts`       | `list[EXRPartArtifact]` | One descriptor per scanned frame, ordered by frame number |
| `frame_count` | `int`                   | Number of frames in the sequence                          |

Each `EXRPartArtifact` carries a `frame_number` field set from the sequence's real frame number — list position in `parts` does not track frame number once a frame is dropped, so use `frame_number`, not index, to name output files or look up corresponding frames elsewhere.

Frames that fail to scan (resolution/channel mismatch against the rest of the sequence, or an out-of-range `part_index`) are handled according to `on_mismatch`:

- `skip` (default): the bad frame is dropped from `parts` (which may then be shorter than `frame_count`) and the drop is reported in the result details; the rest of the sequence still loads.
- `fail`: any dropped frame fails the whole run instead.

The output `parts` is a plain list, so it connects directly into a **For Each** loop for per-frame work (e.g. **Display EXR Part** per frame).

#### Manual composition alternative

For full per-frame control, you can wire `ScanSequence -> ForEach -> LoadEXR` by hand instead: feed `ScanSequence`'s `paths` output into **For Each Start**, and connect the current item to **Load EXR**'s `file_path`. This works today but isn't recommended for tight loops: **Load EXR**'s dynamic per-part/per-channel UI parameters re-populate on every `file_path` change, which is wasteful run hundreds of times. **Load EXR Sequence** followed by **For Each** over its `parts` output is the preferred path for most users.

______________________________________________________________________

### Display EXR Part

Renders an EXR part to an 8-bit PNG (RGB or RGBA). Accepts an `EXRPartArtifact` from **Load EXR**, auto-selects RGB display channels, applies exposure and tone mapping, and writes the result as an `ImageUrlArtifact`.

An **Open in external viewer** button opens the source EXR in a configured HDR viewer (or the OS default) after the node has been run. See [Library Settings](#library-settings).

**Inputs**

| Parameter             | Type              | Description                                                                                                      |
| --------------------- | ----------------- | ---------------------------------------------------------------------------------------------------------------- |
| `part`                | `EXRPartArtifact` | EXR part to render. Assumes scene-linear HDR data; no gamut conversion is applied.                               |
| `exposure`            | `float`           | Exposure in EV stops applied before tone mapping. Range −10 to +10, default `0.0`.                               |
| `window_mode`         | `str`             | `Full` (default), `Crop Overscan`, or `Show Overscan Boundary`. See [Display window](#display-window).           |
| `color_mode`          | `str`             | `basic` (local tone mapping) or `ocio` (connected `OCIOColorParamsArtifact`). Default `basic`.                   |
| `tone_mapping`        | `str`             | `sRGB`, `rec709`, `rec1886`, or `raw`. Default `sRGB`. See [Display transforms](#display-transforms).            |
| `embed_color_profile` | `bool`            | Embed an ICC profile describing the display transform. Default `false`. See [Colour profiles](#colour-profiles). |

**Outputs**

| Parameter     | Type               | Description                                   |
| ------------- | ------------------ | --------------------------------------------- |
| `image`       | `ImageUrlArtifact` | 8-bit PNG (RGB or RGBA) for in-canvas display |
| `output_file` | `str`              | Path to the saved PNG file                    |

#### Display transforms

In `basic` colour mode, `tone_mapping` selects how scene-linear values are
encoded for display. The names match the views in Nuke's default OCIO config,
so `rec709` and `rec1886` mean the same curves there as they do here.

| Choice    | Curve                                    | Linear 0.18 renders as |
| --------- | ---------------------------------------- | ---------------------- |
| `sRGB`    | sRGB transfer function (IEC 61966-2-1)   | 118                    |
| `rec709`  | BT.709 OETF                              | 104                    |
| `rec1886` | BT.1886, pure gamma 2.4                  | 125                    |
| `raw`     | None. Clamped to [0, 1] with no encoding | 46                     |

`rec709` is darker than `sRGB` through the midtones and `rec1886` is lighter,
because sRGB's effective gamma of roughly 2.2 sits between the two. `raw`
applies no transfer function, which is useful for reading actual pixel values
but looks dark on screen.

Every choice is a pure transfer function, so scene values above 1.0 clip to
white. Nuke's default viewer behaves the same way. Lower the `exposure` to
bring highlights into range, or use `ocio` mode with a config that provides a
tone-mapped view transform such as ACES.

Without an embedded profile, viewers decode the PNG as sRGB, so picking
`rec709` or `rec1886` shows the same mismatch you get when viewing a Rec.709
output on an sRGB monitor. See [Colour profiles](#colour-profiles).

> **Note:** `linear` and `filmic` were removed, and workflows still set to
> either fail with an error naming the replacement.
>
> `linear` applied the sRGB transfer function despite its name, making it a
> confusing spelling of `sRGB`. Use `sRGB` to keep the previous output, or
> `raw` for genuinely unencoded linear values.
>
> `filmic` was a Narkowicz 2015 approximation of the ACES RRT, a term from
> Blender and real-time rendering rather than VFX. Basic mode now offers only
> display encodings, matching Nuke's default viewer. Use `sRGB` for a similar
> look, or `ocio` mode for a real tone-mapped view transform.

#### Display window

Use `window_mode` to choose which region the node displays. Supported by **Read EXR**, **Display EXR Part**, **Display EXR Channel** and **EXR Sequence to Preview**.

<details>
<summary>Info: EXR data and display windows</summary>

An EXR's _data window_ contains its stored pixels. The _display window_ defines the intended frame. Stored pixels can extend beyond the frame (overscan) or cover only part of it.

</details>

| `window_mode`            | Rendered image                                                                                                                                        |
| ------------------------ | ----------------------------------------------------------------------------------------------------------------------------------------------------- |
| `Full` (default)         | Every stored pixel, at the data window's size.                                                                                                        |
| `Crop Overscan`          | The display window's size. Overscan is cropped away, and frame areas with no stored pixels are black, with zero alpha in RGBA output.                 |
| `Show Overscan Boundary` | Every stored pixel, with a dashed black-and-white outline just inside the display window's edge. Edges that fall outside the data window are clipped. |

Only the rendered PNG or video changes. The `part` and `channels` outputs describe the original EXR and retain its data window, display window and source pixels. Downstream display nodes choose their own `window_mode`; set the same mode and intended frame there to match Read EXR's rendered dimensions. Read EXR's `image_width` and `image_height` describe its rendered image, while dimensions on `part` describe the source data window.

The automatic file-level EXR preview uses the data window and receives no node-specific rendering settings. It can show overscan even when Read EXR renders with `Crop Overscan`. This preview is separate from the node's rendered `image` output.

**Load EXR** and **Load EXR Sequence** report both windows as stored. For a sequence, each frame is framed by its own header, so a changing data window renders at one size in `Crop Overscan` mode when the display window stays constant. Workflows saved before this option existed load with `Full`, so they render as they did.

#### Colour profiles

By default the written PNG carries no colour profile, so viewers read it as
sRGB whichever transform produced it. That is what makes `rec709` and
`rec1886` look different on screen: the file is encoded with one curve and
decoded with another, the same mismatch you get sending a Rec.709 output to an
sRGB monitor.

Setting `embed_color_profile` writes an ICC profile naming the curve actually
used. The pixels are untouched; only the label changes.

The consequence surprises people, so it is worth stating plainly: **with a
profile embedded, all the transforms look the same in a colour-managed
viewer.** That is correct behaviour. Once the file says how it was encoded, a
managed application converts it to your display and arrives at the same colour
every time. The visible difference between transforms exists only while the
label is missing.

So the switch chooses what the output is for:

| `embed_color_profile` | Use for                                                           |
| --------------------- | ----------------------------------------------------------------- |
| `false` (default)     | An on-screen preview that emulates a Rec.709 or BT.1886 monitor.  |
| `true`                | A file another application will open, convert or grade correctly. |

The parameter is hidden in `ocio` colour mode, where the display/view
transform already resolves to a display colorspace we cannot describe.

**Video** is always tagged, and has no `embed_color_profile` parameter. An MP4
cannot carry an ICC profile, so **EXR Sequence to Preview** writes Rec.709
primaries and a matching transfer characteristic into the stream instead.
Players do honour those tags, and leaving them absent means the player
guesses. In `ocio` colour mode the transfer tag is Rec.709, since the
display/view transform resolves to a colorspace we cannot name.

Alpha is included automatically when an alpha channel (`A`) is found in the part.

______________________________________________________________________

### Display EXR Channel

Combines 1–4 individual `EXRChannelArtifact` inputs into an 8-bit PNG (RGB or RGBA). An **Open in external viewer** button opens the source EXR after the node has been run. See [Library Settings](#library-settings). Each slot (R, G, B, A) is optional and can come from different EXR files or different parts. Missing RGB slots are zero-filled; connecting the A slot produces RGBA output with optional background compositing.

**Inputs**

| Parameter             | Type                                       | Description                                                                                                                                    |
| --------------------- | ------------------------------------------ | ---------------------------------------------------------------------------------------------------------------------------------------------- |
| `channel_r`           | `EXRChannelArtifact`                       | Channel mapped to the red plane. Optional.                                                                                                     |
| `channel_g`           | `EXRChannelArtifact`                       | Channel mapped to the green plane. Optional.                                                                                                   |
| `channel_b`           | `EXRChannelArtifact`                       | Channel mapped to the blue plane. Optional.                                                                                                    |
| `channel_a`           | `EXRChannelArtifact`                       | Channel used as alpha. Optional; when connected, output is RGBA.                                                                               |
| `exposure`            | `float`                                    | Exposure in EV stops applied before tone mapping. Range −10 to +10, default `0.0`.                                                             |
| `window_mode`         | `str`                                      | `Full` (default), `Crop Overscan`, or `Show Overscan Boundary`. The frame comes from `frame_reference`. See [Display window](#display-window). |
| `channel_alignment`   | `str`                                      | `Match Pixel Positions` (default), `Match EXR Coordinates`, or `Require Matching Windows`. See below.                                          |
| `frame_reference`     | `str`                                      | `First Connected RGB` (default), `R`, `G`, `B`, or `A`. Selects the source whose display window defines the intended frame.                    |
| `color_mode`          | `str`                                      | `basic` (local tone mapping) or `ocio` (connected `OCIOColorParamsArtifact`). Default `basic`.                                                 |
| `tone_mapping`        | `str`                                      | `sRGB`, `rec709`, `rec1886`, or `raw`. Default `sRGB`. See [Display transforms](#display-transforms).                                          |
| `embed_color_profile` | `bool`                                     | Embed an ICC profile describing the display transform. Default `false`. See [Colour profiles](#colour-profiles).                               |
| `background`          | `ImageArtifact \| ImageUrlArtifact \| str` | Background image for A-over-B compositing. Ignored when no alpha channel is connected.                                                         |

**Outputs**

| Parameter     | Type               | Description                                   |
| ------------- | ------------------ | --------------------------------------------- |
| `image`       | `ImageUrlArtifact` | 8-bit PNG (RGB or RGBA) for in-canvas display |
| `output_file` | `str`              | Path to the saved PNG file                    |

At least one RGB slot must be connected.

**Channel alignment**

- `Match Pixel Positions` aligns the top-left stored pixels and requires equal array dimensions. It ignores other sources' data-window offsets. The reference source supplies the coordinate origin and intended frame. This default preserves existing saved workflows.
- `Match EXR Coordinates` places every channel using its own data-window coordinates, supporting different sizes and origins without resizing. Missing samples are zero-filled. `Full` and `Show Overscan Boundary` include the bounding rectangle of all connected data windows, including alpha. `Crop Overscan` renders the reference display window directly.
- `Require Matching Windows` fails if any connected channel, including alpha, has different data or display windows. The error identifies the affected slot.

Coordinate and strict alignment require channels with one sample per EXR coordinate; subsampled channels fail with a slot-specific error. Source planes are never resampled.

`First Connected RGB` chooses R, then G, then B. An explicitly selected reference slot must be connected. Use `frame_reference=A` when a matte defines the intended frame, or `R` when beauty should define it.

For beauty with a data window starting at `(-8, -8)` and a matte starting at `(0, 0)`, choose `Match EXR Coordinates` to align their EXR positions. Choose `Match Pixel Positions` to align their stored array positions instead. Result details report the alignment policy and effective reference.

Display EXR Channel checks output and intermediate assembly sizes against `openexr.max_render_pixels`. See [Library Settings](#library-settings) for the default and how to raise it.

______________________________________________________________________

### Save EXR

Saves a single-part EXR file from either an 8-bit image or EXR channel artifacts. Operates in two modes:

- **Mode A (image input)** — accepts an `ImageArtifact` or `ImageUrlArtifact` (8-bit PNG/JPEG), normalises pixel values from [0, 255] to [0.0, 1.0], and writes R, G, B channels.
- **Mode B (channel input)** — accepts up to four `EXRChannelArtifact` slots (R, G, B, A), loads their pixel arrays, and writes them preserving float precision. Mode B takes priority when any channel slot is connected.

**Inputs**

| Parameter     | Type                                | Description                                                                                                                                                                                                                                                                                                           |
| ------------- | ----------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `image_in`    | `ImageArtifact \| ImageUrlArtifact` | 8-bit image source (Mode A). Ignored when any channel slot is connected.                                                                                                                                                                                                                                              |
| `channel_r`   | `EXRChannelArtifact`                | Channel to write as R (Mode B). Optional.                                                                                                                                                                                                                                                                             |
| `channel_g`   | `EXRChannelArtifact`                | Channel to write as G (Mode B). Optional.                                                                                                                                                                                                                                                                             |
| `channel_b`   | `EXRChannelArtifact`                | Channel to write as B (Mode B). Optional.                                                                                                                                                                                                                                                                             |
| `channel_a`   | `EXRChannelArtifact`                | Channel to write as A (Mode B). Optional.                                                                                                                                                                                                                                                                             |
| `compression` | `str`                               | Codec applied to the output file. Choices: `ZIP`, `NONE`, `RLE`, `ZIPS`, `PIZ`, `PXR24`, `B44`, `B44A`, `DWAA`, `DWAB`, `HTJ2K32`, `HTJ2K256`, `LJ2K`, `ZSTD`. `PXR24` is lossy for `FLOAT` pixels, `B44` and `B44A` for `HALF` pixels, and `DWAA`, `DWAB` and `LJ2K` for both; the rest are lossless. Default `ZIP`. |
| `pixel_type`  | `str`                               | Pixel storage type. `HALF` (16-bit float, standard) or `FLOAT` (32-bit, full precision). Default `HALF`.                                                                                                                                                                                                              |

The collapsed **Metadata** group exposes optional header fields:

| Parameter            | Description                                     |
| -------------------- | ----------------------------------------------- |
| `part_name`          | Name for this EXR part                          |
| `pixel_aspect_ratio` | Pixel width/height ratio (default `1.0`)        |
| `owner`              | Asset owner                                     |
| `comments`           | Free-text comments                              |
| `capture_date`       | Capture date (e.g. `2025-01-01T12:00:00`)       |
| `software`           | Authoring application name                      |
| `time_code`          | Editorial timecode (`HH:MM:SS:FF`)              |
| `custom_attributes`  | Non-standard header attributes as a JSON object |

**Outputs**

| Parameter     | Type              | Description                                                                           |
| ------------- | ----------------- | ------------------------------------------------------------------------------------- |
| `output_file` | `str`             | Path to the written `.exr` file                                                       |
| `output_part` | `EXRPartArtifact` | Descriptor for the written part — connectable to **Display EXR Part** or **Load EXR** |

______________________________________________________________________

### EXR Sequence to Preview

Bakes an EXR frame sequence down to a single MP4/MOV preview video, applying exposure and color management per frame. Accepts a `Sequence` (from **Scan Sequence**/**Scan Split Sequence**) and loads pixels eagerly, since it has to encode a video. Use **Load EXR Sequence** instead if you need per-frame EXR data in the graph rather than a baked preview.

**Inputs**

| Parameter          | Type                      | Description                                                                                                                                   |
| ------------------ | ------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------- |
| `sequence`         | `Sequence`                | Frame sequence from **Scan Sequence** / **Scan Split Sequence**                                                                               |
| `frame_rate`       | `float`                   | Output frame rate. Default `24.0`.                                                                                                            |
| `part_index`       | `int`                     | Part index to read from every frame. Default `0`.                                                                                             |
| `exposure`         | `float`                   | Exposure in EV stops applied before tone mapping. Default `0.0`.                                                                              |
| `window_mode`      | `str`                     | `Full` (default), `Crop Overscan`, or `Show Overscan Boundary`, framed by each frame's own windows. See [Display window](#display-window).    |
| `color_mode`       | `str`                     | `basic` or `ocio`. Default `basic`.                                                                                                           |
| `tone_mapping`     | `str`                     | `sRGB`, `rec709`, `rec1886`, or `raw`. Default `sRGB`. See [Display transforms](#display-transforms).                                         |
| `color_params`     | `OCIOColorParamsArtifact` | OCIO colour parameters. Required when `color_mode` is `ocio`.                                                                                 |
| `format`           | `str`                     | Output container: `mp4` or `mov`. Default `mp4`.                                                                                              |
| `odd_dimensions`   | `str`                     | `Pad to Even` (default) adds at most one black column/right and row/bottom. `Require Even` rejects an odd encoder canvas.                     |
| `frame_sizes`      | `str`                     | `Require Matching Sizes` (default) rejects differing rendered dimensions. `Pad to Largest` pads to maximum width and height without resizing. |
| `missing_frames`   | `str`                     | `skip` (default), `error`, or `black`. Controls failed conversions; canvas-policy failures always stop the run.                               |
| `processing_speed` | `str`                     | `fast`, `balanced`, or `quality`. Trades encode speed for size/quality.                                                                       |
| `threads`          | `int`                     | Worker count for per-frame conversion. Range 1-16, default `4`. In the Advanced group.                                                        |

**Outputs**

| Parameter          | Type               | Description                                        |
| ------------------ | ------------------ | -------------------------------------------------- |
| `video`            | `VideoUrlArtifact` | The encoded preview video                          |
| `frame_count`      | `int`              | Total frames in the sequence                       |
| `frames_processed` | `int`              | Frames converted so far, updated live during a run |
| `output_file`      | `str`              | Workspace path to the written video file           |

The default skips failed conversions. Choose `missing_frames=error` to fail the run or `black` to retain sequence timing with black replacements.

### Sequence canvas policies

Both MP4 and MOV use H.264 with `yuv420p`, which requires even width and height. `odd_dimensions=Pad to Even` adds black pixels after colour processing, preserving the rendered image at the top-left. `Require Even` reports the odd dimensions and stops before encoding.

`frame_sizes=Require Matching Sizes` compares successful frames before even padding and reports conflicting source paths and dimensions. Choose `Pad to Largest` to preserve each frame without resizing or cropping. It uses the maximum width and maximum height across successful frames, then applies the odd-dimension policy. Padding is black on the right and bottom. This aligns local rendered canvases; it does not align differing EXR origins. Use `Crop Overscan` with a shared display window to align changing EXR crops by coordinates.

Failed-frame black replacements use the final canvas dimensions. Size-policy failures and renders above `openexr.max_render_pixels` fail the sequence even when `missing_frames=skip` or `black`. Result details report final dimensions, policies and how many successful frames were padded.

The shared [Library Settings](#library-settings) render limit applies to intermediate rendered frames and the final padded canvas. Padding processes one frame at a time, but concurrent EXR conversion workers still require memory for their own buffers.

## Library Settings

Settings are configured in the engine under the `openexr` category.

| Setting                     | Default    | Description                                                                                                                                                                                              |
| --------------------------- | ---------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `openexr.max_render_pixels` | `67108864` | Maximum pixels in Display EXR Channel and EXR Sequence to Preview output and intermediate rendering canvases, including sequence padding. Must be a positive integer. Default supports 8192x8192 images. |
| `openexr.header_only`       | `true`     | When `true`, **Load EXR** reads only the file header (fast) and labels every channel `half`. Set to `false` to read accurate per-channel pixel types at the cost of loading pixel data into memory.      |
| `openexr.viewer_executable` | `""`       | Full path to an external HDR viewer executable (e.g. `/usr/bin/djv`, `/Applications/Nuke15.0v1/Nuke15.0v1.app/Contents/MacOS/Nuke15.0v1`). When empty, the OS default file association is used.          |
| `openexr.viewer_args`       | `""`       | Additional command-line arguments passed to the viewer before the file path, e.g. `--hdr --linear`. Parsed with shell-style quoting rules.                                                               |

Increase `max_render_pixels` in the engine's `openexr` library settings to permit larger canvases. For example, `134217728` allows twice the default pixel count. The setting is read on every execution; oversized renders fail with the required dimensions and setting name. Images are never resized to fit the limit.

The limit applies to Display EXR Channel and EXR Sequence to Preview output and intermediate rendering canvases. Read EXR, Display EXR Part and source EXR decoding are outside this guard: the OpenEXR binding still decodes the full source file. This is a pixel-count limit, with no separate width or height cap. Float32 processing and concurrent buffers require additional memory; one 8192x8192 RGBA float32 buffer occupies 1 GiB.

Only **Load EXR** honours `openexr.header_only`. **Load EXR Sequence** always scans header-only, since its output carries no pixel data and a full scan would only buy slower, discarded reads. **Read EXR** always reads pixels, since decoding the selected part is the whole job.

The viewer is launched fire-and-forget — clicking the button never blocks the canvas.

## Development

### Requirements

- Python 3.12
- [uv](https://docs.astral.sh/uv/)

### Setup

```bash
make install/dev
```

Use the `make` target rather than `uv sync` directly. Dev tooling belongs in `.venv-dev`, and plain `uv` would install it into the `.venv` the engine builds beside the manifest, which makes the library load as FLAWED. See [CONTRIBUTING.md](CONTRIBUTING.md#two-virtual-environments) for why.

### Running Tests

```bash
# Unit tests only (no EXR files required)
make test/unit

# Integration tests (fixtures auto-generated on first run)
make test/integration

# Full suite
make test
```

### Lint and Type Check

```bash
make check/lint
make check/types
```

A direct `uv run` needs `UV_PROJECT_ENVIRONMENT=.venv-dev`, for the reason above.

### Test Fixtures

EXR fixtures are generated locally and are not committed to the repository. To generate them manually:

```bash
make test/fixtures
```

The fixtures are also generated automatically the first time you run `pytest`. The `openexr` Python package (included in dev dependencies) is required:

```bash
make install/dev
```

The fixture set covers:

| File                    | Purpose                                                   |
| ----------------------- | --------------------------------------------------------- |
| `single_part_rgba.exr`  | Baseline single-part RGBA                                 |
| `single_part_aovs.exr`  | Multi-layer AOVs (`beauty`, `diffuse`, `depth`, `normal`) |
| `multi_part.exr`        | Three named parts with legacy channel naming              |
| `tiled.exr`             | Tiled image with `TileDescription`                        |
| `overscan.exr`          | Data window extends beyond display window (8px overscan)  |
| `custom_attributes.exr` | Chromaticities, software, and non-standard attributes     |
| `nuke_metadata.exr`     | Nuke-style custom binary blob attribute (`nuke/node`)     |
| `legacy_multipart.exr`  | Multi-part where part name encodes the layer name         |
