"""Dispatch colour management requests to loaded OCIO libraries without importing them."""

from __future__ import annotations

import logging
from typing import Any, Literal, Protocol, overload

import numpy as np
from griptape_nodes.exe_types.core_types import Parameter, ParameterMode
from griptape_nodes.exe_types.node_types import BaseNode
from griptape_nodes.exe_types.param_types.parameter_bool import ParameterBool
from griptape_nodes.exe_types.param_types.parameter_string import ParameterString
from griptape_nodes.node_library.library_registry import LibraryRegistry
from griptape_nodes.retained_mode.griptape_nodes import GriptapeNodes
from griptape_nodes.traits.options import Options

from griptape_nodes_openexr.exr.colour.tone_mapping import (
    TONE_CHOICES,
    TONE_CURVES_DESCRIPTION,
    TONE_DEFAULT,
    apply_tone_mapping,
    canonical_tone_mapping,
    validate_tone_mapping,
)

logger = logging.getLogger("griptape_nodes")

COLOR_MODE_BASIC = "basic"
COLOR_MODE_OCIO = "ocio"

TONE_MAPPING_TOOLTIP = f"Display transform when color_mode is 'basic'. {TONE_CURVES_DESCRIPTION}"

EMBED_PROFILE_TOOLTIP = (
    "Embed an ICC profile describing the selected display transform. Off: the image is "
    "untagged and the viewer uses its default colour interpretation. If that default is sRGB, "
    "'Linear sRGB to rec709' and 'Linear sRGB to rec1886' look different from 'Linear sRGB to sRGB'. "
    "On: the file is labelled for "
    "other applications, and transforms sharing the same source primaries then look "
    "the same in a colour-managed viewer."
)


class ColorParamsProtocol(Protocol):
    source_colorspace: str
    display: str
    view: str
    config_path: str | None


_cached_request_type: type | None = None


def reset_colorspace_transform_request_type_cache() -> None:
    """Clear the cached ColorspaceTransformRequest type.

    Call when a library is unloaded or replaced at runtime. Newly loaded
    libraries are discovered automatically because misses are never cached.
    """
    global _cached_request_type  # noqa: PLW0603
    _cached_request_type = None


def find_colorspace_transform_request_type() -> type | None:
    """Find ColorspaceTransformRequest in the loaded libraries without importing them.

    Cache a discovered handler to avoid scanning the registry for each sequence
    frame. Misses are not cached, so libraries loaded later can still be found.
    """
    global _cached_request_type  # noqa: PLW0603
    if _cached_request_type is not None:
        return _cached_request_type

    try:
        for lib_name in LibraryRegistry.list_libraries():
            library = LibraryRegistry.get_library(lib_name)
            for req_type in library.get_registered_request_handler_types():  # type: ignore[attr-defined]
                if req_type.__name__ == "ColorspaceTransformRequest":
                    logger.debug("OCIO: found ColorspaceTransformRequest handler in library %r", lib_name)
                    _cached_request_type = req_type
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

    canonical = validate_tone_mapping(tone_mapping)
    return apply_tone_mapping(rgb, canonical), canonical


def _convert_tone_mapping_param(value: str) -> str:
    """Default an empty parameter value, otherwise resolve aliases and casing."""
    return canonical_tone_mapping(value) if value else TONE_DEFAULT


def read_tone_mapping(node: BaseNode, tone_mapping_param: ParameterString) -> str:
    """Read and canonicalise a node's `tone_mapping` parameter, defaulting when empty."""
    raw = node.get_parameter_value(tone_mapping_param.name)
    return _convert_tone_mapping_param(str(raw) if raw is not None else "")


def read_color_settings(
    node: BaseNode,
    color_mode_param: ParameterString,
    tone_mapping_param: ParameterString,
    color_params_param: Parameter,
) -> tuple[str, str, ColorParamsProtocol | None]:
    """Read execution colour settings and validate basic mode before source I/O.

    OCIO ignores the hidden tone mapping value and requires a connected artifact.
    Call inside the node's failure-handling block.
    """
    color_mode = str(node.get_parameter_value(color_mode_param.name) or COLOR_MODE_BASIC)
    tone_mapping = read_tone_mapping(node, tone_mapping_param)
    color_params: ColorParamsProtocol | None = None
    if color_mode == COLOR_MODE_OCIO:
        color_params = node.get_parameter_value(color_params_param.name)  # type: ignore[assignment]
        if color_params is None:
            raise ValueError("OCIO mode requires a connected color_params artifact.")
    else:
        tone_mapping = validate_tone_mapping(tone_mapping)
    return color_mode, tone_mapping, color_params


@overload
def add_color_mode_parameters(
    node: BaseNode,
    *,
    color_mode_tooltip: str = ...,
    tone_mapping_tooltip: str = ...,
    color_params_tooltip: str = ...,
    embed_profile_tooltip: str = ...,
    include_embed_profile: Literal[True] = ...,
) -> tuple[ParameterString, ParameterString, Parameter, ParameterBool]: ...


@overload
def add_color_mode_parameters(
    node: BaseNode,
    *,
    color_mode_tooltip: str = ...,
    tone_mapping_tooltip: str = ...,
    color_params_tooltip: str = ...,
    embed_profile_tooltip: str = ...,
    include_embed_profile: Literal[False],
) -> tuple[ParameterString, ParameterString, Parameter, None]: ...


