"""Tests for DisplayEXRPart viewer button — path tracking and callback wiring."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

_NODES_MODULE = "griptape_nodes_openexr.nodes.display_exr_part"


@pytest.fixture()
def part_node():
    """Instantiate DisplayEXRPart with minimal mocks (no engine required)."""
    mock_pfp_instance = MagicMock()
    mock_pfp_instance.build_file.return_value = MagicMock()

    with patch(f"{_NODES_MODULE}.ProjectFileParameter", return_value=mock_pfp_instance):
        from griptape_nodes_openexr.nodes.display_exr_part import DisplayEXRPart

        node = DisplayEXRPart("test_display_exr_part")
    return node


class TestDisplayEXRPartViewerButton:
    def test_node_has_open_viewer_param(self, part_node) -> None:
        param_names = [p.name for p in part_node.parameters]
        assert "open_in_viewer" in param_names

    def test_current_exr_path_starts_as_none(self, part_node) -> None:
        assert part_node._current_exr_path is None

    def test_callback_with_no_path_returns_failure(self, part_node) -> None:
        mock_details = MagicMock()
        result = part_node._on_open_viewer_click(MagicMock(), mock_details)
        assert result is not None
        assert result.success is False

    def test_callback_with_path_calls_handle_viewer_button_click(self, part_node, tmp_path) -> None:
        exr = str(tmp_path / "test.exr")
        part_node._current_exr_path = exr

        with patch(f"{_NODES_MODULE}.handle_viewer_button_click", return_value=None) as mock_handler:
            result = part_node._on_open_viewer_click(MagicMock(), MagicMock())

        mock_handler.assert_called_once_with(part_node.name, exr)
        assert result is None

    def test_callback_reports_viewer_error(self, part_node, tmp_path) -> None:
        exr = str(tmp_path / "test.exr")
        part_node._current_exr_path = exr

        mock_result = MagicMock()
        mock_result.success = False
        mock_result.details = "viewer not found"
        with patch(f"{_NODES_MODULE}.handle_viewer_button_click", return_value=mock_result):
            result = part_node._on_open_viewer_click(MagicMock(), MagicMock())

        assert result is not None
        assert result.success is False
        assert "viewer not found" in result.details

    def test_on_fail_handler_clears_exr_path(self, part_node, tmp_path) -> None:
        part_node._current_exr_path = str(tmp_path / "test.exr")
        part_node._on_fail_handler("something went wrong")
        assert part_node._current_exr_path is None
