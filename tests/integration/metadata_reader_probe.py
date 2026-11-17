"""Spawn-importable phase-one probe, independent of node and engine state.

This probe exercises the production disk reader with absolute paths while
retaining the spawned-process feasibility checks for future isolation work.
"""

from __future__ import annotations

import os
from pathlib import Path

from griptape_nodes.retained_mode.engine import has_current_engine

from griptape_nodes_openexr.exr.exr_types import EXRData
from griptape_nodes_openexr.exr.metadata_reader import read_metadata


def read_marked_headers(file_path: str, marker: str) -> tuple[int, EXRData]:
    """Signal arrival immediately before the native read for the FIFO probe."""
    Path(marker).touch()
    return read_headers(file_path)


def read_headers(file_path: str) -> tuple[int, EXRData]:
    """Read headers in a spawned interpreter without resolving engine paths."""
    if has_current_engine():
        raise RuntimeError("Reader unexpectedly has an engine context")
    return os.getpid(), read_metadata(file_path)
