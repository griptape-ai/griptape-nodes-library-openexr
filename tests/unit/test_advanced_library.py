"""Unit tests for OpenEXRLibrary.after_library_nodes_loaded()."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from griptape_nodes.retained_mode.events.artifact_events import (
    RegisterArtifactProviderResultFailure,
    RegisterArtifactProviderResultSuccess,
)

from griptape_nodes_openexr.advanced_library import OpenEXRLibrary


def _make_library() -> OpenEXRLibrary:
    return OpenEXRLibrary()


class TestAfterLibraryNodesLoaded:
    def test_registers_provider_on_success(self) -> None:
        with patch("griptape_nodes.retained_mode.griptape_nodes.GriptapeNodes.handle_request") as mock_handle_request:
            mock_handle_request.side_effect = [
                RegisterArtifactProviderResultSuccess(result_details="ok"),
            ]

            _make_library().after_library_nodes_loaded(MagicMock(), MagicMock())

        assert mock_handle_request.call_count == 1

    def test_raises_when_artifact_provider_registration_fails(self) -> None:
        with patch("griptape_nodes.retained_mode.griptape_nodes.GriptapeNodes.handle_request") as mock_handle_request:
            mock_handle_request.return_value = RegisterArtifactProviderResultFailure(result_details="boom")

            with pytest.raises(RuntimeError, match="Failed to register EXRArtifactProvider"):
                _make_library().after_library_nodes_loaded(MagicMock(), MagicMock())
