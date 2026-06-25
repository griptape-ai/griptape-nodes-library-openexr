"""Tests for ocio_helpers — colorspace discovery, dispatch, and fail-loud behaviour."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from griptape_nodes_openexr.exr.ocio_helpers import (
    apply_color_management,
    find_colorspace_transform_request_type,
)
from griptape_nodes_openexr.exr.tone_mapping import TONE_FILMIC, TONE_LINEAR

from .conftest import (
    _color_params,
    _cst_type,
    _make_failed_result,
    _make_succeeded_result,
    _mock_lr_empty,
    _mock_lr_with,
    _rgb,
)

_OCIO_HELPERS = "griptape_nodes_openexr.exr.ocio_helpers"


# ---------------------------------------------------------------------------
# find_colorspace_transform_request_type
# ---------------------------------------------------------------------------


class TestFindColorspaceTransformRequestType:
    def test_returns_type_when_found(self) -> None:
        req_type = _cst_type()

        with patch(f"{_OCIO_HELPERS}.LibraryRegistry", _mock_lr_with(req_type)):
            result = find_colorspace_transform_request_type()

        assert result is req_type

    def test_returns_none_when_registry_empty(self) -> None:
        with patch(f"{_OCIO_HELPERS}.LibraryRegistry", _mock_lr_empty()):
            result = find_colorspace_transform_request_type()

        assert result is None

    def test_returns_none_on_exception(self) -> None:
        mock_lr = MagicMock()
        mock_lr.list_libraries.side_effect = RuntimeError("engine not running")

        with patch(f"{_OCIO_HELPERS}.LibraryRegistry", mock_lr):
            result = find_colorspace_transform_request_type()

        assert result is None


# ---------------------------------------------------------------------------
# apply_color_management — OCIO path
# ---------------------------------------------------------------------------


class TestApplyColorManagement:
    def test_ocio_used_when_artifact_provided(self) -> None:
        rgb = _rgb()
        expected = _rgb() * 2.0
        req_type = _cst_type()
        mock_gn = MagicMock()
        mock_gn.handle_request.return_value = _make_succeeded_result(expected)

        with (
            patch(f"{_OCIO_HELPERS}.LibraryRegistry", _mock_lr_with(req_type)),
            patch(f"{_OCIO_HELPERS}.GriptapeNodes", mock_gn),
        ):
            pixels, label = apply_color_management(rgb, _color_params(), TONE_FILMIC)

        mock_gn.handle_request.assert_called_once()
        assert pixels is expected
        assert label.startswith("ocio:")

    def test_mode_label_contains_source_display_view(self) -> None:
        rgb = _rgb()
        req_type = _cst_type()
        mock_gn = MagicMock()
        mock_gn.handle_request.return_value = _make_succeeded_result(_rgb())

        with (
            patch(f"{_OCIO_HELPERS}.LibraryRegistry", _mock_lr_with(req_type)),
            patch(f"{_OCIO_HELPERS}.GriptapeNodes", mock_gn),
        ):
            _, label = apply_color_management(rgb, _color_params("ACEScg", "sRGB", "ACES"), TONE_FILMIC)

        assert "ACEScg" in label
        assert "sRGB" in label
        assert "ACES" in label

    def test_raises_on_type_error(self) -> None:
        rgb = _rgb()
        req_type = _cst_type()
        mock_gn = MagicMock()
        mock_gn.handle_request.side_effect = TypeError("No manager found")

        with (
            patch(f"{_OCIO_HELPERS}.LibraryRegistry", _mock_lr_with(req_type)),
            patch(f"{_OCIO_HELPERS}.GriptapeNodes", mock_gn),
            pytest.raises(ValueError, match="No manager found"),
        ):
            apply_color_management(rgb, _color_params(), TONE_FILMIC)

    def test_raises_on_failed_ocio_result(self) -> None:
        rgb = _rgb()
        req_type = _cst_type()
        mock_gn = MagicMock()
        mock_gn.handle_request.return_value = _make_failed_result("unknown colorspace")

        with (
            patch(f"{_OCIO_HELPERS}.LibraryRegistry", _mock_lr_with(req_type)),
            patch(f"{_OCIO_HELPERS}.GriptapeNodes", mock_gn),
            pytest.raises(ValueError, match="unknown colorspace"),
        ):
            apply_color_management(rgb, _color_params(), TONE_FILMIC)

    def test_raises_when_ocio_library_not_loaded(self) -> None:
        rgb = _rgb()
        mock_gn = MagicMock()

        with (
            patch(f"{_OCIO_HELPERS}.LibraryRegistry", _mock_lr_empty()),
            patch(f"{_OCIO_HELPERS}.GriptapeNodes", mock_gn),
            pytest.raises(ValueError, match="not loaded"),
        ):
            apply_color_management(rgb, _color_params(), TONE_FILMIC)

        mock_gn.handle_request.assert_not_called()

    def test_no_fallback_from_ocio_on_library_missing(self) -> None:
        """OCIO failure must propagate — never silently fall back to tone mapping."""
        rgb = _rgb()

        with (
            patch(f"{_OCIO_HELPERS}.LibraryRegistry", _mock_lr_empty()),
            pytest.raises(ValueError),
        ):
            apply_color_management(rgb, _color_params(), TONE_FILMIC)

    def test_raises_with_targeted_message_when_color_params_missing_attributes(self) -> None:
        """AttributeError from duck access must surface as ValueError with a clear message."""
        rgb = _rgb()
        bad_params = object()  # no .source_colorspace, .display, .view attributes

        with pytest.raises(ValueError, match="missing a required attribute"):
            apply_color_management(rgb, bad_params, TONE_FILMIC)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# apply_color_management — basic mode (color_params=None)
# ---------------------------------------------------------------------------


class TestApplyColorManagementBasicMode:
    def test_filmic_tone_mapping_used_when_no_artifact(self) -> None:
        rgb = _rgb()
        mock_gn = MagicMock()

        with patch(f"{_OCIO_HELPERS}.GriptapeNodes", mock_gn):
            _, label = apply_color_management(rgb, None, TONE_FILMIC)

        mock_gn.handle_request.assert_not_called()
        assert label == TONE_FILMIC

    def test_linear_tone_mapping_used_when_no_artifact(self) -> None:
        rgb = _rgb()
        mock_gn = MagicMock()

        with patch(f"{_OCIO_HELPERS}.GriptapeNodes", mock_gn):
            _, label = apply_color_management(rgb, None, TONE_LINEAR)

        mock_gn.handle_request.assert_not_called()
        assert label == TONE_LINEAR

    def test_output_shape_preserved_in_basic_mode(self) -> None:
        rgb = _rgb(8, 8)

        with patch(f"{_OCIO_HELPERS}.GriptapeNodes", MagicMock()):
            pixels, _ = apply_color_management(rgb, None, TONE_FILMIC)

        assert pixels.shape == rgb.shape
