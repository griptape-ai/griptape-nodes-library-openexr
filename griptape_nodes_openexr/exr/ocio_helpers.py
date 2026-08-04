"""Shared OCIO helpers — colour management dispatch without hard imports."""

from __future__ import annotations

import logging
from typing import Any, Protocol

import numpy as np
from griptape_nodes.exe_types.core_types import Parameter, ParameterMode
from griptape_nodes.exe_types.node_types import BaseNode
from griptape_nodes.exe_types.param_types.parameter_string import ParameterString
from griptape_nodes.node_library.library_registry import LibraryRegistry
from griptape_nodes.retained_mode.griptape_nodes import GriptapeNodes
from griptape_nodes.traits.options import Options

from griptape_nodes_openexr.exr.tone_mapping import TONE_FILMIC, TONE_LINEAR, apply_tone_mapping

logger = logging.getLogger("griptape_nodes")

COLOR_MODE_BASIC = "basic"
COLOR_MODE_OCIO = "ocio"


class ColorParamsProtocol(Protocol):
    source_colorspace: str
    display: str
    view: str
    config_path: str | None


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
            config_path: str | None = color_params.config_path
        except AttributeError as e:
            msg = f"color_params is missing a required attribute (source_colorspace, display, view, config_path): {e}"
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
                config_path=config_path,
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


def add_color_mode_parameters(
    node: BaseNode,
    *,
    color_mode_tooltip: str = "'basic' uses local tone mapping; 'ocio' uses the connected OCIOColorParamsArtifact.",
    tone_mapping_tooltip: str = "Local tone mapping when color_mode is 'basic'.",
    color_params_tooltip: str = "OCIO color parameters (source colorspace, display, view). Required when color_mode is 'ocio'.",
) -> tuple[ParameterString, ParameterString, Parameter]:
    """Create and register the color_mode, tone_mapping, and color_params parameters.

    Sets default mode based on OCIO availability, restores the saved mode from
    node.metadata, and applies initial visibility. Returns the three parameter
    objects so the caller can store them as instance attributes.
    """
    default_mode = COLOR_MODE_OCIO if find_colorspace_transform_request_type() is not None else COLOR_MODE_BASIC

    color_mode_param = ParameterString(
        name="color_mode",
        default_value=default_mode,
        tooltip=color_mode_tooltip,
        allowed_modes={ParameterMode.INPUT, ParameterMode.PROPERTY},
    )
    color_mode_param.add_trait(Options(choices=[COLOR_MODE_BASIC, COLOR_MODE_OCIO]))
    node.add_parameter(color_mode_param)

    ocio_active = default_mode == COLOR_MODE_OCIO

    tone_mapping_param = ParameterString(
        name="tone_mapping",
        default_value=TONE_FILMIC,
        tooltip=tone_mapping_tooltip,
        allowed_modes={ParameterMode.INPUT, ParameterMode.PROPERTY},
        ui_options={"hide": ocio_active},
    )
    tone_mapping_param.add_trait(Options(choices=[TONE_FILMIC, TONE_LINEAR]))
    node.add_parameter(tone_mapping_param)

    color_params_param = Parameter(
        name="color_params",
        input_types=["OCIOColorParamsArtifact"],
        type="OCIOColorParamsArtifact",
        tooltip=color_params_tooltip,
        allowed_modes={ParameterMode.INPUT},
        ui_options={"hide": not ocio_active},
    )
    node.add_parameter(color_params_param)

    # after_value_set is not called by the framework when restoring saved values,
    # so read the persisted mode from metadata to apply the correct initial visibility.
    restored_mode = node.metadata.get("_color_mode", default_mode)
    apply_color_mode_visibility(
        node, restored_mode == COLOR_MODE_OCIO, tone_mapping_param.name, color_params_param.name
    )

    return color_mode_param, tone_mapping_param, color_params_param


def handle_color_mode_change(
    node: BaseNode,
    value: Any,
    tone_mapping_param_name: str,
    color_params_param_name: str,
) -> None:
    """Persist the color mode to metadata and update parameter visibility."""
    node.metadata["_color_mode"] = value
    apply_color_mode_visibility(node, value == COLOR_MODE_OCIO, tone_mapping_param_name, color_params_param_name)


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
