"""Pure formatting of EXR header metadata for scalar node outputs."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from griptape_nodes_openexr.exr.exr_types import _convert_attribute_value

if TYPE_CHECKING:
    from griptape_nodes_openexr.exr.exr_types import EXRHeader

HEADER_METADATA_DEFAULTS: dict[str, str | float] = {
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
