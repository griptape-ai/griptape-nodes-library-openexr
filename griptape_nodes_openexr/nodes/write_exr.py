"""WriteEXR node — write EXR files from image or channel data."""

from __future__ import annotations

import io
import logging
import tempfile
from typing import Any

import numpy as np
import OpenEXR
from griptape.artifacts import ImageArtifact, ImageUrlArtifact
from griptape_nodes.exe_types.core_types import Parameter, ParameterMode
from griptape_nodes.exe_types.node_types import SuccessFailureNode
from griptape_nodes.exe_types.param_components.project_file_parameter import ProjectFileParameter
from griptape_nodes.exe_types.param_types.parameter_string import ParameterString
from griptape_nodes.files.file import File
from griptape_nodes.retained_mode.events.os_events import (
    DeleteFileRequest,
    DeleteFileResultFailure,
    ReadFileRequest,
    ReadFileResultSuccess,
    WriteFileRequest,
    WriteFileResultSuccess,
)
from griptape_nodes.retained_mode.griptape_nodes import GriptapeNodes
from griptape_nodes.traits.options import Options
from PIL import Image

from griptape_nodes_openexr.exr.exr_header_artifact import EXRChannelArtifact, EXRPartArtifact
from griptape_nodes_openexr.exr.exr_io import load_exr_channels, write_exr_channels
from griptape_nodes_openexr.exr.exr_types import (
    CompressionType,
    EXRChannelInfo,
    EXRHeader,
    LineOrderType,
    PixelType,
    StorageType,
    WindowCoordinates,
)

logger = logging.getLogger("griptape_nodes")

_COMPRESSION_OPTIONS = ["ZIP", "ZIPS", "PIZ", "DWAA", "NONE"]
_PIXEL_TYPE_OPTIONS = ["HALF", "FLOAT"]

_COMPRESSION_TO_OXR: dict[str, OpenEXR.Compression] = {
    "ZIP": OpenEXR.ZIP_COMPRESSION,
    "ZIPS": OpenEXR.ZIPS_COMPRESSION,
    "PIZ": OpenEXR.PIZ_COMPRESSION,
    "DWAA": OpenEXR.DWAA_COMPRESSION,
    "NONE": OpenEXR.NO_COMPRESSION,
}

_COMPRESSION_TO_ENUM: dict[str, CompressionType] = {
    "ZIP": CompressionType.ZIP_COMPRESSION,
    "ZIPS": CompressionType.ZIPS_COMPRESSION,
    "PIZ": CompressionType.PIZ_COMPRESSION,
    "DWAA": CompressionType.DWAA_COMPRESSION,
    "NONE": CompressionType.NO_COMPRESSION,
}

_PIXEL_TYPE_TO_STR: dict[str, str] = {
    "HALF": "half",
    "FLOAT": "float",
}

_PIXEL_TYPE_TO_ENUM: dict[str, PixelType] = {
    "HALF": PixelType.HALF,
    "FLOAT": PixelType.FLOAT,
}


