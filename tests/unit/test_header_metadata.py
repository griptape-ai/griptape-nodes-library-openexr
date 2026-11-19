"""Header metadata formatting and LoadEXR compatibility tests."""

from __future__ import annotations

import copy
import json
from dataclasses import FrozenInstanceError
from unittest.mock import patch

import numpy as np
import pytest

from griptape_nodes_openexr.exr.exr_types import (
    Chromaticities,
    CompressionType,
    EXRChannelInfo,
    EXRData,
    EXRHeader,
    EXRPart,
    LineOrderType,
    PixelType,
    StorageType,
    WindowCoordinates,
)
from griptape_nodes_openexr.exr.header_metadata import (
    HEADER_METADATA_DEFAULTS,
    HEADER_METADATA_DEFINITIONS,
    MetadataOutputDefinition,
    header_metadata_outputs,
)
from griptape_nodes_openexr.nodes.load_exr import LoadEXR


@pytest.fixture()
def header() -> EXRHeader:
    return EXRHeader(
        compression=CompressionType.ZIP_COMPRESSION,
        line_order=LineOrderType.INCREASING_Y,
        data_window=WindowCoordinates(-8, -4, 55, 27),
        display_window=WindowCoordinates(0, 0, 47, 23),
        pixel_aspect_ratio=1.5,
        screen_window_center=(0.0, 0.0),
        screen_window_width=1.0,
        storage_type=StorageType.TILED_IMAGE,
        name="beauty",
        chunk_count=None,
        tile_description=None,
        chromaticities=None,
        time_code=None,
        owner=None,
        comments=None,
        capture_date=None,
        software=None,
        custom={},
    )


def test_defaults_match_existing_empty_outputs() -> None:
    assert HEADER_METADATA_DEFAULTS == {
        "compression": "",
        "storage_type": "",
        "pixel_aspect_ratio": 1.0,
        "data_window": "",
        "display_window": "",
        "time_code": "",
        "software": "",
        "owner": "",
        "chromaticities": "",
        "custom_attributes": "{}",
    }


def test_definitions_preserve_explicit_types_labels_and_tooltips() -> None:
    formats = {
        "compression": "Compression enum name, e.g. ZIP_COMPRESSION. Empty when cleared.",
        "storage_type": "Storage mode: scanlineimage, tiledimage, deepscanline or deeptiled.",
        "pixel_aspect_ratio": "Source pixel width/height ratio; defaults to 1.0.",
        "data_window": "Source pixel bounds as xmin,ymin - xmax,ymax.",
        "display_window": "Source display bounds as xmin,ymin - xmax,ymax.",
        "time_code": "Source timecode text; empty if absent.",
        "software": "Authoring application; empty if absent.",
        "owner": "Asset owner; empty if absent.",
        "chromaticities": "Colour primaries as JSON with red_x/y, green_x/y, blue_x/y and white_x/y; empty if absent.",
        "custom_attributes": "Non-standard header attributes as JSON; {} if absent.",
    }
    assert list(HEADER_METADATA_DEFINITIONS) == list(HEADER_METADATA_DEFAULTS)
    for name, definition in HEADER_METADATA_DEFINITIONS.items():
        assert isinstance(definition, MetadataOutputDefinition)
        assert definition.type == ("float" if name == "pixel_aspect_ratio" else "str")
        assert type(definition.default) is (float if definition.type == "float" else str)
        assert definition.default == HEADER_METADATA_DEFAULTS[name]
        assert definition.display_name == name.replace("_", " ").title()
        assert definition.tooltip == f"{formats[name]} Describes the selected part of the selected frame."


def test_defaults_are_derived_from_definitions() -> None:
    assert HEADER_METADATA_DEFAULTS == {
        name: definition.default for name, definition in HEADER_METADATA_DEFINITIONS.items()
    }


def test_definition_is_frozen() -> None:
    definition = MetadataOutputDefinition(type="str", default="", display_name="Owner", tooltip="Asset owner")
    with pytest.raises(FrozenInstanceError):
        definition.default = "changed"  # pyright: ignore[reportAttributeAccessIssue]


