"""Pure formatting of EXR header metadata for scalar node outputs."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

from griptape_nodes_openexr.exr.exr_types import _convert_attribute_value

if TYPE_CHECKING:
    from griptape_nodes_openexr.exr.exr_types import EXRHeader


@dataclass(frozen=True)
class MetadataOutputDefinition:
    """Scalar output schema shared by metadata formatting and node parameters."""

    type: Literal["str", "float"]
    default: str | float
    display_name: str
    tooltip: str


HEADER_METADATA_DEFINITIONS: dict[str, MetadataOutputDefinition] = {
    "compression": MetadataOutputDefinition(
        type="str",
        default="",
        display_name="Compression",
        tooltip="Compression enum name, e.g. ZIP_COMPRESSION. Empty when cleared. "
        "Describes the selected part of the selected frame.",
    ),
    "storage_type": MetadataOutputDefinition(
        type="str",
        default="",
        display_name="Storage Type",
        tooltip="Storage mode: scanlineimage, tiledimage, deepscanline or deeptiled. "
        "Describes the selected part of the selected frame.",
    ),
    "pixel_aspect_ratio": MetadataOutputDefinition(
        type="float",
        default=1.0,
        display_name="Pixel Aspect Ratio",
        tooltip="Source pixel width/height ratio; defaults to 1.0. Describes the selected part of the selected frame.",
    ),
    "data_window": MetadataOutputDefinition(
        type="str",
        default="",
        display_name="Data Window",
        tooltip="Source pixel bounds as xmin,ymin - xmax,ymax. Describes the selected part of the selected frame.",
    ),
    "display_window": MetadataOutputDefinition(
        type="str",
        default="",
        display_name="Display Window",
        tooltip="Source display bounds as xmin,ymin - xmax,ymax. Describes the selected part of the selected frame.",
    ),
    "time_code": MetadataOutputDefinition(
        type="str",
        default="",
        display_name="Time Code",
        tooltip="Source timecode text; empty if absent. Describes the selected part of the selected frame.",
    ),
    "software": MetadataOutputDefinition(
        type="str",
        default="",
        display_name="Software",
        tooltip="Authoring application; empty if absent. Describes the selected part of the selected frame.",
    ),
    "owner": MetadataOutputDefinition(
        type="str",
        default="",
        display_name="Owner",
        tooltip="Asset owner; empty if absent. Describes the selected part of the selected frame.",
    ),
    "chromaticities": MetadataOutputDefinition(
        type="str",
        default="",
        display_name="Chromaticities",
        tooltip="Colour primaries as JSON with red_x/y, green_x/y, blue_x/y and white_x/y; empty if absent. "
        "Describes the selected part of the selected frame.",
    ),
    "custom_attributes": MetadataOutputDefinition(
        type="str",
        default="{}",
        display_name="Custom Attributes",
        tooltip="Non-standard header attributes as JSON; {} if absent. "
        "Describes the selected part of the selected frame.",
    ),
}

HEADER_METADATA_DEFAULTS: dict[str, str | float] = {
    name: definition.default for name, definition in HEADER_METADATA_DEFINITIONS.items()
}


def header_metadata_outputs(header: EXRHeader) -> dict[str, str | float]:
    """Return fresh scalar outputs using LoadEXR's window and JSON conventions.

    Custom attributes are already normalised by the header scanner. Keep its
    values and use string fallback for values supplied by other callers.
    Dimensions and file-level counts are not header metadata outputs.
    """
    data_window = header.data_window
    display_window = header.display_window
    chromaticities = ""
    if header.chromaticities:
        c = header.chromaticities
        chroma_dict = {
            "red_x": c.red_x,
            "red_y": c.red_y,
            "green_x": c.green_x,
            "green_y": c.green_y,
            "blue_x": c.blue_x,
            "blue_y": c.blue_y,
            "white_x": c.white_x,
            "white_y": c.white_y,
        }
        chromaticities = json.dumps({key: _convert_attribute_value(value) for key, value in chroma_dict.items()})

    return {
        "compression": header.compression.value,
        "storage_type": header.storage_type.value,
        "pixel_aspect_ratio": header.pixel_aspect_ratio,
        "data_window": f"{data_window.xmin},{data_window.ymin} - {data_window.xmax},{data_window.ymax}",
        "display_window": f"{display_window.xmin},{display_window.ymin} - {display_window.xmax},{display_window.ymax}",
        "time_code": header.time_code or "",
        "software": header.software or "",
        "owner": header.owner or "",
        "chromaticities": chromaticities,
        "custom_attributes": json.dumps(header.custom, indent=2, default=str) if header.custom else "{}",
    }
