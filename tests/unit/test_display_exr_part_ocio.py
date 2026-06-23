"""Tests for DisplayEXRPart OCIO integration — discovery, dispatch, and fallback."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import numpy as np

from griptape_nodes_openexr.exr.tone_mapping import TONE_FILMIC
from griptape_nodes_openexr.nodes.display_exr_part import (
    _apply_color_management,
    _find_colorspace_transform_request_type,
)


def _rgb(h: int = 4, w: int = 4) -> np.ndarray:
    return np.ones((h, w, 3), dtype=np.float32) * 0.18


def _make_succeeded_result(pixels: np.ndarray) -> MagicMock:
    result = MagicMock()
    result.succeeded.return_value = True
    result.pixels = pixels
    return result


def _make_failed_result() -> MagicMock:
    result = MagicMock()
    result.succeeded.return_value = False
    result.pixels = None
    return result


def _library_with_req_type(req_type: type) -> MagicMock:
    lib = MagicMock()
    lib.get_registered_request_handler_types.return_value = [req_type]
    return lib


def _cst_type() -> MagicMock:
    """Return a callable mock named ColorspaceTransformRequest for name-check matching."""
    req_type = MagicMock()
    req_type.__name__ = "ColorspaceTransformRequest"
    return req_type


def _mock_lr_with(req_type: MagicMock) -> MagicMock:
    """LibraryRegistry mock that has one library containing req_type."""
    mock_lr = MagicMock()
    mock_lr.list_libraries.return_value = ["OpenColorIO Library"]
    mock_lr.get_library.return_value = _library_with_req_type(req_type)
    return mock_lr


def _mock_lr_empty() -> MagicMock:
    mock_lr = MagicMock()
    mock_lr.list_libraries.return_value = []
    return mock_lr


# ---------------------------------------------------------------------------
# _find_colorspace_transform_request_type
# ---------------------------------------------------------------------------


class TestFindColorspaceTransformRequestType:
    def test_returns_type_when_found(self) -> None:
        req_type = _cst_type()

        with patch(
            "griptape_nodes_openexr.nodes.display_exr_part.LibraryRegistry",
            _mock_lr_with(req_type),
        ):
            result = _find_colorspace_transform_request_type()

        assert result is req_type

    def test_returns_none_when_registry_empty(self) -> None:
        with patch(
            "griptape_nodes_openexr.nodes.display_exr_part.LibraryRegistry",
            _mock_lr_empty(),
        ):
            result = _find_colorspace_transform_request_type()

        assert result is None

    def test_returns_none_on_exception(self) -> None:
        mock_lr = MagicMock()
        mock_lr.list_libraries.side_effect = RuntimeError("engine not running")

        with patch(
            "griptape_nodes_openexr.nodes.display_exr_part.LibraryRegistry",
            mock_lr,
        ):
            result = _find_colorspace_transform_request_type()

        assert result is None


# ---------------------------------------------------------------------------
# _apply_color_management
# ---------------------------------------------------------------------------


class TestApplyColorManagement:
    def test_ocio_used_when_all_params_set(self) -> None:
        rgb = _rgb()
        expected = _rgb() * 2.0
        req_type = _cst_type()
        mock_gn = MagicMock()
        mock_gn.handle_request.return_value = _make_succeeded_result(expected)

        with (
            patch("griptape_nodes_openexr.nodes.display_exr_part.LibraryRegistry", _mock_lr_with(req_type)),
            patch("griptape_nodes_openexr.nodes.display_exr_part.GriptapeNodes", mock_gn),
        ):
            pixels, label = _apply_color_management(rgb, "ACEScg", "sRGB", "ACES", TONE_FILMIC)

        mock_gn.handle_request.assert_called_once()
        assert pixels is expected
        assert label.startswith("ocio:")

    def test_mode_label_contains_source_display_view(self) -> None:
        rgb = _rgb()
        req_type = _cst_type()
        mock_gn = MagicMock()
        mock_gn.handle_request.return_value = _make_succeeded_result(_rgb())

        with (
            patch("griptape_nodes_openexr.nodes.display_exr_part.LibraryRegistry", _mock_lr_with(req_type)),
            patch("griptape_nodes_openexr.nodes.display_exr_part.GriptapeNodes", mock_gn),
        ):
            _, label = _apply_color_management(rgb, "ACEScg", "sRGB", "ACES", TONE_FILMIC)

        assert "ACEScg" in label
        assert "sRGB" in label
        assert "ACES" in label

    def test_fallback_on_type_error(self) -> None:
        rgb = _rgb()
        req_type = _cst_type()
        mock_gn = MagicMock()
        mock_gn.handle_request.side_effect = TypeError("No manager found")

        with (
            patch("griptape_nodes_openexr.nodes.display_exr_part.LibraryRegistry", _mock_lr_with(req_type)),
            patch("griptape_nodes_openexr.nodes.display_exr_part.GriptapeNodes", mock_gn),
        ):
            pixels, label = _apply_color_management(rgb, "ACEScg", "sRGB", "ACES", TONE_FILMIC)

        assert label == TONE_FILMIC
        assert not label.startswith("ocio:")

    def test_fallback_on_failed_result(self) -> None:
        rgb = _rgb()
        req_type = _cst_type()
        mock_gn = MagicMock()
        mock_gn.handle_request.return_value = _make_failed_result()

        with (
            patch("griptape_nodes_openexr.nodes.display_exr_part.LibraryRegistry", _mock_lr_with(req_type)),
            patch("griptape_nodes_openexr.nodes.display_exr_part.GriptapeNodes", mock_gn),
        ):
            pixels, label = _apply_color_management(rgb, "ACEScg", "sRGB", "ACES", TONE_FILMIC)

        assert label == TONE_FILMIC

    def test_ocio_skipped_when_display_missing(self) -> None:
        rgb = _rgb()
        mock_gn = MagicMock()

        with patch("griptape_nodes_openexr.nodes.display_exr_part.GriptapeNodes", mock_gn):
            pixels, label = _apply_color_management(rgb, "ACEScg", "", "ACES", TONE_FILMIC)

        mock_gn.handle_request.assert_not_called()
        assert label == TONE_FILMIC

    def test_ocio_skipped_when_source_colorspace_missing(self) -> None:
        rgb = _rgb()
        mock_gn = MagicMock()

        with patch("griptape_nodes_openexr.nodes.display_exr_part.GriptapeNodes", mock_gn):
            pixels, label = _apply_color_management(rgb, "", "sRGB", "ACES", TONE_FILMIC)

        mock_gn.handle_request.assert_not_called()
        assert label == TONE_FILMIC

    def test_ocio_skipped_when_req_type_not_found(self) -> None:
        rgb = _rgb()
        mock_gn = MagicMock()

        with (
            patch("griptape_nodes_openexr.nodes.display_exr_part.LibraryRegistry", _mock_lr_empty()),
            patch("griptape_nodes_openexr.nodes.display_exr_part.GriptapeNodes", mock_gn),
        ):
            pixels, label = _apply_color_management(rgb, "ACEScg", "sRGB", "ACES", TONE_FILMIC)

        mock_gn.handle_request.assert_not_called()
        assert label == TONE_FILMIC

    def test_mode_label_is_tone_mapping_on_fallback(self) -> None:
        rgb = _rgb()
        mock_gn = MagicMock()

        with (
            patch("griptape_nodes_openexr.nodes.display_exr_part.LibraryRegistry", _mock_lr_empty()),
            patch("griptape_nodes_openexr.nodes.display_exr_part.GriptapeNodes", mock_gn),
        ):
            _, label = _apply_color_management(rgb, "ACEScg", "sRGB", "ACES", TONE_FILMIC)

        assert label == TONE_FILMIC
