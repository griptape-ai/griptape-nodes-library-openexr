"""Shared canvas-size guard for channel rendering and sequence previews."""

from griptape_nodes.retained_mode.events.config_events import GetConfigValueRequest, GetConfigValueResultSuccess
from griptape_nodes.retained_mode.griptape_nodes import GriptapeNodes

from griptape_nodes_openexr.exr.exr_types import WindowCoordinates

MAX_RENDER_PIXELS_DEFAULT = 67108864
MAX_RENDER_PIXELS_SETTING = "openexr.max_render_pixels"


class RenderLimitError(ValueError):
    """A render canvas exceeds the configured pixel limit."""


def validate_render_limit(value: object) -> int:
    """Require a positive integer rather than coercing a malformed setting."""
    if type(value) is not int or value <= 0:
        msg = f"{MAX_RENDER_PIXELS_SETTING} must be a positive integer, got {value!r}."
        raise ValueError(msg)
    return value


def get_render_limit() -> int:
    """Read the library setting on every execution, falling back when absent."""
    result = GriptapeNodes.handle_request(GetConfigValueRequest(category_and_key=MAX_RENDER_PIXELS_SETTING))
    if result.failed():
        return MAX_RENDER_PIXELS_DEFAULT
    if not isinstance(result, GetConfigValueResultSuccess):
        msg = f"Unexpected response reading {MAX_RENDER_PIXELS_SETTING}"
        raise RuntimeError(msg)
    return validate_render_limit(result.value)


def check_canvas(window: WindowCoordinates, limit: int) -> None:
    """Check geometry before allocating an assembly or output canvas."""
    if window.width <= 0 or window.height <= 0:
        msg = f"Invalid render window: {window}"
        raise ValueError(msg)
    pixels = window.width * window.height
    if pixels > limit:
        msg = (
            f"Render canvas {window.width}x{window.height} ({pixels} pixels) exceeds "
            f"{MAX_RENDER_PIXELS_SETTING} ({limit}). Raise this library setting to render this canvas."
        )
        raise RenderLimitError(msg)