class WriteEXR(SuccessFailureNode):
    """Write a single-part EXR file from an image or EXR channel artifacts.

    Mode A (image input): accepts an ImageArtifact (8-bit PNG/JPEG), normalises
    pixel values from [0, 255] to [0.0, 1.0], and writes R, G, B channels.

    Mode B (channel input): accepts up to four EXRChannelArtifact slots (R, G, B, A),
    loads their pixel arrays, and writes them preserving float precision.

    Mode B takes priority when any channel slot is connected.
    """

    def __init__(self, name: str, metadata: dict[Any, Any] | None = None) -> None:
        super().__init__(name, metadata)

        self._image_in_param = Parameter(
            name="image_in",
            input_types=["ImageArtifact", "ImageUrlArtifact"],
            type="ImageArtifact",
            tooltip="8-bit image to convert to EXR (Mode A). Ignored when any channel slot is connected.",
            allowed_modes={ParameterMode.INPUT},
        )
        self.add_parameter(self._image_in_param)

        self._channel_r_param = Parameter(
            name="channel_r",
            input_types=["EXRChannelArtifact"],
            type="EXRChannelArtifact",
            tooltip="EXR channel to write as the R channel (Mode B). Optional.",
            allowed_modes={ParameterMode.INPUT},
        )
        self.add_parameter(self._channel_r_param)

        self._channel_g_param = Parameter(
            name="channel_g",
            input_types=["EXRChannelArtifact"],
            type="EXRChannelArtifact",
            tooltip="EXR channel to write as the G channel (Mode B). Optional.",
            allowed_modes={ParameterMode.INPUT},
        )
        self.add_parameter(self._channel_g_param)

        self._channel_b_param = Parameter(
            name="channel_b",
            input_types=["EXRChannelArtifact"],
            type="EXRChannelArtifact",
            tooltip="EXR channel to write as the B channel (Mode B). Optional.",
            allowed_modes={ParameterMode.INPUT},
        )
        self.add_parameter(self._channel_b_param)

        self._channel_a_param = Parameter(
            name="channel_a",
            input_types=["EXRChannelArtifact"],
            type="EXRChannelArtifact",
            tooltip="EXR channel to write as the A channel (Mode B). Optional.",
            allowed_modes={ParameterMode.INPUT},
        )
        self.add_parameter(self._channel_a_param)

        self._compression_param = ParameterString(
            name="compression",
            default_value="ZIP",
            tooltip="EXR compression codec applied to the output file.",
            allowed_modes={ParameterMode.INPUT, ParameterMode.PROPERTY},
        )
        self._compression_param.add_trait(Options(choices=_COMPRESSION_OPTIONS))
        self.add_parameter(self._compression_param)

        self._pixel_type_param = ParameterString(
            name="pixel_type",
            default_value="HALF",
            tooltip="Pixel storage type. HALF (16-bit float) is standard for most EXR workflows; FLOAT (32-bit) preserves full precision.",
            allowed_modes={ParameterMode.INPUT, ParameterMode.PROPERTY},
        )
        self._pixel_type_param.add_trait(Options(choices=_PIXEL_TYPE_OPTIONS))
        self.add_parameter(self._pixel_type_param)

        self._output_file = ProjectFileParameter(
            node=self,
            name="output_file",
            default_filename="output.exr",
        )
        self._output_file.add_parameter()

        self._output_part_param = Parameter(
            name="output_part",
            type="EXRPartArtifact",
            output_type="EXRPartArtifact",
            tooltip="Descriptor for the written EXR file's single part (engine path, channel metadata).",
            allowed_modes={ParameterMode.OUTPUT},
        )
        self.add_parameter(self._output_part_param)

        self._create_status_parameters(
            result_details_tooltip="Details about the EXR write result",
            result_details_placeholder="Write details will appear here.",
            parameter_group_initially_collapsed=True,
        )

    async def aprocess(self) -> None:
        self._clear_execution_status()
        self.parameter_output_values[self._output_part_param.name] = None

        image_in: ImageArtifact | ImageUrlArtifact | None = self.get_parameter_value(self._image_in_param.name)
        channel_r: EXRChannelArtifact | None = self.get_parameter_value(self._channel_r_param.name)
        channel_g: EXRChannelArtifact | None = self.get_parameter_value(self._channel_g_param.name)
        channel_b: EXRChannelArtifact | None = self.get_parameter_value(self._channel_b_param.name)
        channel_a: EXRChannelArtifact | None = self.get_parameter_value(self._channel_a_param.name)

        compression_key = str(self.get_parameter_value(self._compression_param.name) or "ZIP")
        pixel_type_key = str(self.get_parameter_value(self._pixel_type_param.name) or "HALF")

        oxr_compression = _COMPRESSION_TO_OXR.get(compression_key, OpenEXR.ZIP_COMPRESSION)
        compression_enum = _COMPRESSION_TO_ENUM.get(compression_key, CompressionType.ZIP_COMPRESSION)
        pixel_type_str = _PIXEL_TYPE_TO_STR.get(pixel_type_key, "half")
        pixel_type_enum = _PIXEL_TYPE_TO_ENUM.get(pixel_type_key, PixelType.HALF)

        channel_slots = {
            slot: artifact
            for slot, artifact in {"R": channel_r, "G": channel_g, "B": channel_b, "A": channel_a}.items()
            if artifact is not None
        }

        if channel_slots:
            result = self._gather_mode_b_channels(channel_slots)
        elif image_in is not None:
            result = self._gather_mode_a_channels(image_in)
        else:
            self._set_status_results(
                was_successful=False, result_details="No input connected: connect image_in or at least one channel slot"
            )
            return

        if isinstance(result, str):
            self._set_status_results(was_successful=False, result_details=result)
            return

        channels, width, height, mode = result

        try:
            exr_bytes, channel_infos = _write_to_bytes(channels, oxr_compression, pixel_type_str, pixel_type_enum)
        except Exception as e:
            self._set_status_results(was_successful=False, result_details=f"Failed to write EXR: {e}")
            return

        try:
            dest = self._output_file.build_file()
            dest_path = dest.resolve()
        except Exception as e:
            self._set_status_results(was_successful=False, result_details=f"Failed to resolve output path: {e}")
            return

        write_result = GriptapeNodes.handle_request(WriteFileRequest(file_path=dest_path, content=exr_bytes))
        if not isinstance(write_result, WriteFileResultSuccess):
            self._set_status_results(was_successful=False, result_details="Failed to save output file")
            return

        window = WindowCoordinates(xmin=0, ymin=0, xmax=width - 1, ymax=height - 1)
        header = EXRHeader(
            compression=compression_enum,
            line_order=LineOrderType.INCREASING_Y,
            data_window=window,
            display_window=window,
            pixel_aspect_ratio=1.0,
            screen_window_center=(0.0, 0.0),
            screen_window_width=1.0,
            storage_type=StorageType.SCANLINE_IMAGE,
            name="",
            chunk_count=None,
            tile_description=None,
            chromaticities=None,
            time_code=None,
            owner=None,
            comments=None,
            capture_date=None,
            software=None,
            custom={},
        )
        output_part = EXRPartArtifact(
            file_path=write_result.final_file_path,
            part_index=0,
            name="",
            width=width,
            height=height,
            header=header,
            channels=channel_infos,
        )

        self.parameter_output_values[self._output_part_param.name] = output_part
        self.publish_update_to_parameter(self._output_part_param.name, output_part)

        channel_names = [ch.name for ch in channel_infos]
        details = f"Wrote {width}×{height} EXR ({mode}), channels: {channel_names}, {compression_key}, {pixel_type_key}"
        self._set_status_results(was_successful=True, result_details=details)
        logger.info("WriteEXR '%s': %s", self.name, details)

    @staticmethod
    def _gather_mode_a_channels(
        image_in: ImageArtifact | ImageUrlArtifact,
    ) -> tuple[dict[str, np.ndarray], int, int, str] | str:
        """Decode an ImageArtifact or ImageUrlArtifact and return normalised float32 R, G, B channels."""
        try:
            if isinstance(image_in, ImageUrlArtifact):
                image_bytes = File(image_in.value).read_bytes()
            else:
                image_bytes = image_in.value
            img = Image.open(io.BytesIO(image_bytes)).convert("RGB")
        except Exception as e:
            return f"Failed to decode image: {e}"
        arr = np.array(img, dtype=np.float32) / 255.0
        channels = {
            "R": arr[..., 0],
            "G": arr[..., 1],
            "B": arr[..., 2],
        }
        return channels, img.width, img.height, "image"

    @staticmethod
    def _gather_mode_b_channels(
        channel_slots: dict[str, EXRChannelArtifact],
    ) -> tuple[dict[str, np.ndarray], int, int, str] | str:
        """Load pixel arrays from EXRChannelArtifact slots, validate shapes."""
        channels: dict[str, np.ndarray] = {}
        for slot, artifact in channel_slots.items():
            try:
                loaded = load_exr_channels(artifact.file_path, artifact.part_index, [artifact.channel.name])
            except (ValueError, RuntimeError) as e:
                return f"Failed to load {slot} channel: {e}"
            channels[slot] = loaded[artifact.channel.name]

        shapes = {slot: arr.shape for slot, arr in channels.items()}
        unique_shapes = set(shapes.values())
        if len(unique_shapes) > 1:
            return f"Channel dimensions do not match: {shapes}"

        shape = next(iter(unique_shapes))
        height, width = shape
        return channels, width, height, "channels"


