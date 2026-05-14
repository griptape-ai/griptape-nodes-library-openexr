# OpenEXR Library for Griptape Nodes

Professional OpenEXR support for Griptape Nodes, designed for VFX and HDR pipelines. Parses EXR headers, extracts metadata, and exposes per-part and per-layer structure for downstream nodes - without loading pixel data.

## Nodes

### Read EXR Header

Parses an EXR file's header and exposes its full structure as typed outputs. No pixel data is ever loaded, making it fast even on large multi-part renders.

**Inputs**

| Parameter | Description |
|---|---|
| `file_path` | Path to the `.exr` file |
| `channel_style` | How channels are parsed and grouped into layers (see below) |

**Outputs**

| Parameter | Type | Description |
|---|---|---|
| `exr_header` | `EXRHeaderArtifact` | Full structured metadata descriptor |
| `all_parts` | `list[EXRPartHeaderArtifact]` | Per-part descriptors |
| `all_layers` | `list[EXRLayerArtifact]` | All layers across all parts |
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

- **Parts** - one `EXRPartHeaderArtifact` output per part (hidden for single-part files)
- **Layers** - one `EXRLayerArtifact` per layer, labelled `beauty (R, G, B, A)`
- **Channels** - one output per raw channel with name, pixel type, and sampling

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

## Extending: Custom Channel Styles

Studios can add their own channel grouping strategies without modifying this library.

### 1. Implement the strategy

```python
# my_studio/exr_strategy.py
from griptape_nodes_openexr.exr.exr_types import (
    ChannelNameParts,
    EXRChannelInfo,
    EXRLayer,
    EXRPart,
)


class MyStudioChannelGrouping:
    name = "my_studio"
    display_name = "My Studio"

    def parse_channel(self, full_name: str) -> ChannelNameParts:
        # Example: studio convention uses '_' as the layer separator
        # e.g. "beauty_R" → layer "beauty", channel "R"
        if "_" in full_name:
            layer, _, channel = full_name.rpartition("_")
            return ChannelNameParts(layer_name=layer, channel_name=channel)
        return ChannelNameParts(layer_name="", channel_name=full_name)

    def group_into_layers(self, channels: list[EXRChannelInfo]) -> list[EXRLayer]:
        layers: dict[str, list[EXRChannelInfo]] = {}
        for ch in channels:
            layer_name = self.parse_channel(ch.name).layer_name
            layers.setdefault(layer_name, []).append(ch)
        result = [EXRLayer(name=name, channels=chs) for name, chs in layers.items()]
        result.sort(key=lambda l: (l.name != "", l.name))
        return result

    def postprocess_parts(self, parts: list[EXRPart]) -> None:
        pass  # No additional post-processing needed
```

### 2. Register it in a JSON config

```json
{
  "strategies": [
    {
      "name": "my_studio",
      "display_name": "My Studio",
      "module": "my_studio.exr_strategy",
      "class": "MyStudioChannelGrouping"
    }
  ]
}
```

### 3. Add the config path in Griptape Nodes settings

Open Griptape Nodes → **Settings → OpenEXR**. Add the absolute path to your JSON file in the `openexr_configs` list. Your strategy will appear in the `channel_style` dropdown on every `Read EXR Header` node after restarting. Multiple strategies can be registered in the same JSON file.

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

# Full suite including integration tests
uv run pytest tests/
```

### Lint and Type Check

```bash
uv run ruff check .
uv run pyright
```

### Test Fixtures

EXR fixtures live in `tests/data/`. The current set covers:

| File | Purpose |
|---|---|
| `single_part_rgba.exr` | Baseline single-part RGBA |
| `single_part_aovs.exr` | Multi-layer AOVs (`beauty`, `diffuse`, `depth`, `normal`) |
| `multi_part.exr` | Three named parts with legacy channel naming |
| `tiled.exr` | Tiled image with `TileDescription` |
| `overscan.exr` | Data window extends beyond display window (8px overscan) |
| `custom_attributes.exr` | Chromaticities, software, and non-standard attributes |
| `nuke_metadata.exr` | Written by Nuke 17 Write node |
| `legacy_multipart.exr` | Multi-part where part name encodes the layer name |
