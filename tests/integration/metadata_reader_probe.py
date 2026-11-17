"""Spawn-importable phase-one probe, independent of node and engine state.

This prototype exercises existing header conversion helpers with absolute paths.
The production disk-reader extraction belongs to the next implementation phase.
"""

from __future__ import annotations

import os
from pathlib import Path

import OpenEXR
from griptape_nodes.retained_mode.engine import has_current_engine

from griptape_nodes_openexr.exr.exr_io import _build_channel_list_from_header, _build_header
from griptape_nodes_openexr.exr.exr_types import EXRData, EXRPart


def read_marked_headers(file_path: str, marker: str) -> tuple[int, EXRData]:
    """Signal arrival immediately before the native read for the FIFO probe."""
    Path(marker).touch()
    return read_headers(file_path)


def read_headers(file_path: str) -> tuple[int, EXRData]:
    """Read headers in a spawned interpreter without resolving engine paths."""
    if has_current_engine():
        raise RuntimeError("Reader unexpectedly has an engine context")
    if not Path(file_path).is_absolute():
        raise ValueError("Reader requires an absolute path")
    parts = []
    with OpenEXR.File(file_path, separate_channels=True, header_only=True) as file:
        for part in file.parts:
            raw_header = part.header
            header = _build_header(part, raw_header)
            parts.append(
                EXRPart(
                    name=part.name(),
                    channels=_build_channel_list_from_header(raw_header.get("channels", []), None),
                    header=header,
                    width=header.data_window.width,
                    height=header.data_window.height,
                )
            )
    return os.getpid(), EXRData(parts=parts)
