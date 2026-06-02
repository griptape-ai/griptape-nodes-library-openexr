"""SaveEXR node — save EXR files from image or channel data."""

from __future__ import annotations

import io
import json
import logging
import tempfile
from typing import Any

import numpy as np
import OpenEXR
from griptape.artifacts import ImageArtifact, ImageUrlArtifact
from griptape_nodes.exe_types.core_types import Parameter, ParameterGroup, ParameterMode
from griptape_nodes.exe_types.node_types import SuccessFailureNode
from griptape_nodes.exe_types.param_components.project_file_parameter import ProjectFileParameter
from griptape_nodes.exe_types.param_types.parameter_float import ParameterFloat
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
    _ATTR_CAP_DATE,
    _ATTR_COMMENTS,
    _ATTR_NAME,
    _ATTR_OWNER,
    _ATTR_PIXEL_ASPECT_RATIO,
    _ATTR_SOFTWARE,
    _ATTR_TIME_CODE,
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


class SaveEXR(SuccessFailureNode):
    """Save a single-part EXR file from an image or EXR channel artifacts.

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

        with ParameterGroup(name="Metadata", ui_options={"collapsed": True}) as metadata_group:
            self._metadata_part_name_param = ParameterString(
                name="metadata_part_name",
                display_name="Part Name",
                default_value="",
                tooltip="Name for this EXR part (useful for multi-part workflows).",
                allowed_modes={ParameterMode.INPUT, ParameterMode.PROPERTY},
            )

            self._metadata_pixel_aspect_ratio_param = ParameterFloat(
                name="metadata_pixel_aspect_ratio",
                display_name="Pixel Aspect Ratio",
                default_value=1.0,
                tooltip="Pixel width/height ratio (1.0 = square pixels).",
                allowed_modes={ParameterMode.INPUT, ParameterMode.PROPERTY},
            )

            self._metadata_owner_param = ParameterString(
                name="metadata_owner",
                display_name="Owner",
                default_value="",
                tooltip="Asset owner, empty if absent.",
                allowed_modes={ParameterMode.INPUT, ParameterMode.PROPERTY},
            )

            self._metadata_comments_param = ParameterString(
                name="metadata_comments",
                display_name="Comments",
                default_value="",
                tooltip="Free-text comments embedded in the EXR header.",
                allowed_modes={ParameterMode.INPUT, ParameterMode.PROPERTY},
            )

            self._metadata_capture_date_param = ParameterString(
                name="metadata_capture_date",
                display_name="Capture Date",
                default_value="",
                tooltip="Capture date (e.g. 2025-01-01T12:00:00), empty if absent.",
                allowed_modes={ParameterMode.INPUT, ParameterMode.PROPERTY},
            )

            self._metadata_software_param = ParameterString(
                name="metadata_software",
                display_name="Software",
                default_value="",
                tooltip="Authoring application name, empty if absent.",
                allowed_modes={ParameterMode.INPUT, ParameterMode.PROPERTY},
            )

            self._metadata_time_code_param = ParameterString(
                name="metadata_time_code",
                display_name="Time Code",
                default_value="",
                tooltip="Editorial timecode (HH:MM:SS:FF), empty if absent.",
                allowed_modes={ParameterMode.INPUT, ParameterMode.PROPERTY},
            )

            self._metadata_custom_param = Parameter(
                name="metadata_custom",
                display_name="Custom Attributes",
                input_types=["json", "str", "dict"],
                type="json",
                default_value={},
                tooltip="Non-standard header attributes as a JSON object.",
                allowed_modes={ParameterMode.INPUT, ParameterMode.PROPERTY},
            )

        self.add_node_element(metadata_group)

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

    def _read_metadata(self) -> dict:
        def _opt(val: str | None) -> str | None:
            return (val or "").strip() or None

        custom_val = self.get_parameter_value(self._metadata_custom_param.name) or {}
        if isinstance(custom_val, str):
            try:
                custom_val = json.loads(custom_val)
            except (ValueError, TypeError):
                logger.warning("SaveEXR: metadata_custom is not valid JSON — using {}")
                custom_val = {}
        custom = custom_val if isinstance(custom_val, dict) else {}

        return {
            "name": self.get_parameter_value(self._metadata_part_name_param.name) or "",
            "pixel_aspect_ratio": float(self.get_parameter_value(self._metadata_pixel_aspect_ratio_param.name) or 1.0),
            "owner": _opt(self.get_parameter_value(self._metadata_owner_param.name)),
            "comments": _opt(self.get_parameter_value(self._metadata_comments_param.name)),
            "capture_date": _opt(self.get_parameter_value(self._metadata_capture_date_param.name)),
            "software": _opt(self.get_parameter_value(self._metadata_software_param.name)),
            "time_code": _opt(self.get_parameter_value(self._metadata_time_code_param.name)),
            "custom": custom,
        }

    def _on_fail_handler(self, details: str) -> None:
        self.parameter_output_values[self._output_part_param.name] = None
        self._set_status_results(was_successful=False, result_details=details)

    async def aprocess(self) -> None:
        self._clear_execution_status()

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
            self._on_fail_handler("No input connected: connect image_in or at least one channel slot")
            return

        if isinstance(result, str):
            self._on_fail_handler(result)
            return

        channels, width, height, mode = result

        meta = self._read_metadata()
        extra_header = _build_extra_header(meta)

        try:
            exr_bytes, channel_infos = _write_to_bytes(
                channels, oxr_compression, pixel_type_str, pixel_type_enum, extra_header
            )
        except Exception as e:
            self._on_fail_handler(f"Failed to write EXR: {e}")
            return

        try:
            dest = self._output_file.build_file()
            dest_path = dest.resolve()
        except Exception as e:
            self._on_fail_handler(f"Failed to resolve output path: {e}")
            return

        write_result = GriptapeNodes.handle_request(WriteFileRequest(file_path=dest_path, content=exr_bytes))
        if not isinstance(write_result, WriteFileResultSuccess):
            self._on_fail_handler("Failed to save output file")
            return

        window = WindowCoordinates(xmin=0, ymin=0, xmax=width - 1, ymax=height - 1)
        header = EXRHeader(
            compression=compression_enum,
            line_order=LineOrderType.INCREASING_Y,
            data_window=window,
            display_window=window,
            pixel_aspect_ratio=meta["pixel_aspect_ratio"],
            screen_window_center=(0.0, 0.0),
            screen_window_width=1.0,
            storage_type=StorageType.SCANLINE_IMAGE,
            name=meta["name"],
            chunk_count=None,
            tile_description=None,
            # TODO: expose chromaticities (8 floats: red/green/blue/white x,y) for HDR/wide-gamut workflows
            chromaticities=None,
            time_code=meta["time_code"],
            owner=meta["owner"],
            comments=meta["comments"],
            capture_date=meta["capture_date"],
            software=meta["software"],
            custom=meta["custom"],
        )
        output_part = EXRPartArtifact(
            file_path=write_result.final_file_path,
            part_index=0,
            name=meta["name"],
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
        logger.info("SaveEXR '%s': %s", self.name, details)

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


def _build_extra_header(meta: dict) -> dict:
    """Build the optional header attributes dict from _read_metadata() output."""
    attrs: dict = {}
    if meta["name"]:
        attrs[_ATTR_NAME] = meta["name"]
    if meta["pixel_aspect_ratio"] != 1.0:
        attrs[_ATTR_PIXEL_ASPECT_RATIO] = meta["pixel_aspect_ratio"]
    if meta["owner"]:
        attrs[_ATTR_OWNER] = meta["owner"]
    if meta["comments"]:
        attrs[_ATTR_COMMENTS] = meta["comments"]
    if meta["capture_date"]:
        attrs[_ATTR_CAP_DATE] = meta["capture_date"]
    if meta["software"]:
        attrs[_ATTR_SOFTWARE] = meta["software"]
    if meta["time_code"]:
        attrs[_ATTR_TIME_CODE] = meta["time_code"]
    if meta["custom"]:
        attrs.update(meta["custom"])
    return attrs


def _write_to_bytes(
    channels: dict[str, np.ndarray],
    compression: OpenEXR.Compression,
    pixel_type: str,
    pixel_type_enum: PixelType,
    extra_header: dict | None = None,
) -> tuple[bytes, list[EXRChannelInfo]]:
    """Write channels to a temp file, read back as bytes, return (bytes, channel_infos)."""
    # OpenEXR's Python binding only accepts a filesystem path — no in-memory write API.
    # TODO: use a Project Situation for temp file placement so the directory is
    # configurable per-project rather than defaulting to the OS temp dir.
    # Tracked: https://github.com/griptape-ai/griptape-nodes-library-openexr/issues/17
    with tempfile.NamedTemporaryFile(suffix=".exr", delete=False) as tmp:
        tmp_path = tmp.name

    try:
        write_exr_channels(
            tmp_path, channels, compression=compression, pixel_type=pixel_type, extra_header=extra_header
        )
        # Read it back but skip any attempt to generate thumbnail for now until we add ArtfiactManager support.
        read_result = GriptapeNodes.handle_request(
            ReadFileRequest(file_path=tmp_path, should_transform_image_content_to_thumbnail=False, workspace_only=False)
        )
        if not isinstance(read_result, ReadFileResultSuccess):
            raise RuntimeError(f"Failed to read temp EXR: {tmp_path}")
        raw = read_result.content
        if not isinstance(raw, bytes):
            raise RuntimeError(f"Expected bytes from temp EXR, got {type(raw)}")
        exr_bytes = raw
    finally:
        delete_result = GriptapeNodes.handle_request(DeleteFileRequest(path=tmp_path, workspace_only=False))
        if isinstance(delete_result, DeleteFileResultFailure):
            logger.warning("SaveEXR: failed to clean up temp file '%s': %s", tmp_path, delete_result.failure_reason)

    channel_infos = [
        EXRChannelInfo(name=name, pixel_type=pixel_type_enum, x_sampling=1, y_sampling=1) for name in channels
    ]
    return exr_bytes, channel_infos
