"""Viewer launcher — open an EXR file in an external HDR viewer."""

from __future__ import annotations

import logging

from griptape_nodes.exe_types.core_types import NodeMessageResult
from griptape_nodes.retained_mode.events.os_events import (
    LaunchExternalViewerRequest,
    LaunchExternalViewerResultFailure,
)
from griptape_nodes.retained_mode.griptape_nodes import GriptapeNodes

logger = logging.getLogger("griptape_nodes")


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
    # Launches `openexr.viewer_executable` with `openexr.viewer_args`, or the OS default
    # application when no viewer is configured.
    result = GriptapeNodes.handle_request(
        LaunchExternalViewerRequest(path_to_file=file_path, config_category="openexr", fallback_to_os_default=True)
    )
    if isinstance(result, LaunchExternalViewerResultFailure):
        return str(result.result_details) or f"Failed to open viewer: {result.failure_reason}"
    return None