def add_color_mode_parameters(
    node: BaseNode,
    *,
    color_mode_tooltip: str = "'basic' uses local tone mapping; 'ocio' uses the connected OCIOColorParamsArtifact.",
    tone_mapping_tooltip: str = TONE_MAPPING_TOOLTIP,
    color_params_tooltip: str = "OCIO color parameters (source colorspace, display, view). Required when color_mode is 'ocio'.",
    embed_profile_tooltip: str = EMBED_PROFILE_TOOLTIP,
    include_embed_profile: bool = True,
) -> tuple[ParameterString, ParameterString, Parameter, ParameterBool | None]:
    """Create and register the colour management parameters.

    Registers color_mode, tone_mapping and color_params. Also registers
    embed_color_profile unless `include_embed_profile` is False. Video nodes
    pass False because YUV players use CICP stream and container tags instead
    of an RGB ICC profile.

    The mode defaults to 'basic'. Initial visibility follows the saved mode in
    node.metadata, then any color_mode restored from a saved workflow.
    Returns the parameter objects for the caller to store, with None in place
    of embed_color_profile when it is omitted.
    """
    tone_mapping_name = "tone_mapping"
    color_params_name = "color_params"
    embed_profile_name = "embed_color_profile" if include_embed_profile else None

    def apply_visibility_for_value(value: Any) -> Any:
        # Converters run during initial_setup when after_value_set is skipped.
        # Workflows saved while 'ocio' was the default can have color_mode without
        # "_color_mode" metadata. Use the restored value to set their visibility.
        apply_color_mode_visibility(
            node, value == COLOR_MODE_OCIO, tone_mapping_name, color_params_name, embed_profile_name
        )
        return value

    color_mode_param = ParameterString(
        name="color_mode",
        default_value=COLOR_MODE_BASIC,
        tooltip=color_mode_tooltip,
        allowed_modes={ParameterMode.INPUT, ParameterMode.PROPERTY},
        converters=[apply_visibility_for_value],
    )
    color_mode_param.add_trait(Options(choices=[COLOR_MODE_BASIC, COLOR_MODE_OCIO]))
    node.add_parameter(color_mode_param)

    tone_mapping_param = ParameterString(
        name=tone_mapping_name,
        default_value=TONE_DEFAULT,
        tooltip=tone_mapping_tooltip,
        allowed_modes={ParameterMode.INPUT, ParameterMode.PROPERTY},
        # Shows saved aliases and mixed-case inputs under their canonical choice name.
        converters=[_convert_tone_mapping_param],
    )
    # allow_custom keeps the dropdown and disables the Options converter, which
    # rewrites off-list values to choices[0]. Retired names survive loading so
    # apply_tone_mapping can report them during execution. Validation is deferred:
    # with initial_setup=True, a validator error only reaches the log and leaves
    # the parameter at its default. The workflow would silently render as sRGB.
    tone_mapping_param.add_trait(Options(choices=TONE_CHOICES, allow_custom=True))
    node.add_parameter(tone_mapping_param)

    color_params_param = Parameter(
        name=color_params_name,
        input_types=["OCIOColorParamsArtifact"],
        type="OCIOColorParamsArtifact",
        tooltip=color_params_tooltip,
        allowed_modes={ParameterMode.INPUT},
        ui_options={"hide": True},
    )
    node.add_parameter(color_params_param)

    embed_profile_param = None
    if embed_profile_name is not None:
        embed_profile_param = ParameterBool(
            name=embed_profile_name,
            default_value=False,
            tooltip=embed_profile_tooltip,
            allowed_modes={ParameterMode.INPUT, ParameterMode.PROPERTY},
        )
        node.add_parameter(embed_profile_param)

    # after_value_set is not called by the framework when restoring saved values,
    # so read the persisted mode from metadata to apply the correct initial visibility.
    restored_mode = node.metadata.get("_color_mode", COLOR_MODE_BASIC)
    apply_color_mode_visibility(
        node, restored_mode == COLOR_MODE_OCIO, tone_mapping_name, color_params_name, embed_profile_name
    )

    return color_mode_param, tone_mapping_param, color_params_param, embed_profile_param


def handle_color_mode_change(
    node: BaseNode,
    value: Any,
    tone_mapping_param_name: str,
    color_params_param_name: str,
    embed_profile_param_name: str | None,
) -> None:
    """Persist the color mode to metadata and update parameter visibility."""
    node.metadata["_color_mode"] = value
    apply_color_mode_visibility(
        node,
        value == COLOR_MODE_OCIO,
        tone_mapping_param_name,
        color_params_param_name,
        embed_profile_param_name,
    )


def apply_color_mode_visibility(
    node: BaseNode,
    ocio_active: bool,
    tone_mapping_name: str,
    color_params_name: str,
    embed_profile_name: str | None,
) -> None:
    """Set basic-mode and OCIO parameter visibility for the active colour mode.

    embed_color_profile follows tone_mapping because it describes a local
    display transform. OCIO output is already display-referred. Video nodes
    pass None because they have no profile to embed.
    """
    if ocio_active:
        node.hide_parameter_by_name(tone_mapping_name)
        node.show_parameter_by_name(color_params_name)
        if embed_profile_name is not None:
            node.hide_parameter_by_name(embed_profile_name)
    else:
        node.show_parameter_by_name(tone_mapping_name)
        node.hide_parameter_by_name(color_params_name)
        if embed_profile_name is not None:
            node.show_parameter_by_name(embed_profile_name)
