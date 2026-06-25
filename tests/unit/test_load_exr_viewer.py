"""Tests for LoadEXR viewer button — callback wiring and path resolution."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

_NODES_MODULE = "griptape_nodes_openexr.nodes.load_exr"


@pytest.fixture()
def load_node():
    """Instantiate LoadEXR with minimal mocks (no engine or file required)."""
    with (
        patch(f"{_NODES_MODULE}.GriptapeNodes") as mock_gn,
        patch(f"{_NODES_MODULE}.os.path.exists", return_value=False),
    ):
        mock_gn.ConfigManager.return_value.get_config_value.return_value = True
        from griptape_nodes_openexr.nodes.load_exr import LoadEXR

        node = LoadEXR("test_load_exr")
    return node


class TestLoadEXRViewerButton:
    def test_node_has_open_viewer_param(self, load_node) -> None:
        param_names = [p.name for p in load_node.parameters]
        assert "open_in_viewer" in param_names

    def test_callback_with_no_path_returns_failure(self, load_node) -> None:
        with patch.object(load_node, "_resolve_file_path_param", return_value=""):
            result = load_node._on_open_viewer_click(MagicMock(), MagicMock())
        assert result is not None
        assert result.success is False

    def test_callback_with_path_calls_handle_viewer_button_click(self, load_node, tmp_path) -> None:
        exr = str(tmp_path / "test.exr")
        with (
            patch.object(load_node, "_resolve_file_path_param", return_value=exr),
            patch(f"{_NODES_MODULE}.handle_viewer_button_click", return_value=None) as mock_handler,
        ):
            result = load_node._on_open_viewer_click(MagicMock(), MagicMock())

        mock_handler.assert_called_once_with(load_node.name, exr)
        assert result is None

    def test_callback_reports_viewer_error(self, load_node, tmp_path) -> None:
        exr = str(tmp_path / "test.exr")
        mock_result = MagicMock()
        mock_result.success = False
        mock_result.details = "viewer not found"
        with (
            patch.object(load_node, "_resolve_file_path_param", return_value=exr),
            patch(f"{_NODES_MODULE}.handle_viewer_button_click", return_value=mock_result),
        ):
            result = load_node._on_open_viewer_click(MagicMock(), MagicMock())

        assert result is not None
        assert result.success is False
        assert "viewer not found" in result.details
