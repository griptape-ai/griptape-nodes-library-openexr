# Development

## Requirements

- Python 3.12
- [uv](https://docs.astral.sh/uv/)

## Setup

```bash
make install/dev
```

Use `make install/dev` rather than `uv sync` directly. Development tooling belongs in `.venv-dev`. Plain `uv sync` would install it into the `.venv` the engine builds beside the manifest, causing the library to load as FLAWED. See [CONTRIBUTING.md](../CONTRIBUTING.md#two-virtual-environments).

## Running tests

```bash
# Unit tests only (no EXR files required)
make test/unit

# Integration tests (fixtures auto-generated on first run)
make test/integration

# Full suite
make test
```

## Lint and type check

```bash
make check/lint
make check/types
```

A direct `uv run` needs `UV_PROJECT_ENVIRONMENT=.venv-dev`, for the reason above.

## Test fixtures

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
