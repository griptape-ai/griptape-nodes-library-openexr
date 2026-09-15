"""Integration tests for EXRPreviewGenerator.attempt_generate_preview()."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import numpy as np
import pytest
from griptape_nodes.retained_mode.events.os_events import WriteFileResultSuccess
from PIL import Image

from griptape_nodes_openexr.artifact_providers.exr_preview_generator import (
    EXRPreviewGenerator,
)

DATA_DIR = Path(__file__).parent.parent / "data"


def _make_engine(*, return_value: WriteFileResultSuccess | None = None, side_effect=None) -> MagicMock:
    """A fake `Engine` exposing an `ahandle_request` AsyncMock, as consumed by `self.engine.ahandle_request(...)`."""
    engine = MagicMock()
    if side_effect is not None:
        engine.ahandle_request = AsyncMock(side_effect=side_effect)
    else:
        engine.ahandle_request = AsyncMock(
            return_value=return_value
            or WriteFileResultSuccess(result_details="ok", final_file_path="", bytes_written=0)
        )
    return engine


def _make_generator(
    source: Path,
    dest_dir: Path,
    dest_name: str = "preview.webp",
    params: dict | None = None,
    engine: MagicMock | None = None,
) -> EXRPreviewGenerator:
    return EXRPreviewGenerator(
        source_file_location=str(source),
        preview_format="webp",
        destination_preview_directory=str(dest_dir),
        destination_preview_file_name=dest_name,
        params=params or {},
        engine=engine,
    )


def _img_to_bytes(img: Image.Image, fmt: str) -> bytes:
    from io import BytesIO

    buf = BytesIO()
    img.save(buf, format=fmt.upper())
    return buf.getvalue()


class TestConstructorEngineKeyword:
    """Regression: BaseArtifactProvider.attempt_generate_preview() always passes `engine=` as a keyword."""

    def test_accepts_engine_keyword(self, tmp_path: Path) -> None:
        exr_path = DATA_DIR / "single_part_rgba.exr"
        sentinel_engine = object()
        gen = EXRPreviewGenerator(
            source_file_location=str(exr_path),
            preview_format="webp",
            destination_preview_directory=str(tmp_path),
            destination_preview_file_name="preview.webp",
            params={},
            engine=sentinel_engine,  # type: ignore[arg-type]
        )
        assert gen.engine is sentinel_engine


class TestEngineRouting:
    """Regression: writes must go through self.engine, not the global GriptapeNodes singleton."""

    @pytest.mark.asyncio
    async def test_write_routes_through_injected_engine(self, tmp_path: Path) -> None:
        exr_path = DATA_DIR / "single_part_rgba.exr"
        engine = _make_engine()

        with patch(
            "griptape_nodes.retained_mode.griptape_nodes.GriptapeNodes.ahandle_request",
            new_callable=AsyncMock,
        ) as mock_global_handle:
            gen = _make_generator(exr_path, tmp_path, engine=engine)
            await gen.attempt_generate_preview()

        engine.ahandle_request.assert_called_once()
        mock_global_handle.assert_not_called()


class TestAttemptGeneratePreviewRGBA:
    """Tests using single_part_rgba.exr, a simple RGBA EXR."""

    @pytest.mark.asyncio
    async def test_produces_file_at_destination(self, tmp_path: Path) -> None:
        exr_path = DATA_DIR / "single_part_rgba.exr"
        dest_name = "preview.webp"

        write_result = WriteFileResultSuccess(
            result_details="ok", final_file_path=str(tmp_path / dest_name), bytes_written=0
        )
        engine = _make_engine(return_value=write_result)

        gen = _make_generator(exr_path, tmp_path, dest_name, engine=engine)
        result = await gen.attempt_generate_preview()

        assert result == dest_name
        # Verify a WriteFileRequest was made with non-empty bytes
        engine.ahandle_request.assert_called_once()
        write_req = engine.ahandle_request.call_args[0][0]
        assert len(write_req.content) > 0

    @pytest.mark.asyncio
    async def test_output_is_valid_image(self, tmp_path: Path) -> None:
        exr_path = DATA_DIR / "single_part_rgba.exr"
        dest_name = "preview.webp"
        dest_path = tmp_path / dest_name

        captured: list[bytes] = []

        async def capture_write(req):
            captured.append(req.content)
            return WriteFileResultSuccess(
                result_details="ok", final_file_path=str(dest_path), bytes_written=len(req.content)
            )

        engine = _make_engine(side_effect=capture_write)
        gen = _make_generator(exr_path, tmp_path, dest_name, engine=engine)
        await gen.attempt_generate_preview()

        from io import BytesIO

        img = Image.open(BytesIO(captured[0]))
        assert img.width > 0
        assert img.height > 0

    @pytest.mark.asyncio
    async def test_respects_max_dimensions(self, tmp_path: Path) -> None:
        exr_path = DATA_DIR / "single_part_rgba.exr"
        dest_name = "small.webp"

        captured: list[bytes] = []

        async def capture_write(req):
            captured.append(req.content)
            return WriteFileResultSuccess(
                result_details="ok", final_file_path=str(tmp_path / dest_name), bytes_written=len(req.content)
            )

        engine = _make_engine(side_effect=capture_write)
        gen = _make_generator(exr_path, tmp_path, dest_name, params={"max_width": 64, "max_height": 64}, engine=engine)
        await gen.attempt_generate_preview()

        from io import BytesIO

        img = Image.open(BytesIO(captured[0]))
        assert img.width <= 64
        assert img.height <= 64

    @pytest.mark.asyncio
    async def test_filmic_tone_mapping_succeeds(self, tmp_path: Path) -> None:
        exr_path = DATA_DIR / "single_part_rgba.exr"
        engine = _make_engine()

        gen = _make_generator(exr_path, tmp_path, params={"tone_mapping": "filmic"}, engine=engine)
        result = await gen.attempt_generate_preview()

        assert result == "preview.webp"

    @pytest.mark.asyncio
    async def test_linear_tone_mapping_succeeds(self, tmp_path: Path) -> None:
        exr_path = DATA_DIR / "single_part_rgba.exr"
        engine = _make_engine()

        gen = _make_generator(exr_path, tmp_path, params={"tone_mapping": "linear"}, engine=engine)
        result = await gen.attempt_generate_preview()

        assert result == "preview.webp"

    @pytest.mark.asyncio
    async def test_positive_exposure_brightens(self, tmp_path: Path) -> None:
        """With higher exposure, average pixel value should increase."""
        exr_path = DATA_DIR / "single_part_rgba.exr"

        results: dict[str, bytes] = {}

        async def capture_write(req):
            # Key by the destination filename
            results[req.file_path] = req.content
            return WriteFileResultSuccess(
                result_details="ok", final_file_path=req.file_path, bytes_written=len(req.content)
            )

        engine = _make_engine(side_effect=capture_write)

        gen_dark = EXRPreviewGenerator(
            source_file_location=str(exr_path),
            preview_format="webp",
            destination_preview_directory=str(tmp_path),
            destination_preview_file_name="dark.webp",
            params={"exposure": -3.0, "tone_mapping": "linear"},
            engine=engine,
        )
        await gen_dark.attempt_generate_preview()

        gen_bright = EXRPreviewGenerator(
            source_file_location=str(exr_path),
            preview_format="webp",
            destination_preview_directory=str(tmp_path),
            destination_preview_file_name="bright.webp",
            params={"exposure": 3.0, "tone_mapping": "linear"},
            engine=engine,
        )
        await gen_bright.attempt_generate_preview()

        from io import BytesIO

        dark_key = str(tmp_path / "dark.webp")
        bright_key = str(tmp_path / "bright.webp")
        dark_mean = np.array(Image.open(BytesIO(results[dark_key])).convert("RGB")).mean()
        bright_mean = np.array(Image.open(BytesIO(results[bright_key])).convert("RGB")).mean()
        assert bright_mean > dark_mean


class TestAttemptGeneratePreviewAOVs:
    """Tests using single_part_aovs.exr, a multi-channel AOV file."""

    @pytest.mark.asyncio
    async def test_aov_file_succeeds(self, tmp_path: Path) -> None:
        exr_path = DATA_DIR / "single_part_aovs.exr"
        engine = _make_engine()

        gen = _make_generator(exr_path, tmp_path, engine=engine)
        result = await gen.attempt_generate_preview()

        assert result == "preview.webp"


class TestAttemptGeneratePreviewPNG:
    """Tests with PNG output format."""

    @pytest.mark.asyncio
    async def test_png_output_format(self, tmp_path: Path) -> None:
        exr_path = DATA_DIR / "single_part_rgba.exr"

        captured: list[bytes] = []

        async def capture_write(req):
            captured.append(req.content)
            return WriteFileResultSuccess(result_details="ok", final_file_path="", bytes_written=0)

        engine = _make_engine(side_effect=capture_write)

        gen = EXRPreviewGenerator(
            source_file_location=str(exr_path),
            preview_format="png",
            destination_preview_directory=str(tmp_path),
            destination_preview_file_name="preview.png",
            params={},
            engine=engine,
        )
        await gen.attempt_generate_preview()

        from io import BytesIO

        img = Image.open(BytesIO(captured[0]))
        assert img.format == "PNG"
