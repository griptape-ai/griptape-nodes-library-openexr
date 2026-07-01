"""Shared mock helpers for OCIO unit tests."""

from __future__ import annotations

from unittest.mock import MagicMock

import numpy as np


def _rgb(h: int = 4, w: int = 4) -> np.ndarray:
    return np.ones((h, w, 3), dtype=np.float32) * 0.18


def _make_succeeded_result(pixels: np.ndarray) -> MagicMock:
    result = MagicMock()
    result.succeeded.return_value = True
    result.pixels = pixels
    return result


def _make_failed_result(detail: str = "error") -> MagicMock:
    result = MagicMock()
    result.succeeded.return_value = False
    result.result_details = detail
    return result


def _library_with_req_type(req_type: type) -> MagicMock:
    lib = MagicMock()
    lib.get_registered_request_handler_types.return_value = [req_type]
    return lib


def _cst_type() -> MagicMock:
    req_type = MagicMock()
    req_type.__name__ = "ColorspaceTransformRequest"
    return req_type


def _mock_lr_with(req_type: MagicMock) -> MagicMock:
    mock_lr = MagicMock()
    mock_lr.list_libraries.return_value = ["OpenColorIO Library"]
    mock_lr.get_library.return_value = _library_with_req_type(req_type)
    return mock_lr


def _mock_lr_empty() -> MagicMock:
    mock_lr = MagicMock()
    mock_lr.list_libraries.return_value = []
    return mock_lr


def _color_params(
    source: str = "ACEScg",
    display: str = "sRGB",
    view: str = "ACES",
    config_path: str | None = None,
) -> MagicMock:
    artifact = MagicMock()
    artifact.source_colorspace = source
    artifact.display = display
    artifact.view = view
    artifact.config_path = config_path
    return artifact