def _write_to_bytes(
    channels: dict[str, np.ndarray],
    compression: OpenEXR.Compression,
    pixel_type: str,
    pixel_type_enum: PixelType,
) -> tuple[bytes, list[EXRChannelInfo]]:
    """Write channels to a temp file, read back as bytes, return (bytes, channel_infos)."""
    # OpenEXR's Python binding only accepts a filesystem path — no in-memory write API.
    with tempfile.NamedTemporaryFile(suffix=".exr", delete=False) as tmp:
        tmp_path = tmp.name

    try:
        write_exr_channels(tmp_path, channels, compression=compression, pixel_type=pixel_type)
        read_result = GriptapeNodes.handle_request(ReadFileRequest(file_path=tmp_path, workspace_only=False))
        if not isinstance(read_result, ReadFileResultSuccess):
            raise RuntimeError(f"Failed to read temp EXR: {tmp_path}")
        raw = read_result.content
        if not isinstance(raw, bytes):
            raise RuntimeError(f"Expected bytes from temp EXR, got {type(raw)}")
        exr_bytes = raw
    finally:
        delete_result = GriptapeNodes.handle_request(DeleteFileRequest(path=tmp_path, workspace_only=False))
        if isinstance(delete_result, DeleteFileResultFailure):
            logger.warning("WriteEXR: failed to clean up temp file '%s': %s", tmp_path, delete_result.failure_reason)

    channel_infos = [
        EXRChannelInfo(name=name, pixel_type=pixel_type_enum, x_sampling=1, y_sampling=1) for name in channels
    ]
    return exr_bytes, channel_infos
