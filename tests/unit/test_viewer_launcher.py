"""Unit tests for the open_in_viewer() viewer launcher helper."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from griptape_nodes.retained_mode.events.os_events import (
    FileIOFailureReason,
    LaunchExternalViewerRequest,
    LaunchExternalViewerResultFailure,
    LaunchExternalViewerResultSuccess,
)

_MODULE = "griptape_nodes_openexr.exr.viewer_launcher"


def _mock_gn(handle_request_result: object) -> MagicMock:
    gn = MagicMock()
    gn.handle_request.return_value = handle_request_result
    return gn


# ---------------------------------------------------------------------------
# handle_viewer_button_click
# ---------------------------------------------------------------------------


class TestHandleViewerButtonClick:
    def test_no_path_returns_failure(self) -> None:
        from griptape_nodes_openexr.exr.viewer_launcher import handle_viewer_button_click

        result = handle_viewer_button_click("my_node", None)
        assert result is not None
        assert result.success is False

    def test_with_path_success_returns_none(self, tmp_path) -> None:
        exr = str(tmp_path / "test.exr")

        with patch(f"{_MODULE}.open_in_viewer", return_value=None):
            from griptape_nodes_openexr.exr.viewer_launcher import handle_viewer_button_click

            result = handle_viewer_button_click("my_node", exr)

        assert result is None

    def test_with_path_error_returns_failure(self, tmp_path) -> None:
        exr = str(tmp_path / "test.exr")

        with patch(f"{_MODULE}.open_in_viewer", return_value="viewer not found"):
            from griptape_nodes_openexr.exr.viewer_launcher import handle_viewer_button_click

            result = handle_viewer_button_click("my_node", exr)

        assert result is not None
        assert result.success is False
        assert "viewer not found" in result.details


# ---------------------------------------------------------------------------
# open_in_viewer — delegates to LaunchExternalViewerRequest
# ---------------------------------------------------------------------------


class TestOpenInViewer:
    def test_sends_launch_request_for_openexr_settings_with_fallback(self, tmp_path) -> None:
        exr = str(tmp_path / "test.exr")
        gn_mock = _mock_gn(LaunchExternalViewerResultSuccess(used_fallback=False, result_details="ok"))

        with patch(f"{_MODULE}.GriptapeNodes", gn_mock):
            from griptape_nodes_openexr.exr.viewer_launcher import open_in_viewer

            open_in_viewer(exr)

        gn_mock.handle_request.assert_called_once()
        (request,) = gn_mock.handle_request.call_args.args
        assert isinstance(request, LaunchExternalViewerRequest)
        assert request.path_to_file == exr
        assert request.config_category == "openexr"
        assert request.fallback_to_os_default is True

    def test_success_returns_none(self, tmp_path) -> None:
        exr = str(tmp_path / "test.exr")

        with patch(
            f"{_MODULE}.GriptapeNodes",
            _mock_gn(LaunchExternalViewerResultSuccess(used_fallback=True, result_details="ok")),
        ):
            from griptape_nodes_openexr.exr.viewer_launcher import open_in_viewer

            result = open_in_viewer(exr)

        assert result is None

    def test_failure_returns_result_details(self, tmp_path) -> None:
        exr = str(tmp_path / "test.exr")
        failure = LaunchExternalViewerResultFailure(
            failure_reason=FileIOFailureReason.FILE_NOT_FOUND, result_details="viewer '/no/such/binary' was not found"
        )

        with patch(f"{_MODULE}.GriptapeNodes", _mock_gn(failure)):
            from griptape_nodes_openexr.exr.viewer_launcher import open_in_viewer

            result = open_in_viewer(exr)

        assert result == "viewer '/no/such/binary' was not found"

    def test_failure_without_details_returns_failure_reason(self, tmp_path) -> None:
        exr = str(tmp_path / "test.exr")
        failure = LaunchExternalViewerResultFailure(failure_reason=FileIOFailureReason.IO_ERROR, result_details="")

        with patch(f"{_MODULE}.GriptapeNodes", _mock_gn(failure)):
            from griptape_nodes_openexr.exr.viewer_launcher import open_in_viewer

            result = open_in_viewer(exr)

        assert result is not None
        assert "io_error" in result.lower()
