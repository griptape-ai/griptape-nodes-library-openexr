"""The OpenEXR binding enum -> StrEnum maps cover every value the binding can return."""

from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Any

import OpenEXR
import pytest

from griptape_nodes_openexr.exr import exr_types
from griptape_nodes_openexr.exr.exr_io import _map_exr_enum
from griptape_nodes_openexr.exr.exr_types import (
    _EXR_COMPRESSION_MAP,
    _EXR_LEVEL_MODE_MAP,
    _EXR_LEVEL_ROUNDING_MODE_MAP,
    _EXR_LINE_ORDER_MAP,
    _EXR_PIXEL_TYPE_MAP,
    _EXR_STORAGE_TYPE_MAP,
    CompressionType,
    _map_by_name,
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


def _compression_without_lj2k() -> Any:
    # A compiled binding that predates LJ2K_COMPRESSION, as an OpenEXR 3.4 build does.
    members = {name: value for name, value in OpenEXR.Compression.__members__.items() if name != "LJ2K_COMPRESSION"}
    return type("Compression", (), {"__members__": members})


def test_stale_binding_missing_a_member_raises_reinstall_instructions(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    venv = tmp_path / "venv"
    site_packages = venv / "Lib" / "site-packages"
    site_packages.mkdir(parents=True)
    (venv / "pyvenv.cfg").write_text("", encoding="utf-8")
    monkeypatch.setattr(OpenEXR, "__version__", "3.4.6")
    monkeypatch.setattr(OpenEXR, "__file__", str(site_packages / "OpenEXR.pyd"))

    with pytest.raises(ImportError) as excinfo:
        _map_by_name(_compression_without_lj2k(), CompressionType)

    message = str(excinfo.value)
    assert "version 3.4.6" in message
    assert "OpenEXR.Compression value LJ2K_COMPRESSION" in message
    assert "needs openexr>=3.5.3" in message
    assert "Stop Griptape Nodes" in message
    assert f'uv pip install --python "{venv}" --force-reinstall "openexr>=3.5.3"' in message


def test_binding_at_required_version_missing_a_member_asks_for_a_library_update(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(OpenEXR, "__version__", "3.6.0")

    with pytest.raises(ImportError, match="does not support OpenEXR 3.6.0 yet") as excinfo:
        _map_by_name(_compression_without_lj2k(), CompressionType)

    assert "--force-reinstall" not in str(excinfo.value)


def test_openexr_requirement_matches_pyproject() -> None:
    pyproject = tomllib.loads((Path(__file__).parents[2] / "pyproject.toml").read_text(encoding="utf-8"))
    dependencies = [dep.lower() for dep in pyproject["project"]["dependencies"]]
    assert exr_types.OPENEXR_REQUIREMENT in dependencies


def test_map_exr_enum_returns_mapped_value() -> None:
    assert _map_exr_enum(_EXR_COMPRESSION_MAP, OpenEXR.ZSTD_COMPRESSION, "compression") == (
        CompressionType.ZSTD_COMPRESSION
    )


def test_map_exr_enum_raises_on_unmapped_value() -> None:
    with pytest.raises(ValueError, match="Unsupported EXR compression"):
        _map_exr_enum(_EXR_COMPRESSION_MAP, OpenEXR.Compression.NUM_COMPRESSION_METHODS, "compression")
