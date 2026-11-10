"""The OpenEXR binding enum -> StrEnum maps cover every value the binding can return."""

from __future__ import annotations

from typing import Any

import OpenEXR
import pytest

from griptape_nodes_openexr.exr.exr_io import _map_exr_enum
from griptape_nodes_openexr.exr.exr_types import (
    _EXR_COMPRESSION_MAP,
    _EXR_LEVEL_MODE_MAP,
    _EXR_LEVEL_ROUNDING_MODE_MAP,
    _EXR_LINE_ORDER_MAP,
    _EXR_PIXEL_TYPE_MAP,
    _EXR_STORAGE_TYPE_MAP,
    CompressionType,
)


@pytest.mark.parametrize(
    ("binding_enum", "mapping"),
    [
        (OpenEXR.Compression, _EXR_COMPRESSION_MAP),
        (OpenEXR.PixelType, _EXR_PIXEL_TYPE_MAP),
        (OpenEXR.LineOrder, _EXR_LINE_ORDER_MAP),
        (OpenEXR.Storage, _EXR_STORAGE_TYPE_MAP),
        (OpenEXR.LevelMode, _EXR_LEVEL_MODE_MAP),
        (OpenEXR.LevelRoundingMode, _EXR_LEVEL_ROUNDING_MODE_MAP),
    ],
)
def test_map_covers_every_binding_value(binding_enum: Any, mapping: dict[Any, Any]) -> None:
    # NUM_* members are count sentinels, not values a file can carry.
    values = {value for name, value in binding_enum.__members__.items() if not name.startswith("NUM_")}
    assert values - mapping.keys() == set()


def test_map_exr_enum_returns_mapped_value() -> None:
    assert _map_exr_enum(_EXR_COMPRESSION_MAP, OpenEXR.ZSTD_COMPRESSION, "compression") == (
        CompressionType.ZSTD_COMPRESSION
    )


def test_map_exr_enum_raises_on_unmapped_value() -> None:
    with pytest.raises(ValueError, match="Unsupported EXR compression"):
        _map_exr_enum(_EXR_COMPRESSION_MAP, OpenEXR.Compression.NUM_COMPRESSION_METHODS, "compression")
