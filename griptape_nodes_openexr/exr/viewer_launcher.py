"""Viewer launcher — open an EXR file in an external HDR viewer."""

from __future__ import annotations

import logging
import shlex
import subprocess
import sys
from typing import Any

from griptape_nodes.exe_types.core_types import NodeMessageResult
from griptape_nodes.retained_mode.events.config_events import GetConfigValueRequest, GetConfigValueResultSuccess
from griptape_nodes.retained_mode.events.os_events import OpenAssociatedFileRequest, OpenAssociatedFileResultFailure
from griptape_nodes.retained_mode.griptape_nodes import GriptapeNodes

logger = logging.getLogger("griptape_nodes")


def _get_config_str(key: str) -> str:
    result = GriptapeNodes.handle_request(GetConfigValueRequest(category_and_key=key))
    return str(result.value) if isinstance(result, GetConfigValueResultSuccess) and result.value else ""


def handle_viewer_button_click(node_name: str, exr_path: str | None) -> NodeMessageResult | None:
    if exr_path is None:
        return NodeMessageResult(
            success=False,
            details="No EXR file path available — run the node first",
            response=None,
        )
    error = open_in_viewer(exr_path)
    if error:
        logger.error("'%s': failed to open viewer — %s", node_name, error)
        return NodeMessageResult(success=False, details=error, response=None)
    return None


def open_in_viewer(file_path: str) -> str | None:
    executable = _get_config_str("openexr.viewer_executable")
    viewer_args = _get_config_str("openexr.viewer_args")

    try:
        # TODO: replace with LaunchExternalViewerRequest once added to griptape-nodes engine
        # (griptape-ai/griptape-nodes-engine#4942)
        if executable:
            args_list = shlex.split(viewer_args, posix=(sys.platform != "win32"))
            popen_kwargs: dict[str, Any] = {
                "stdin": subprocess.DEVNULL,
                "stdout": subprocess.DEVNULL,
                "stderr": subprocess.DEVNULL,
            }
            if sys.platform == "win32":
                popen_kwargs["creationflags"] = subprocess.DETACHED_PROCESS
            else:
                popen_kwargs["start_new_session"] = True
            subprocess.Popen([executable, *args_list, file_path], **popen_kwargs)  # noqa: S603
        else:
            # OpenAssociatedFileRequest is dispatched via the engine — if the engine runs
            # remotely the file opens on the engine host, not the local user's machine.
            result = GriptapeNodes.handle_request(OpenAssociatedFileRequest(path_to_file=file_path))
            if isinstance(result, OpenAssociatedFileResultFailure):
                return f"Failed to open file: {result.result_details or result.failure_reason}"
    except FileNotFoundError as exc:
        return f"Viewer not found: {exc}"
    except subprocess.SubprocessError as exc:
        return f"Failed to launch viewer: {exc}"
    except ValueError as exc:
        return f"Invalid viewer args: {exc}"
    except OSError as exc:
        return f"OS error launching viewer: {exc}"

    return None
