"""Tests for DisplayEXRChannel viewer button — path tracking and callback wiring."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

_NODES_MODULE = "griptape_nodes_openexr.nodes.display_exr_channel"


@pytest.fixture()
def channel_node():
    """Instantiate DisplayEXRChannel with minimal mocks (no engine required)."""
    mock_pfp_instance = MagicMock()
    mock_pfp_instance.build_file.return_value = MagicMock()

    with patch(f"{_NODES_MODULE}.ProjectFileParameter", return_value=mock_pfp_instance):
        from griptape_nodes_openexr.nodes.display_exr_channel import DisplayEXRChannel

        node = DisplayEXRChannel("test_display_exr_channel")
    return node


class TestDisplayEXRChannelViewerButton:
    def test_node_has_open_viewer_param(self, channel_node) -> None:
        param_names = [p.name for p in channel_node.parameters]
        assert "open_in_viewer" in param_names

    def test_current_exr_path_starts_as_none(self, channel_node) -> None:
        assert channel_node._current_exr_path is None

    def test_callback_with_no_path_returns_failure(self, channel_node) -> None:
        mock_details = MagicMock()
        result = channel_node._on_open_viewer_click(MagicMock(), mock_details)
        assert result is not None
        assert result.success is False

    def test_callback_with_path_calls_handle_viewer_button_click(self, channel_node, tmp_path) -> None:
        exr = str(tmp_path / "test.exr")
        channel_node._current_exr_path = exr

        with patch(f"{_NODES_MODULE}.handle_viewer_button_click", return_value=None) as mock_handler:
            result = channel_node._on_open_viewer_click(MagicMock(), MagicMock())

        mock_handler.assert_called_once_with(channel_node.name, exr)
        assert result is None

    def test_callback_reports_viewer_error(self, channel_node, tmp_path) -> None:
        exr = str(tmp_path / "test.exr")
        channel_node._current_exr_path = exr

        mock_result = MagicMock()
        mock_result.success = False
        mock_result.details = "viewer not found"
        with patch(f"{_NODES_MODULE}.handle_viewer_button_click", return_value=mock_result):
            result = channel_node._on_open_viewer_click(MagicMock(), MagicMock())

        assert result is not None
        assert result.success is False
        assert "viewer not found" in result.details
