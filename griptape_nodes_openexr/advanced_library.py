"""Advanced library entry point: registers the EXR artifact provider on load."""

from __future__ import annotations

from typing import TYPE_CHECKING

from griptape_nodes.node_library.advanced_node_library import AdvancedNodeLibrary

from griptape_nodes_openexr.exr.metadata_service import shutdown_metadata_service

if TYPE_CHECKING:
    from griptape_nodes.node_library.library_registry import Library, LibrarySchema


class OpenEXRLibrary(AdvancedNodeLibrary):
    """Registers EXR artifact provider and preview generator with the engine."""

    def before_library_unregistered(self, library_data: LibrarySchema, library: Library) -> None:

        shutdown_metadata_service()

    def after_library_nodes_loaded(self, library_data: LibrarySchema, library: Library) -> None:
        from griptape_nodes.retained_mode.events.artifact_events import (
            RegisterArtifactProviderRequest,
        )
        from griptape_nodes.retained_mode.griptape_nodes import GriptapeNodes

        from griptape_nodes_openexr.artifact_providers.exr_artifact_provider import EXRArtifactProvider

        result = GriptapeNodes.handle_request(RegisterArtifactProviderRequest(provider_class=EXRArtifactProvider))
        if result.failed():
            msg = f"Failed to register EXRArtifactProvider: {result.result_details}"
            raise RuntimeError(msg)
