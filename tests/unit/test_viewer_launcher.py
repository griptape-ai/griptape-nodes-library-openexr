"""Unit tests for the open_in_viewer() viewer launcher helper."""

from __future__ import annotations

import subprocess
from unittest.mock import MagicMock, patch

from griptape_nodes.retained_mode.events.config_events import GetConfigValueRequest, GetConfigValueResultSuccess
from griptape_nodes.retained_mode.events.os_events import (
    FileIOFailureReason,
    OpenAssociatedFileRequest,
    OpenAssociatedFileResultFailure,
    OpenAssociatedFileResultSuccess,
)

_MODULE = "griptape_nodes_openexr.exr.viewer_launcher"


def _mock_gn(executable: str = "", args: str = "", handle_request_result: object = None) -> MagicMock:
    gn = MagicMock()
    config_values = {
        "openexr.viewer_executable": executable,
        "openexr.viewer_args": args,
    }

    def _handle_request(req):
        if isinstance(req, GetConfigValueRequest):
            return GetConfigValueResultSuccess(value=config_values.get(req.category_and_key, ""), result_details="")
        return handle_request_result

    gn.handle_request.side_effect = _handle_request
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
# Explicit executable path
# ---------------------------------------------------------------------------


class TestExplicitExecutable:
    def test_popen_called_with_executable_and_path(self, tmp_path) -> None:
        exr = str(tmp_path / "test.exr")
        open(exr, "w").close()

        with (
            patch(f"{_MODULE}.GriptapeNodes", _mock_gn(executable="/usr/bin/djv")),
            patch(f"{_MODULE}.subprocess.Popen") as mock_popen,
        ):
            from griptape_nodes_openexr.exr.viewer_launcher import open_in_viewer

            result = open_in_viewer(exr)

        assert result is None
        mock_popen.assert_called_once()
        (cmd,), kwargs = mock_popen.call_args
        assert cmd == ["/usr/bin/djv", exr]
        assert kwargs.get("stdin") == subprocess.DEVNULL
        assert "start_new_session" in kwargs or "creationflags" in kwargs

    def test_args_string_is_split_correctly(self, tmp_path) -> None:
        exr = str(tmp_path / "test.exr")
        open(exr, "w").close()

        with (
            patch(f"{_MODULE}.GriptapeNodes", _mock_gn(executable="/app/viewer", args="--hdr --linear")),
            patch(f"{_MODULE}.subprocess.Popen") as mock_popen,
        ):
            from griptape_nodes_openexr.exr.viewer_launcher import open_in_viewer

            result = open_in_viewer(exr)

        assert result is None
        mock_popen.assert_called_once()
        (cmd,), kwargs = mock_popen.call_args
        assert cmd == ["/app/viewer", "--hdr", "--linear", exr]
        assert kwargs.get("stdin") == subprocess.DEVNULL

    def test_empty_args_string_produces_no_extra_args(self, tmp_path) -> None:
        exr = str(tmp_path / "test.exr")
        open(exr, "w").close()

        with (
            patch(f"{_MODULE}.GriptapeNodes", _mock_gn(executable="/usr/bin/djv", args="")),
            patch(f"{_MODULE}.subprocess.Popen") as mock_popen,
        ):
            from griptape_nodes_openexr.exr.viewer_launcher import open_in_viewer

            result = open_in_viewer(exr)

        assert result is None
        mock_popen.assert_called_once()
        (cmd,), _ = mock_popen.call_args
        assert cmd == ["/usr/bin/djv", exr]

    def test_oserror_returns_error_string(self, tmp_path) -> None:
        exr = str(tmp_path / "test.exr")
        open(exr, "w").close()

        with (
            patch(f"{_MODULE}.GriptapeNodes", _mock_gn(executable="/no/such/binary")),
            patch(f"{_MODULE}.subprocess.Popen", side_effect=FileNotFoundError("not found")),
        ):
            from griptape_nodes_openexr.exr.viewer_launcher import open_in_viewer

            result = open_in_viewer(exr)

        assert result is not None
        assert "not found" in result.lower() or "/no/such/binary" in result

    def test_subprocess_error_returns_error_string(self, tmp_path) -> None:
        exr = str(tmp_path / "test.exr")
        open(exr, "w").close()

        with (
            patch(f"{_MODULE}.GriptapeNodes", _mock_gn(executable="/usr/bin/djv")),
            patch(f"{_MODULE}.subprocess.Popen", side_effect=subprocess.SubprocessError("boom")),
        ):
            from griptape_nodes_openexr.exr.viewer_launcher import open_in_viewer

            result = open_in_viewer(exr)

        assert result is not None
        assert "boom" in result

    def test_malformed_viewer_args_returns_error_string(self, tmp_path) -> None:
        exr = str(tmp_path / "test.exr")

        with patch(f"{_MODULE}.GriptapeNodes", _mock_gn(executable="/usr/bin/djv", args="--flag 'unclosed")):
            from griptape_nodes_openexr.exr.viewer_launcher import open_in_viewer

            result = open_in_viewer(exr)

        assert result is not None
        assert "Invalid viewer args" in result or "closing quotation" in result.lower()


# ---------------------------------------------------------------------------
# OS-default fallback (no executable configured) — delegates to OpenAssociatedFileRequest
# ---------------------------------------------------------------------------


class TestOsFallback:
    def test_os_default_calls_handle_request_with_path(self, tmp_path) -> None:
        exr = str(tmp_path / "test.exr")
        gn_mock = _mock_gn(executable="", handle_request_result=OpenAssociatedFileResultSuccess(result_details="ok"))

        with patch(f"{_MODULE}.GriptapeNodes", gn_mock):
            from griptape_nodes_openexr.exr.viewer_launcher import open_in_viewer

            open_in_viewer(exr)

        os_calls = [
            c for c in gn_mock.handle_request.call_args_list if isinstance(c.args[0], OpenAssociatedFileRequest)
        ]
        assert len(os_calls) == 1
        assert os_calls[0].args[0].path_to_file == exr

    def test_os_default_success_returns_none(self, tmp_path) -> None:
        exr = str(tmp_path / "test.exr")

        with patch(
            f"{_MODULE}.GriptapeNodes",
            _mock_gn(executable="", handle_request_result=OpenAssociatedFileResultSuccess(result_details="ok")),
        ):
            from griptape_nodes_openexr.exr.viewer_launcher import open_in_viewer

            result = open_in_viewer(exr)

        assert result is None

    def test_os_default_failure_returns_error_string(self, tmp_path) -> None:
        exr = str(tmp_path / "test.exr")
        failure = OpenAssociatedFileResultFailure(
            failure_reason=FileIOFailureReason.FILE_NOT_FOUND, result_details="not found"
        )

        with patch(f"{_MODULE}.GriptapeNodes", _mock_gn(executable="", handle_request_result=failure)):
            from griptape_nodes_openexr.exr.viewer_launcher import open_in_viewer

            result = open_in_viewer(exr)

        assert result is not None
        assert "not found" in result

    def test_os_default_io_error_failure_returns_error_string(self, tmp_path) -> None:
        exr = str(tmp_path / "test.exr")
        failure = OpenAssociatedFileResultFailure(
            failure_reason=FileIOFailureReason.IO_ERROR, result_details="io error"
        )

        with patch(f"{_MODULE}.GriptapeNodes", _mock_gn(executable="", handle_request_result=failure)):
            from griptape_nodes_openexr.exr.viewer_launcher import open_in_viewer

            result = open_in_viewer(exr)

        assert result is not None
        assert "io error" in result
