"""EXR preview generator: converts HDR EXR files to PNG/WebP previews."""

from __future__ import annotations

from io import BytesIO
from pathlib import Path
from typing import TYPE_CHECKING, Any

import numpy as np
from griptape_nodes.retained_mode.events.os_events import (
    ExistingFilePolicy,
    WriteFileRequest,
    WriteFileResultSuccess,
)
from griptape_nodes.retained_mode.managers.artifact_providers.base_artifact_preview_generator import (
    BaseArtifactPreviewGenerator,
)
from griptape_nodes.retained_mode.managers.artifact_providers.base_generator_parameters import (
    BaseGeneratorParameters,
    Field,
)
from PIL import Image
from pydantic import PositiveInt

from griptape_nodes_openexr.exr.channel_selection import select_alpha_channel, select_display_channels
from griptape_nodes_openexr.exr.exr_io import build_rgb_array, load_exr_channels, scan_exr_header
from griptape_nodes_openexr.exr.tone_mapping import apply_exposure, apply_tone_mapping, to_uint8_srgb

if TYPE_CHECKING:
    from griptape_nodes.retained_mode.engine import Engine


class EXRPreviewParameters(BaseGeneratorParameters):
    """Parameters for EXR preview generation."""

    tone_mapping: str = Field(
        default="filmic",
        description="Tone mapping curve: 'filmic' or 'linear' (clamp only)",
        editor_schema_type="string",
    )

    exposure: float = Field(
        default=0.0,
        description="Exposure adjustment in EV stops (-10 to +10). Positive = brighter.",
        editor_schema_type="number",
        ge=-10.0,
        le=10.0,
    )

    max_width: PositiveInt = Field(
        default=1024,
        description="Maximum preview width in pixels (1-8192)",
        editor_schema_type="integer",
        le=8192,
    )

    max_height: PositiveInt = Field(
        default=1024,
        description="Maximum preview height in pixels (1-8192)",
        editor_schema_type="integer",
        le=8192,
    )


class EXRPreviewGenerator(BaseArtifactPreviewGenerator):
    """Converts EXR files to PNG/WebP previews via tone mapping.

    Reuses the exr/ helpers for channel selection, pixel loading, exposure,
    and tone mapping shared with the display nodes.
    """

    def __init__(  # noqa: PLR0913
        self,
        source_file_location: str,
        preview_format: str,
        destination_preview_directory: str,
        destination_preview_file_name: str,
        params: dict[str, Any],
        *,
        engine: Engine | None = None,
    ) -> None:
        super().__init__(
            source_file_location,
            preview_format,
            destination_preview_directory,
            destination_preview_file_name,
            params,
            engine=engine,
        )
        self.params = EXRPreviewParameters.model_validate(params)

    @classmethod
    def get_friendly_name(cls) -> str:
        return "EXR Preview"

    @classmethod
    def get_supported_source_formats(cls) -> set[str]:
        return {"exr"}

    @classmethod
    def get_supported_preview_formats(cls) -> set[str]:
        return {"png", "webp"}

    @classmethod
    def get_parameters(cls) -> type[BaseGeneratorParameters]:
        return EXRPreviewParameters

    async def attempt_generate_preview(self) -> str:
        """Convert EXR to a display-ready PNG/WebP preview.

        Raises:
            RuntimeError: If the EXR file cannot be opened or has no channels.
            OSError: If the preview file cannot be written.
        """
        # header_only avoids loading pixel data just to enumerate channels.
        exr_data = scan_exr_header(self.source_file_location, header_only=True)
        # The preview generator API just asks for preview so assume the first part
        # in the EXR.
        part = exr_data.parts[0]
        channel_names = [ch.name for ch in part.channels]

        rgb_names = select_display_channels(channel_names)
        if not rgb_names:
            msg = f"No displayable channels found in '{self.source_file_location}'"
            raise RuntimeError(msg)
        alpha_name = select_alpha_channel(channel_names, rgb_names)

        channels_to_load = rgb_names + ([alpha_name] if alpha_name else [])
        pixel_data = load_exr_channels(self.source_file_location, part_index=0, channel_names=channels_to_load)

        rgb = build_rgb_array(pixel_data, rgb_names)

        rgb = apply_exposure(rgb, self.params.exposure)
        rgb = apply_tone_mapping(rgb, self.params.tone_mapping)
        rgb_uint8 = to_uint8_srgb(rgb)

        if alpha_name:
            alpha_float = np.clip(pixel_data[alpha_name], 0.0, 1.0)
            alpha_uint8 = (alpha_float * 255.0 + 0.5).astype(np.uint8)
            rgba_uint8 = np.dstack([rgb_uint8, alpha_uint8])
            img = Image.fromarray(rgba_uint8, mode="RGBA")
        else:
            img = Image.fromarray(rgb_uint8, mode="RGB")

        img.thumbnail((self.params.max_width, self.params.max_height), Image.Resampling.LANCZOS)

        output_buffer = BytesIO()
        img.save(output_buffer, format=self.preview_format.upper())
        output_bytes = output_buffer.getvalue()

        destination_path = str(Path(self.destination_preview_directory) / self.destination_preview_file_name)
        write_request = WriteFileRequest(
            file_path=destination_path,
            content=output_bytes,
            create_parents=True,
            existing_file_policy=ExistingFilePolicy.OVERWRITE,
        )
        write_result = await self.engine.ahandle_request(write_request)

        if not isinstance(write_result, WriteFileResultSuccess):
            msg = f"Failed to write EXR preview: {write_result.result_details}"
            raise OSError(msg)

        return self.destination_preview_file_name
