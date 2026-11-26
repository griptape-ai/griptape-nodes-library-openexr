"""Tests for the Open in Viewer button on the display nodes: path tracking and callback wiring."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from griptape_nodes_openexr.nodes.display_exr_channel import DisplayEXRChannel
from griptape_nodes_openexr.nodes.display_exr_part import DisplayEXRPart

_DISPLAY_NODES = [
    pytest.param(DisplayEXRPart, id="part"),
    pytest.param(DisplayEXRChannel, id="channel"),
]


@pytest.fixture(params=_DISPLAY_NODES)
def display_node(request: pytest.FixtureRequest):
    """Instantiate each display node without the engine."""
    node_type: type = request.param
    return node_type("test_display_node")


class TestViewerButton:
    def test_node_has_open_viewer_param(self, display_node) -> None:
        param_names = [p.name for p in display_node.parameters]
        assert "open_in_viewer" in param_names

    def test_current_exr_path_starts_as_none(self, display_node) -> None:
        assert display_node._current_exr_path is None

    def test_callback_with_no_path_returns_failure(self, display_node) -> None:
        mock_details = MagicMock()
        result = display_node._on_open_viewer_click(MagicMock(), mock_details)
        assert result is not None
        assert result.success is False

    def test_callback_with_path_calls_handle_viewer_button_click(self, display_node, tmp_path) -> None:
        exr = str(tmp_path / "test.exr")
        display_node._current_exr_path = exr

        with patch(
            f"{type(display_node).__module__}.handle_viewer_button_click", autospec=True, return_value=None
        ) as mock_handler:
            result = display_node._on_open_viewer_click(MagicMock(), MagicMock())

        mock_handler.assert_called_once_with(display_node.name, exr)
        assert result is None

    def test_callback_reports_viewer_error(self, display_node, tmp_path) -> None:
        exr = str(tmp_path / "test.exr")
        display_node._current_exr_path = exr

        mock_result = MagicMock()
        mock_result.success = False
        mock_result.details = "viewer not found"
        with patch(
            f"{type(display_node).__module__}.handle_viewer_button_click", autospec=True, return_value=mock_result
        ):
            result = display_node._on_open_viewer_click(MagicMock(), MagicMock())

        assert result is not None
        assert result.success is False
        assert "viewer not found" in result.details


def test_part_on_fail_handler_clears_exr_path(tmp_path) -> None:
    node = DisplayEXRPart("test_display_exr_part")
    node._current_exr_path = str(tmp_path / "test.exr")

    node._on_fail_handler("something went wrong")

    assert node._current_exr_path is None