def test_formats_required_fields_and_absent_metadata(header: EXRHeader) -> None:
    outputs = header_metadata_outputs(header)
    assert outputs == {
        **HEADER_METADATA_DEFAULTS,
        "compression": "ZIP_COMPRESSION",
        "storage_type": "tiledimage",
        "pixel_aspect_ratio": 1.5,
        "data_window": "-8,-4 - 55,27",
        "display_window": "0,0 - 47,23",
    }
    assert all(isinstance(value, (str, float)) for value in outputs.values())


@pytest.mark.parametrize("value", [None, "", "authored metadata"])
def test_optional_strings(header: EXRHeader, value: str | None) -> None:
    header.time_code = header.software = header.owner = value
    outputs = header_metadata_outputs(header)
    for name in ("time_code", "software", "owner"):
        assert outputs[name] == (value or "")


def test_chromaticities_json_preserves_keys_order_and_nonfinite_conversion(header: EXRHeader) -> None:
    header.chromaticities = Chromaticities(0.64, 0.33, 0.3, 0.6, 0.15, float("nan"), float("inf"), -float("inf"))
    outputs = header_metadata_outputs(header)
    assert outputs["chromaticities"] == (
        '{"red_x": 0.64, "red_y": 0.33, "green_x": 0.3, "green_y": 0.6, '
        '"blue_x": 0.15, "blue_y": "NaN", "white_x": "Infinity", "white_y": "-Infinity"}'
    )


def test_custom_json_preserves_indent_order_and_string_fallback(header: EXRHeader) -> None:
    # Custom values arrive normalised by the scanner; preserve default=str for other callers.
    header.custom = {"artist": "Ada", "samples": np.array([1, 2]), "gain": "Infinity"}
    assert header_metadata_outputs(header)["custom_attributes"] == (
        '{\n  "artist": "Ada",\n  "samples": "[1 2]",\n  "gain": "Infinity"\n}'
    )
    assert json.loads(str(header_metadata_outputs(header)["custom_attributes"]))["gain"] == "Infinity"


def test_formatter_does_not_mutate_header_or_defaults_and_returns_fresh_dict(header: EXRHeader) -> None:
    header.custom = {"nested": {"values": [1, 2]}}
    original_header = copy.deepcopy(header)
    original_defaults = HEADER_METADATA_DEFAULTS.copy()
    first = header_metadata_outputs(header)
    first["owner"] = "changed"
    assert header_metadata_outputs(header)["owner"] == ""
    assert header == original_header
    assert HEADER_METADATA_DEFAULTS == original_defaults


def test_load_exr_delegates_without_changing_dimensions_or_counts(header: EXRHeader) -> None:
    channel = EXRChannelInfo("R", PixelType.HALF, 1, 1)
    data = EXRData(
        [
            EXRPart("beauty", [channel], header, 64, 32),
            EXRPart("other", [channel, channel], header, 12, 9),
        ]
    )
    node = LoadEXR("test_metadata")
    formatted = {**HEADER_METADATA_DEFAULTS, "owner": "formatter sentinel"}
    with patch("griptape_nodes_openexr.nodes.load_exr.header_metadata_outputs", return_value=formatted) as formatter:
        node._populate_scalar_outputs(data)
    formatter.assert_called_once_with(header)
    for name, value in formatted.items():
        assert node.parameter_output_values[name] == value
    assert node.parameter_output_values["image_width"] == 64
    assert node.parameter_output_values["image_height"] == 32
    assert node.parameter_output_values["part_count"] == 2
    assert node.parameter_output_values["channel_count"] == 3


def test_load_exr_clear_preserves_defaults(header: EXRHeader) -> None:
    node = LoadEXR("test_clear_metadata")
    node._populate_scalar_outputs(EXRData([EXRPart("beauty", [], header, 64, 32)]))
    node._clear_static_outputs()
    for name, value in HEADER_METADATA_DEFAULTS.items():
        assert node.parameter_output_values[name] == value
    for name in ("image_width", "image_height", "part_count", "channel_count"):
        assert node.parameter_output_values[name] == 0
    assert node.parameter_output_values["parts"] == []
