"""EXR preview generators for the ArtifactManager.

Two generators:
- EXRPreviewGenerator: tone-mapped RGB preview of a layer or composite
- EXRChannelPreviewGenerator: single-channel greyscale (depth, mattes, etc.)

Both use griptape_nodes_openexr.exr.exr_pixel_io for all pixel work,
with no opencolorio dependency.
"""

from __future__ import annotations

from io import BytesIO
from pathlib import Path
from typing import Any

from griptape_nodes.retained_mode.events.os_events import (
    ExistingFilePolicy,
    WriteFileRequest,
    WriteFileResultSuccess,
)
from griptape_nodes.retained_mode.griptape_nodes import GriptapeNodes
from griptape_nodes.retained_mode.managers.artifact_providers.base_artifact_preview_generator import (
    BaseArtifactPreviewGenerator,
)
from griptape_nodes.retained_mode.managers.artifact_providers.base_generator_parameters import (
    BaseGeneratorParameters,
    Field,
)
from pydantic import PositiveInt

from griptape_nodes_openexr.exr.exr_io import scan_exr_header
from griptape_nodes_openexr.exr.exr_pixel_io import (
    apply_exposure,
    apply_gamma,
    load_layer_pixels,
    normalize_pixels,
    to_pil_gray,
    to_pil_rgb,
    tone_map,
)
from griptape_nodes_openexr.exr.exr_types import EXRChannelInfo, EXRPart, parse_channel_name
from griptape_nodes_openexr.exr.strategies.registry import get_strategy


def _default_strategy():
    return get_strategy("nuke")


def _load_exr_part(file_path: str, part_index: int) -> EXRPart:
    exr_data = scan_exr_header(file_path, _default_strategy())
    if part_index < 0 or part_index >= len(exr_data.parts):
        msg = (
            f"Part index {part_index} out of range for '{file_path}' "
            f"({len(exr_data.parts)} part(s))"
        )
        raise ValueError(msg)
    return exr_data.parts[part_index]


def resolve_layer_channels(part: EXRPart, layer_name: str | None, file_path: str) -> list[EXRChannelInfo]:
    """Return channels for the named layer, or the default composite if None."""
    if layer_name is not None:
        for layer in part.layers:
            if layer.name == layer_name:
                return layer.channels
        available = [layer.name or "(default)" for layer in part.layers]
        msg = (
            f"Layer '{layer_name}' not found in part {part.index} of '{file_path}'. "
            f"Available: {', '.join(available)}"
        )
        raise ValueError(msg)

    # Default: top-level RGBA, or first layer
    rgba: list[EXRChannelInfo] = []
    for ch in part.channels:
        parsed = parse_channel_name(ch.name)
        role = parsed.channel_name.upper()
        if parsed.layer_name == "" and role in ("R", "G", "B", "A"):
            rgba.append(ch)
    if rgba:
        return rgba

    if part.layers:
        return part.layers[0].channels

    msg = f"No RGBA or layer channels found in part {part.index} of '{file_path}'"
    raise ValueError(msg)


def _write_preview(directory: str, filename: str, image_bytes: bytes) -> str:
    destination = str(Path(directory) / filename)
    result = GriptapeNodes.handle_request(
        WriteFileRequest(
            file_path=destination,
            content=image_bytes,
            create_parents=True,
            existing_file_policy=ExistingFilePolicy.OVERWRITE,
        )
    )
    if not isinstance(result, WriteFileResultSuccess):
        msg = f"Failed to write EXR preview to '{destination}': {result.result_details}"
        raise OSError(msg)
    return filename


# ---------------------------------------------------------------------------
# RGB tone-mapped preview
# ---------------------------------------------------------------------------


class EXRPreviewParameters(BaseGeneratorParameters):
    """Parameters for EXR RGB tone-mapped preview generation."""

    part_index: int = Field(
        default=0,
        description="Part index (0-based). Default 0 uses the first part.",
        editor_schema_type="integer",
        ge=0,
    )
    layer_name: str = Field(
        default="",
        description="Layer to render. Empty string renders the default composite (top-level RGBA or first layer).",
        editor_schema_type="string",
    )
    tone_mapping: str = Field(
        default="simple",
        description="Tone mapping method: simple, reinhard, or filmic",
        editor_schema_type="string",
    )
    exposure: float = Field(
        default=0.0,
        description="Exposure adjustment in stops before tone mapping",
        editor_schema_type="number",
    )
    max_width: PositiveInt = Field(
        default=1024,
        description="Maximum preview width in pixels (1–8192)",
        editor_schema_type="integer",
        le=8192,
    )
    max_height: PositiveInt = Field(
        default=1024,
        description="Maximum preview height in pixels (1–8192)",
        editor_schema_type="integer",
        le=8192,
    )


