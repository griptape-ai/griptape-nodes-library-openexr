"""Shared OCIO helpers — colour management dispatch without hard imports."""

from __future__ import annotations

import logging
from typing import Protocol

import numpy as np
from griptape_nodes.exe_types.node_types import BaseNode
from griptape_nodes.node_library.library_registry import LibraryRegistry
from griptape_nodes.retained_mode.griptape_nodes import GriptapeNodes

from griptape_nodes_openexr.exr.tone_mapping import apply_tone_mapping

logger = logging.getLogger("griptape_nodes")

COLOR_MODE_BASIC = "basic"
COLOR_MODE_OCIO = "ocio"


class ColorParamsProtocol(Protocol):
    source_colorspace: str
    display: str
    view: str


def find_colorspace_transform_request_type() -> type | None:
    """Discover ColorspaceTransformRequest from any loaded library — no hard import."""
    try:
        for lib_name in LibraryRegistry.list_libraries():
            library = LibraryRegistry.get_library(lib_name)
            for req_type in library.get_registered_request_handler_types():  # type: ignore[attr-defined]
                if req_type.__name__ == "ColorspaceTransformRequest":
                    logger.debug("OCIO: found ColorspaceTransformRequest handler in library %r", lib_name)
                    return req_type
        logger.debug("OCIO: ColorspaceTransformRequest handler not found in any loaded library")
    except Exception:
        logger.warning("OCIO: error scanning LibraryRegistry for ColorspaceTransformRequest", exc_info=True)
    return None


def apply_color_management(
    rgb: np.ndarray,
    color_params: ColorParamsProtocol | None,
    tone_mapping: str,
) -> tuple[np.ndarray, str]:
    if color_params is not None:
        try:
            source_colorspace: str = color_params.source_colorspace
            display: str = color_params.display
            view: str = color_params.view
        except AttributeError as e:
            msg = f"color_params is missing a required attribute (source_colorspace, display, view): {e}"
            raise ValueError(msg) from e

        req_type = find_colorspace_transform_request_type()
        if req_type is None:
            msg = (
                f"OCIO transform requested (source={source_colorspace!r}, display={display!r}, view={view!r}) "
                "but the OpenColorIO library is not loaded. Load the library or switch to 'basic' mode."
            )
            raise ValueError(msg)

        try:
            req = req_type(
                pixels=rgb,
                source_colorspace=source_colorspace,
                display=display,
                view=view,
            )
            result = GriptapeNodes.handle_request(req)
        except TypeError as e:
            msg = f"OCIO transform failed — {e}"
            raise ValueError(msg) from e

        if not result.succeeded():
            msg = f"OCIO transform failed — {result.result_details}"
            raise ValueError(msg)

        logger.debug("OCIO: transform succeeded (%s→%s/%s)", source_colorspace, display, view)
        return result.pixels, f"ocio:{source_colorspace}→{display}/{view}"  # type: ignore[attr-defined]

    return apply_tone_mapping(rgb, tone_mapping), tone_mapping


def apply_color_mode_visibility(
    node: BaseNode,
    ocio_active: bool,
    tone_mapping_name: str,
    color_params_name: str,
) -> None:
    """Show/hide tone_mapping and color_params parameters to match the active color mode."""
    if ocio_active:
        node.hide_parameter_by_name(tone_mapping_name)
        node.show_parameter_by_name(color_params_name)
    else:
        node.show_parameter_by_name(tone_mapping_name)
        node.hide_parameter_by_name(color_params_name)
