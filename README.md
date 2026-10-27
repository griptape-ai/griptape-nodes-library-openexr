# OpenEXR Library for Griptape Nodes

Professional OpenEXR support for Griptape Nodes, designed for VFX and HDR pipelines. Read an EXR file or frame sequence straight to a viewable image, inspect a file's structure, load an EXR frame sequence, display individual parts or manually-assembled channels as tone-mapped images, convert a sequence to a preview video, and save images or channel data back to EXR.

Start with **Read EXR**. The other nodes are there when you need the structure, the lower-level sequence controls, or explicit per-channel wiring.

## Nodes

### Read EXR

Reads an EXR file or frame sequence and outputs a viewable image from a single node. This is the front door for most work: **Load EXR** -> **Get From List** -> **Display EXR Part** collapses into one node here. Modelled on Nuke's Read node.

The `path` input accepts either a path or a connected `Sequence`. A bare filename comes back from the scan as a one-frame sequence, so a single file and a 200-frame render use the same input and the same wiring.

An **Open in external viewer** button opens the source EXR in a configured HDR viewer (or the OS default) after the node has been run. See [Library Settings](#library-settings).

**Inputs**

| Parameter      | Type              | Description                                                                                                                  |
| -------------- | ----------------- | ---------------------------------------------------------------------------------------------------------------------------- |
| `path`         | `Sequence \| str` | Path to an `.exr` file, or a frame pattern such as `render.####.exr`. A connected `Sequence` is used as-is.                  |
| `part_name`    | `str`             | Which part to read, as `"0: rgba"`, `"1: depth"`. Populated after the scan, hidden for single-part files.                    |
| `layer`        | `str`             | Which layer of the part to render. Populated after the scan, hidden when the part has one layer.                             |
| `frame`        | `int`             | Absolute frame number, matching the filenames on disk. Hidden for a single file.                                             |
| `exposure`     | `float`           | Exposure in EV stops applied before tone mapping. Range -10 to +10, default `0.0`. In the Color Management group.            |
| `color_mode`   | `str`             | `basic` (local tone mapping) or `ocio` (connected `OCIOColorParamsArtifact`). Defaults to `ocio` when OpenColorIO is loaded. |
| `tone_mapping` | `str`             | `filmic` (Narkowicz 2015 curve) or `linear` (clamp to [0, 1]). Default `filmic`. Used when `color_mode` is `basic`.          |

**Outputs**

| Parameter                      | Type                       | Description                                                       |
| ------------------------------ | -------------------------- | ----------------------------------------------------------------- |
| `image`                        | `ImageUrlArtifact`         | 8-bit sRGB (or RGBA) PNG of the current frame                     |
| `part`                         | `EXRPartArtifact`          | Descriptor for the part that was read, carrying the float data on |
| `channels`                     | `list[EXRChannelArtifact]` | One descriptor per channel in that part                           |
| `output_file`                  | `str`                      | Path to the saved PNG                                             |
| `image_width` / `image_height` | `int`                      | Dimensions from the data window                                   |
| `part_count` / `channel_count` | `int`                      | Parts in the file, channels in the part that was read             |
| `first_frame` / `last_frame`   | `int`                      | Sequence bounds; `0` for a single file                            |
| `frame_count`                  | `int`                      | Frames in the sequence; `1` for a single file                     |

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

Loads an EXR file and exposes its full structure as typed outputs. Pixel loading is off by default (`header_only=True`), making it fast even on large multi-part renders. This is configurable via the `openexr.header_only` engine setting.

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

Renders an EXR part to an 8-bit sRGB (or RGBA) PNG. Accepts an `EXRPartArtifact` from **Load EXR**, auto-selects RGB display channels, applies exposure and tone mapping, and writes the result as an `ImageUrlArtifact`.

An **Open in external viewer** button opens the source EXR in a configured HDR viewer (or the OS default) after the node has been run. See [Library Settings](#library-settings).

**Inputs**

| Parameter      | Type              | Description                                                                                                         |
| -------------- | ----------------- | ------------------------------------------------------------------------------------------------------------------- |
| `part`         | `EXRPartArtifact` | EXR part to render. Assumes scene-linear HDR data; no gamut conversion is applied.                                  |
| `exposure`     | `float`           | Exposure in EV stops applied before tone mapping. Range −10 to +10, default `0.0`.                                  |
| `tone_mapping` | `str`             | `filmic` (Narkowicz 2015 curve) or `linear` (clamp to [0, 1]). Both are sRGB-encoded for display. Default `filmic`. |

**Outputs**

| Parameter     | Type               | Description                                  |
| ------------- | ------------------ | -------------------------------------------- |
| `image`       | `ImageUrlArtifact` | 8-bit sRGB or RGBA PNG for in-canvas display |
| `output_file` | `str`              | Path to the saved PNG file                   |

Alpha is included automatically when an alpha channel (`A`) is found in the part.

______________________________________________________________________

### Display EXR Channel

Combines 1–4 individual `EXRChannelArtifact` inputs into an 8-bit sRGB or RGBA PNG. An **Open in external viewer** button opens the source EXR after the node has been run. See [Library Settings](#library-settings). Each slot (R, G, B, A) is optional and can come from different EXR files or different parts. Missing RGB slots are zero-filled; connecting the A slot produces RGBA output with optional background compositing.

**Inputs**

| Parameter      | Type                                       | Description                                                                                                         |
| -------------- | ------------------------------------------ | ------------------------------------------------------------------------------------------------------------------- |
| `channel_r`    | `EXRChannelArtifact`                       | Channel mapped to the red plane. Optional.                                                                          |
| `channel_g`    | `EXRChannelArtifact`                       | Channel mapped to the green plane. Optional.                                                                        |
| `channel_b`    | `EXRChannelArtifact`                       | Channel mapped to the blue plane. Optional.                                                                         |
| `channel_a`    | `EXRChannelArtifact`                       | Channel used as alpha. Optional; when connected, output is RGBA.                                                    |
| `exposure`     | `float`                                    | Exposure in EV stops applied before tone mapping. Range −10 to +10, default `0.0`.                                  |
| `tone_mapping` | `str`                                      | `filmic` (Narkowicz 2015 curve) or `linear` (clamp to [0, 1]). Both are sRGB-encoded for display. Default `filmic`. |
| `background`   | `ImageArtifact \| ImageUrlArtifact \| str` | Background image for A-over-B compositing. Ignored when no alpha channel is connected.                              |

**Outputs**

| Parameter     | Type               | Description                                  |
| ------------- | ------------------ | -------------------------------------------- |
| `image`       | `ImageUrlArtifact` | 8-bit sRGB or RGBA PNG for in-canvas display |
| `output_file` | `str`              | Path to the saved PNG file                   |

At least one RGB slot must be connected. All connected channels must have the same pixel dimensions.

______________________________________________________________________

### Save EXR

Saves a single-part EXR file from either an 8-bit image or EXR channel artifacts. Operates in two modes:

- **Mode A (image input)** — accepts an `ImageArtifact` or `ImageUrlArtifact` (8-bit PNG/JPEG), normalises pixel values from [0, 255] to [0.0, 1.0], and writes R, G, B channels.
- **Mode B (channel input)** — accepts up to four `EXRChannelArtifact` slots (R, G, B, A), loads their pixel arrays, and writes them preserving float precision. Mode B takes priority when any channel slot is connected.

**Inputs**

| Parameter     | Type                                | Description                                                                                              |
| ------------- | ----------------------------------- | -------------------------------------------------------------------------------------------------------- |
| `image_in`    | `ImageArtifact \| ImageUrlArtifact` | 8-bit image source (Mode A). Ignored when any channel slot is connected.                                 |
| `channel_r`   | `EXRChannelArtifact`                | Channel to write as R (Mode B). Optional.                                                                |
| `channel_g`   | `EXRChannelArtifact`                | Channel to write as G (Mode B). Optional.                                                                |
| `channel_b`   | `EXRChannelArtifact`                | Channel to write as B (Mode B). Optional.                                                                |
| `channel_a`   | `EXRChannelArtifact`                | Channel to write as A (Mode B). Optional.                                                                |
| `compression` | `str`                               | Codec applied to the output file. Choices: `ZIP`, `ZIPS`, `PIZ`, `DWAA`, `NONE`. Default `ZIP`.          |
| `pixel_type`  | `str`                               | Pixel storage type. `HALF` (16-bit float, standard) or `FLOAT` (32-bit, full precision). Default `HALF`. |

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

| Parameter          | Type                 | Description                                                                            |
| ------------------ | -------------------- | -------------------------------------------------------------------------------------- |
| `sequence`         | `Sequence`           | Frame sequence from **Scan Sequence** / **Scan Split Sequence**                        |
| `frame_rate`       | `float`              | Output frame rate. Default `24.0`.                                                     |
| `part_index`       | `int`                | Part index to read from every frame. Default `0`.                                      |
| `exposure`         | `float`              | Exposure in EV stops applied before tone mapping. Default `0.0`.                       |
| `color_mode`       | `str`                | `Basic` or `OCIO`.                                                                     |
| `tone_mapping`     | `str`                | `filmic` or `linear`. Default `filmic`.                                                |
| `color_params`     | `OCIOConfigArtifact` | OCIO config. Required when `color_mode` is `OCIO`.                                     |
| `format`           | `str`                | Output container: `mp4` or `mov`. Default `mp4`.                                       |
| `processing_speed` | `str`                | `fast`, `balanced`, or `quality`. Trades encode speed for size/quality.                |
| `threads`          | `int`                | Worker count for per-frame conversion. Range 1-16, default `4`. In the Advanced group. |

**Outputs**

| Parameter          | Type               | Description                                        |
| ------------------ | ------------------ | -------------------------------------------------- |
| `video`            | `VideoUrlArtifact` | The encoded preview video                          |
| `frame_count`      | `int`              | Total frames in the sequence                       |
| `frames_processed` | `int`              | Frames converted so far, updated live during a run |
| `output_file`      | `str`              | Workspace path to the written video file           |

Frames that fail to convert are skipped; the run continues and the error count is reported in the result details.

## Library Settings

Settings are configured in the engine under the `openexr` category.

| Setting                     | Default | Description                                                                                                                                                                                     |
| --------------------------- | ------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `openexr.header_only`       | `true`  | When `true`, **Load EXR** reads only the file header (fast). Set to `false` to read accurate per-channel pixel types at the cost of loading pixel data into memory.                             |
| `openexr.viewer_executable` | `""`    | Full path to an external HDR viewer executable (e.g. `/usr/bin/djv`, `/Applications/Nuke15.0v1/Nuke15.0v1.app/Contents/MacOS/Nuke15.0v1`). When empty, the OS default file association is used. |
| `openexr.viewer_args`       | `""`    | Additional command-line arguments passed to the viewer before the file path, e.g. `--hdr --linear`. Parsed with shell-style quoting rules.                                                      |

Only **Load EXR** honours `openexr.header_only`. **Load EXR Sequence** always scans header-only, since its output carries no pixel data and a full scan would only buy slower, discarded reads. **Read EXR** always reads pixels, since decoding the selected part is the whole job.

The viewer is launched fire-and-forget — clicking the button never blocks the canvas.

## Development

### Requirements

- Python 3.12
- [uv](https://docs.astral.sh/uv/)

### Setup

```bash
uv sync
```

### Running Tests

```bash
# Unit tests only (no EXR files required)
uv run pytest tests/unit/

# Full suite including integration tests (fixtures auto-generated on first run)
uv run pytest tests/
```

### Lint and Type Check

```bash
uv run ruff check .
uv run pyright
```

### Test Fixtures

EXR fixtures are generated locally and are not committed to the repository. To generate them manually:

```bash
make test/fixtures
```

The fixtures are also generated automatically the first time you run `pytest`. The `openexr` Python package (included in dev dependencies) is required:

```bash
uv sync --group dev
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
