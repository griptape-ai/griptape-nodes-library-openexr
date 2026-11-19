# Node reference

Use **Read EXR** for new file and sequence reading workflows. See the [getting-started examples](../README.md#getting-started) for common connections.

## Read EXR

Reads an EXR file or frame sequence and outputs a viewable image. It combines **Load EXR** -> **Get From List** -> **Display EXR Part** in one node and is modelled on Nuke's Read node.

The `path` input accepts either a path or a connected `Sequence`. Scanning a bare filename produces a one-frame sequence, so single files and multi-frame renders use the same input and connections.

An **Open in external viewer** button opens the source EXR in a configured HDR viewer (or the OS default) after the node has been run. See [Library Settings](../README.md#library-settings).

**Inputs**

| Parameter             | Type                      | Description                                                                                                                                          |
| --------------------- | ------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------- |
| `path`                | `Sequence \| str`         | Path to an `.exr` file, or a frame pattern such as `render.####.exr`. A connected `Sequence` is used as-is.                                          |
| `part_name`           | `str`                     | Which part to read, as `"0: rgba"`, `"1: depth"`. Populated after the scan, hidden for single-part files.                                            |
| `layer`               | `str`                     | Which layer of the part to render. Populated after the scan, hidden when the part has one layer.                                                     |
| `frame`               | `int`                     | Absolute frame number, matching the filenames on disk. Hidden for a single file.                                                                     |
| `window_mode`         | `str`                     | `Full` (default), `Crop Overscan`, or `Show Overscan Boundary`. See [Display window](rendering.md#display-window).                                   |
| `exposure`            | `float`                   | Exposure in EV stops applied before tone mapping. Range -10 to +10, default `0.0`. In the Color Management group.                                    |
| `color_mode`          | `str`                     | `basic` (local tone mapping) or `ocio` (connected `OCIOColorParamsArtifact`). Default `basic`.                                                       |
| `color_params`        | `OCIOColorParamsArtifact` | Connected colour parameters. Required when `color_mode` is `ocio`; hidden in `basic` mode.                                                           |
| `tone_mapping`        | `str`                     | `sRGB`, `rec709`, `rec1886`, or `raw`. Default `sRGB`. See [Display transforms](rendering.md#display-transforms). Used when `color_mode` is `basic`. |
| `embed_color_profile` | `bool`                    | Embed an ICC profile describing the display transform. Default `false`. See [Colour profiles](rendering.md#colour-profiles).                         |
| `output_file`         | `str`                     | Filename for the saved PNG. Carries the frame number for a sequence, e.g. `read_exr.1001.png`.                                                       |

**Outputs**

| Parameter                      | Type                       | Description                                                                                                                                           |
| ------------------------------ | -------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------- |
| `image`                        | `ImageUrlArtifact`         | 8-bit PNG (RGB or RGBA) of the current frame                                                                                                          |
| `part`                         | `EXRPartArtifact`          | Descriptor for the selected part, retaining its source floating-point data                                                                            |
| `channels`                     | `list[EXRChannelArtifact]` | One descriptor per channel in that part                                                                                                               |
| `image_width` / `image_height` | `int`                      | Dimensions of the rendered `image`: the display window in `Crop Overscan` mode, otherwise the data window. `part` keeps the data window's dimensions. |
| `part_count` / `channel_count` | `int`                      | Parts in the file, channels in the part that was read                                                                                                 |
| `first_frame` / `last_frame`   | `int`                      | Sequence bounds; `0` for a single file                                                                                                                |
| `frame_count`                  | `int`                      | Frames in the sequence; `1` for a single file                                                                                                         |

The `image` output is a rendered 8-bit PNG rather than the `.exr` itself, because the standard library's image nodes cannot open an EXR and have no float or greater-than-8-bit path. The float data travels on `part` and `channels` instead, so a downstream **Display EXR Channel** still gets the real pixels.

## Load EXR

**Deprecated.** Use **Read EXR** for new file-reading workflows. Load EXR remains available while missing functionality is migrated, with no scheduled removal release. See the [migration guide](migration.md).

Loads an EXR file and exposes its full structure as typed outputs. Pixel loading is off by default (`header_only=True`), making it fast even on large multi-part renders. This is configurable via the `openexr.header_only` engine setting. A header-only scan labels every channel `half`, and the node shows a warning saying so.

An **Open in external viewer** button lets you open the loaded file directly in a configured HDR viewer (or the OS default). See [Library Settings](../README.md#library-settings).

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

Dynamic groups are populated after scanning:

- **Parts** : one `EXRPartArtifact` output per part, each with its own channel outputs (hidden for single-part files; channels appear directly in the Channels group instead)
- **Channels** : one `EXRChannelArtifact` per raw channel with name, pixel type, and sampling (single-part files only)

## Load EXR Sequence

**Deprecated.** Use **Read EXR** for new sequence-reading workflows. Load EXR Sequence remains available while missing functionality is migrated, with no scheduled removal release. See the [migration guide](migration.md).

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

Each `EXRPartArtifact` carries a `frame_number` field set from the sequence's real frame number. List position in `parts` does not track frame number once a frame is dropped, so use `frame_number`, not index, to name output files or look up corresponding frames elsewhere.

Frames that fail to scan (resolution/channel mismatch against the rest of the sequence, or an out-of-range `part_index`) are handled according to `on_mismatch`:

- `skip` (default): the bad frame is dropped from `parts` (which may then be shorter than `frame_count`) and the drop is reported in the result details; the rest of the sequence still loads.
- `fail`: any dropped frame fails the whole run instead.

The output `parts` is a plain list, so it connects directly into a **For Each** loop for per-frame work (e.g. **Display EXR Part** per frame).

### Manual composition alternative

For new selected-frame reading workflows, connect `Sequence` to **Read EXR**'s `path` and iterate actual frame numbers through `frame`. See [Iterating a sequence](sequences.md#iterating-a-sequence).

Existing `ScanSequence -> ForEach -> LoadEXR` workflows remain supported during migration. Load EXR's dynamic parameters repopulate on every `file_path` change. Keep **Load EXR Sequence** followed by **For Each** where you need its batch descriptor list, mismatch validation or parallel header-only scans.

## Display EXR Part

Renders an EXR part to an 8-bit PNG (RGB or RGBA). Accepts an `EXRPartArtifact` from **Read EXR**'s `part` output or the retained deprecated load nodes, auto-selects RGB display channels, applies exposure and tone mapping, and writes the result as an `ImageUrlArtifact`.

An **Open in external viewer** button opens the source EXR in a configured HDR viewer (or the OS default) after the node has been run. See [Library Settings](../README.md#library-settings).

**Inputs**

| Parameter             | Type                      | Description                                                                                                                  |
| --------------------- | ------------------------- | ---------------------------------------------------------------------------------------------------------------------------- |
| `part`                | `EXRPartArtifact`         | EXR part to render. Assumes scene-linear HDR data; no gamut conversion is applied.                                           |
| `exposure`            | `float`                   | Exposure in EV stops applied before tone mapping. Range -10 to +10, default `0.0`.                                           |
| `window_mode`         | `str`                     | `Full` (default), `Crop Overscan`, or `Show Overscan Boundary`. See [Display window](rendering.md#display-window).           |
| `color_mode`          | `str`                     | `basic` (local tone mapping) or `ocio` (connected `OCIOColorParamsArtifact`). Default `basic`.                               |
| `color_params`        | `OCIOColorParamsArtifact` | Connected colour parameters. Required when `color_mode` is `ocio`; hidden in `basic` mode.                                   |
| `tone_mapping`        | `str`                     | `sRGB`, `rec709`, `rec1886`, or `raw`. Default `sRGB`. See [Display transforms](rendering.md#display-transforms).            |
| `embed_color_profile` | `bool`                    | Embed an ICC profile describing the display transform. Default `false`. See [Colour profiles](rendering.md#colour-profiles). |

**Outputs**

| Parameter     | Type               | Description                                   |
| ------------- | ------------------ | --------------------------------------------- |
| `image`       | `ImageUrlArtifact` | 8-bit PNG (RGB or RGBA) for in-canvas display |
| `output_file` | `str`              | Path to the saved PNG file                    |

## Display EXR Channel

Combines 1-4 individual `EXRChannelArtifact` inputs into an 8-bit PNG (RGB or RGBA). An **Open in external viewer** button opens the source EXR after the node has been run. See [Library Settings](../README.md#library-settings). Each slot (R, G, B, A) is optional and can come from different EXR files or different parts. Missing RGB slots are zero-filled; connecting the A slot produces RGBA output with optional background compositing.

**Inputs**

| Parameter             | Type                                       | Description                                                                                                                                                |
| --------------------- | ------------------------------------------ | ---------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `channel_r`           | `EXRChannelArtifact`                       | Channel mapped to the red plane. Optional.                                                                                                                 |
| `channel_g`           | `EXRChannelArtifact`                       | Channel mapped to the green plane. Optional.                                                                                                               |
| `channel_b`           | `EXRChannelArtifact`                       | Channel mapped to the blue plane. Optional.                                                                                                                |
| `channel_a`           | `EXRChannelArtifact`                       | Channel used as alpha. Optional; when connected, output is RGBA.                                                                                           |
| `exposure`            | `float`                                    | Exposure in EV stops applied before tone mapping. Range -10 to +10, default `0.0`.                                                                         |
| `window_mode`         | `str`                                      | `Full` (default), `Crop Overscan`, or `Show Overscan Boundary`. The frame comes from `frame_reference`. See [Display window](rendering.md#display-window). |
| `channel_alignment`   | `str`                                      | `Match Pixel Positions` (default), `Match EXR Coordinates`, or `Require Matching Windows`. See below.                                                      |
| `frame_reference`     | `str`                                      | `First Connected RGB` (default), `R`, `G`, `B`, or `A`. Selects the source whose display window defines the intended frame.                                |
| `color_mode`          | `str`                                      | `basic` (local tone mapping) or `ocio` (connected `OCIOColorParamsArtifact`). Default `basic`.                                                             |
| `color_params`        | `OCIOColorParamsArtifact`                  | Connected colour parameters. Required when `color_mode` is `ocio`; hidden in `basic` mode.                                                                 |
| `tone_mapping`        | `str`                                      | `sRGB`, `rec709`, `rec1886`, or `raw`. Default `sRGB`. See [Display transforms](rendering.md#display-transforms).                                          |
| `embed_color_profile` | `bool`                                     | Embed an ICC profile describing the display transform. Default `false`. See [Colour profiles](rendering.md#colour-profiles).                               |
| `background`          | `ImageArtifact \| ImageUrlArtifact \| str` | Background image for A-over-B compositing. Ignored when no alpha channel is connected.                                                                     |

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

Display EXR Channel checks output and intermediate assembly sizes against `openexr.max_render_pixels`. See [Library Settings](../README.md#library-settings) for the default and how to raise it.

## Save EXR

Saves a single-part EXR file from either an 8-bit image or EXR channel artifacts. Operates in two modes:

- **Image input**: accepts an `ImageArtifact` or `ImageUrlArtifact` (8-bit PNG/JPEG), normalises pixel values from [0, 255] to [0.0, 1.0], and writes R, G, B channels.
- **Channel input**: accepts up to four `EXRChannelArtifact` slots (R, G, B, A), loads their pixel arrays, and writes them preserving float precision. Mode B takes priority when any channel slot is connected.

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

| Parameter     | Type              | Description                                                          |
| ------------- | ----------------- | -------------------------------------------------------------------- |
| `output_file` | `str`             | Path to the written `.exr` file                                      |
| `output_part` | `EXRPartArtifact` | Descriptor for the written part, connectable to **Display EXR Part** |

## EXR Sequence to Preview

Converts an EXR frame sequence to a single MP4/MOV preview video, applying exposure and color management per frame. Enter an EXR file path or frame pattern in `sequence`, select one with the file picker, or connect a `Sequence` from **Scan Sequence**/**Scan Split Sequence**. A connected Sequence takes precedence over a local path and keeps its existing entries and missing-frame policy. The node loads pixels eagerly to encode the video. Use **Read EXR** for new per-frame reading workflows. Keep the deprecated **Load EXR Sequence** where you need its batch descriptor list, mismatch validation or parallel header-only scans during migration.

**Inputs**

| Parameter          | Type                      | Description                                                                                                                                            |
| ------------------ | ------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `sequence`         | `Sequence` or `str`       | EXR path or frame pattern, or a connected **Scan Sequence** / **Scan Split Sequence** output.                                                          |
| `frame_rate`       | `float`                   | Output frame rate. Default `24.0`.                                                                                                                     |
| `part_index`       | `int`                     | Part index to read from every frame. Default `0`.                                                                                                      |
| `exposure`         | `float`                   | Exposure in EV stops applied before tone mapping. Default `0.0`.                                                                                       |
| `window_mode`      | `str`                     | `Full` (default), `Crop Overscan`, or `Show Overscan Boundary`, framed by each frame's own windows. See [Display window](rendering.md#display-window). |
| `color_mode`       | `str`                     | `basic` or `ocio`. Default `basic`.                                                                                                                    |
| `tone_mapping`     | `str`                     | `sRGB`, `rec709`, `rec1886`, or `raw`. Default `sRGB`. See [Display transforms](rendering.md#display-transforms).                                      |
| `color_params`     | `OCIOColorParamsArtifact` | OCIO colour parameters. Required when `color_mode` is `ocio`.                                                                                          |
| `format`           | `str`                     | Output container: `mp4` or `mov`. Default `mp4`.                                                                                                       |
| `odd_dimensions`   | `str`                     | `Pad to Even` (default) adds at most one black column/right and row/bottom. `Require Even` rejects an odd encoder canvas.                              |
| `frame_sizes`      | `str`                     | `Require Matching Sizes` (default) rejects differing rendered dimensions. `Pad to Largest` pads to maximum width and height without resizing.          |
| `missing_frames`   | `str`                     | `skip` (default), `error`, or `black`. Controls failed conversions; canvas-policy failures always stop the run.                                        |
| `processing_speed` | `str`                     | `fast`, `balanced`, or `quality`. Trades encode speed for size/quality.                                                                                |
| `threads`          | `int`                     | Worker count for per-frame conversion. Range 1-16, default `4`. In the Advanced group.                                                                 |

**Outputs**

| Parameter          | Type               | Description                                        |
| ------------------ | ------------------ | -------------------------------------------------- |
| `video`            | `VideoUrlArtifact` | The encoded preview video                          |
| `frame_count`      | `int`              | Total frames in the sequence                       |
| `frames_processed` | `int`              | Frames converted so far, updated live during a run |
| `output_file`      | `str`              | Workspace path to the written video file           |

For example, set `sequence` to `/renders/shot/beauty.####.exr`, `frame_rate` to `24`, and `format` to `mp4`, then run the node. It discovers the frames and writes a preview using the current exposure and colour settings. The path supports the same syntax as **Read EXR**, including `####`, `%04d`, and `@@@@`. A bare file path produces a one-frame video. Invalid patterns and paths with no matching frames report a failure in `result_details`.

Path discovery skips absent frame numbers, so gaps shorten the video. To preserve gaps with a different discovery policy, connect a Sequence from **Scan Sequence**. The `missing_frames` setting applies to entries that fail conversion after discovery: `skip` (default) omits them, `error` fails the run, and `black` inserts black replacements to retain their timing. Existing saved workflows with connected Sequences keep working.
