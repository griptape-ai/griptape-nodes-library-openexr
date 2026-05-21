# OpenEXR Library for Griptape Nodes

Professional OpenEXR support for Griptape Nodes, designed for VFX and HDR pipelines. Parses EXR headers, extracts metadata, and exposes per-part and per-layer structure for downstream nodes - without loading pixel data.

## Nodes

### Load EXR

Loads an EXR file and exposes its full structure as typed outputs. No pixel data is ever read, making it fast even on large multi-part renders.

**Inputs**

| Parameter | Description |
|---|---|
| `file_path` | Path to the `.exr` file |
| `channel_style` | How channels are parsed and grouped into layers (see below) |

**Outputs**

| Parameter | Type | Description |
|---|---|---|
| `parts` | `list[EXRPartArtifact]` | Per-part metadata descriptors |
| `layers` | `list[EXRDisplayChannel]` | All display channels (layers) across all parts |
| `image_width` / `image_height` | `int` | Dimensions from data window |
| `part_count` / `layer_count` / `channel_count` | `int` | Counts |
| `compression` | `str` | e.g. `ZIP_COMPRESSION`, `DWAB_COMPRESSION` |
| `storage_type` | `str` | `scanlineimage`, `tiledimage`, `deepscanline`, `deeptiled` |
| `pixel_aspect_ratio` | `float` | |
| `data_window` / `display_window` | `str` | `"xmin,ymin - xmax,ymax"` |
| `time_code` | `str` | `HH:MM:SS:FF`, empty if absent |
| `software` | `str` | Authoring application, empty if absent |
| `owner` | `str` | Asset owner, empty if absent |
| `chromaticities` | `str` | JSON with `red_x/y`, `green_x/y`, `blue_x/y`, `white_x/y` |
| `custom_attributes` | `str` | JSON of all non-standard header attributes |

Dynamic groups are also populated after scan:

- **Parts** - one `EXRPartArtifact` output per part (hidden for single-part files)
- **Layers** - one `EXRDisplayChannel` per layer, labelled `beauty (R, G, B, A)`
- **Channels** - one `EXRChannelArtifact` per raw channel with name, pixel type, and sampling

## Channel Style

The `channel_style` dropdown controls how channel names are parsed and grouped into layers. Two styles are built in:

### `nuke` (default)

Replicates Nuke's channel-name-to-layer algorithm:

- Channels are split on `.` (max two splits) - `beauty.R` → layer `beauty`, channel `R`
- Leading digits are stripped and non-alphanumeric characters replaced with `_`
- The `Ci` prefix (RenderMan default layer) maps to the unnamed default layer
- Multi-part files where no channels use dot notation have the part name prepended as a layer prefix (legacy Blender/Maya/Katana pattern)
- Display window is normalised to a `(0, 0)` origin; data window is shifted to match

### `raw`

Presents channels exactly as stored in the file - no parsing, no grouping, no coordinate changes. Each channel appears as its own single-channel layer. Useful for inspecting files from unfamiliar pipelines or debugging unexpected grouping.

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

| File | Purpose |
|---|---|
| `single_part_rgba.exr` | Baseline single-part RGBA |
| `single_part_aovs.exr` | Multi-layer AOVs (`beauty`, `diffuse`, `depth`, `normal`) |
| `multi_part.exr` | Three named parts with legacy channel naming |
| `tiled.exr` | Tiled image with `TileDescription` |
| `overscan.exr` | Data window extends beyond display window (8px overscan) |
| `custom_attributes.exr` | Chromaticities, software, and non-standard attributes |
| `nuke_metadata.exr` | Nuke-style custom binary blob attribute (`nuke/node`) |
| `legacy_multipart.exr` | Multi-part where part name encodes the layer name |