class EXRPreviewGenerator(BaseArtifactPreviewGenerator):
    """Tone-mapped RGB preview generator for OpenEXR files.

    Renders a named layer (or the default composite) as a sRGB PNG/WebP/JPEG.
    """

    def __init__(
        self,
        source_file_location: str,
        preview_format: str,
        destination_preview_directory: str,
        destination_preview_file_name: str,
        params: dict[str, Any],
    ) -> None:
        super().__init__(
            source_file_location,
            preview_format,
            destination_preview_directory,
            destination_preview_file_name,
            params,
        )
        self.params = EXRPreviewParameters.model_validate(params)

    @classmethod
    def get_friendly_name(cls) -> str:
        return "EXR Preview Generation"

    @classmethod
    def get_supported_source_formats(cls) -> set[str]:
        return {"exr"}

    @classmethod
    def get_supported_preview_formats(cls) -> set[str]:
        return {"png", "jpg", "webp"}

    @classmethod
    def get_parameters(cls) -> type[BaseGeneratorParameters]:
        return EXRPreviewParameters

    async def attempt_generate_preview(self) -> str:
        part = _load_exr_part(self.source_file_location, self.params.part_index)
        layer = self.params.layer_name or None
        channels = resolve_layer_channels(part, layer, self.source_file_location)
        indices = [ch.channel_index for ch in channels]

        pixels = load_layer_pixels(self.source_file_location, self.params.part_index, indices)

        if self.params.exposure != 0.0:
            pixels = apply_exposure(pixels, self.params.exposure)

        pixels = tone_map(pixels, self.params.tone_mapping)
        img = to_pil_rgb(pixels, self.params.max_width, self.params.max_height)

        buf = BytesIO()
        img.save(buf, format=self.preview_format.upper())
        return _write_preview(
            self.destination_preview_directory,
            self.destination_preview_file_name,
            buf.getvalue(),
        )


# ---------------------------------------------------------------------------
# Single-channel greyscale preview
# ---------------------------------------------------------------------------


class EXRChannelPreviewParameters(BaseGeneratorParameters):
    """Parameters for EXR single-channel greyscale preview generation."""

    part_index: int = Field(
        default=0,
        description="Part index (0-based).",
        editor_schema_type="integer",
        ge=0,
    )
    layer_name: str = Field(
        default="",
        description="Layer containing the channel. Empty string uses the default layer.",
        editor_schema_type="string",
    )
    channel_name: str = Field(
        default="R",
        description="Short channel name to visualize (e.g. R, G, B, A, Z)",
        editor_schema_type="string",
    )
    normalize: bool = Field(
        default=False,
        description="Remap min/max to [0, 1]. Useful for depth and other data passes.",
        editor_schema_type="boolean",
    )
    exposure: float = Field(
        default=0.0,
        description="Exposure adjustment in stops",
        editor_schema_type="number",
    )
    gamma: float = Field(
        default=2.2,
        description="Gamma correction value",
        editor_schema_type="number",
        gt=0.0,
    )
    max_width: PositiveInt = Field(
        default=1024,
        description="Maximum preview width in pixels (1–8192)",
        editor_schema_type="integer",
        le=8192,
    )
    max_height: PositiveInt = Field(
        default=1024,
        description="Maximum preview height in pixels (1–8192)",
        editor_schema_type="integer",
        le=8192,
    )


class EXRChannelPreviewGenerator(BaseArtifactPreviewGenerator):
    """Single-channel greyscale preview generator for OpenEXR files.

    Extracts one channel by its short name (e.g. "R", "Z"), applies optional
    normalization, exposure, and gamma, then writes a greyscale image.
    """

    def __init__(
        self,
        source_file_location: str,
        preview_format: str,
        destination_preview_directory: str,
        destination_preview_file_name: str,
        params: dict[str, Any],
    ) -> None:
        super().__init__(
            source_file_location,
            preview_format,
            destination_preview_directory,
            destination_preview_file_name,
            params,
        )
        self.params = EXRChannelPreviewParameters.model_validate(params)

    @classmethod
    def get_friendly_name(cls) -> str:
        return "EXR Channel Preview Generation"

    @classmethod
    def get_supported_source_formats(cls) -> set[str]:
        return {"exr"}

    @classmethod
    def get_supported_preview_formats(cls) -> set[str]:
        return {"png", "jpg", "webp"}

    @classmethod
    def get_parameters(cls) -> type[BaseGeneratorParameters]:
        return EXRChannelPreviewParameters

    async def attempt_generate_preview(self) -> str:
        part = _load_exr_part(self.source_file_location, self.params.part_index)
        layer = self.params.layer_name or None
        channels = resolve_layer_channels(part, layer, self.source_file_location)
        channel_info = self._find_channel(channels, self.params.channel_name)

        pixels = load_layer_pixels(
            self.source_file_location, self.params.part_index, [channel_info.channel_index]
        )
        # Shape is (H, W, 1) — reduce to (H, W)
        pixels_2d = pixels[:, :, 0]

        if self.params.normalize:
            pixels_2d = normalize_pixels(pixels_2d)

        if self.params.exposure != 0.0:
            pixels_2d = apply_exposure(pixels_2d, self.params.exposure)

        pixels_2d = apply_gamma(pixels_2d, self.params.gamma)
        img = to_pil_gray(pixels_2d, self.params.max_width, self.params.max_height)

        buf = BytesIO()
        img.save(buf, format=self.preview_format.upper())
        return _write_preview(
            self.destination_preview_directory,
            self.destination_preview_file_name,
            buf.getvalue(),
        )

    def _find_channel(self, channels: list[EXRChannelInfo], channel_name: str) -> EXRChannelInfo:
        """Find a channel by its parsed short name (e.g. "R", "Z").

        Raises:
            ValueError: If the channel is not found.
        """
        for ch in channels:
            if parse_channel_name(ch.name).channel_name == channel_name:
                return ch
        available = [parse_channel_name(ch.name).channel_name for ch in channels]
        msg = (
            f"Channel '{channel_name}' not found. "
            f"Available: {', '.join(available)}"
        )
        raise ValueError(msg)
