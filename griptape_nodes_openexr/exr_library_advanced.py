"""Advanced library entry point for the OpenEXR library.

Registers EXRArtifactProvider with the ArtifactManager after nodes are loaded,
enabling automatic preview generation for .exr files in the file browser.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from griptape_nodes.node_library.advanced_node_library import AdvancedNodeLibrary
from griptape_nodes.retained_mode.events.artifact_events import (
    RegisterArtifactProviderRequest,
    RegisterArtifactProviderResultFailure,
)
from griptape_nodes.retained_mode.griptape_nodes import GriptapeNodes

if TYPE_CHECKING:
    from griptape_nodes.node_library.library_registry import Library, LibrarySchema

logger = logging.getLogger("griptape_nodes")


class OpenEXRLibraryAdvanced(AdvancedNodeLibrary):
    def after_library_nodes_loaded(self, library_data: LibrarySchema, library: Library) -> None:
        logger.info("OpenEXR library: after_library_nodes_loaded called — registering EXRArtifactProvider")

        from griptape_nodes_openexr.artifact_providers.exr_artifact_provider import EXRArtifactProvider

        result = GriptapeNodes.handle_request(
            RegisterArtifactProviderRequest(provider_class=EXRArtifactProvider)
        )
        if isinstance(result, RegisterArtifactProviderResultFailure):
            logger.warning(
                "OpenEXR library: Failed to register EXRArtifactProvider: %s",
                result.result_details,
            )
        else:
            logger.info(
                "OpenEXR library: EXRArtifactProvider registered — supported formats: %s",
                EXRArtifactProvider.get_supported_formats(),
            )
