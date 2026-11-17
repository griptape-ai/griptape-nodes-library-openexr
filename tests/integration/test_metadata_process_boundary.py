"""Phase-one feasibility checks for persistent spawned metadata readers.

The test helper prototypes the child boundary; production nodes are unchanged.
"""

from __future__ import annotations

import asyncio
import multiprocessing
import os
import subprocess
import sys
import time
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np
import OpenEXR
import pytest

from griptape_nodes_openexr.exr.exr_types import EXRData
from tests.integration.metadata_reader_probe import read_headers, read_marked_headers

DATA = Path(__file__).parents[1] / "data"


def test_spawned_reader_reuses_process_without_engine_context(tmp_path: Path) -> None:
    # Given a real EXR with custom array metadata.
    path = tmp_path / "custom.exr"
    pixels = np.zeros((4, 6), dtype=np.float32)
    OpenEXR.File(
        {"owner": "artist", "shot": "010", "weights": np.array([1.0, 2.0, 3.0], dtype=np.float32)},
        {"R": pixels},
    ).write(str(path))

    # When two sequential reads cross the same spawned process boundary.
    with ProcessPoolExecutor(max_workers=1, mp_context=multiprocessing.get_context("spawn")) as pool:
        first_pid, first = pool.submit(read_headers, str(path)).result(timeout=15)
        second_pid, second = pool.submit(read_headers, str(DATA / "multi_part.exr")).result(timeout=15)

    # Then the process persists and returns ordinary metadata objects.
    assert first_pid == second_pid
    assert first_pid != os.getpid()
    assert isinstance(first, EXRData)
    assert isinstance(second, EXRData)
    assert first.parts[0].header.owner == "artist"
    assert first.parts[0].header.custom == {"shot": "010", "weights": [1.0, 2.0, 3.0]}
    assert (first.parts[0].width, first.parts[0].height) == (6, 4)
    assert len(second.parts) == 3
    print(f"host PID={os.getpid()}, reused reader PID={first_pid}, multipart parts={len(second.parts)}")


@pytest.mark.asyncio
@pytest.mark.skipif(sys.platform != "linux", reason="Delayed FIFO opening probe requires Linux /proc")
async def test_blocked_native_header_open_allows_host_heartbeat(tmp_path: Path) -> None:
    # Given a warm spawned reader and a FIFO whose writer waits for the blocked open.
    fifo = tmp_path / "delayed.exr"
    marker = tmp_path / "writer-open"
    os.mkfifo(fifo)
    writer = None
    ticks = 0
    with ProcessPoolExecutor(max_workers=1, mp_context=multiprocessing.get_context("spawn")) as pool:
        reader_pid, _data = await asyncio.wrap_future(pool.submit(read_headers, str(DATA / "single_part_rgba.exr")))
        writer_code = "open(__import__('sys').argv[1], 'wb').close()"
        try:
            # When the child enters a GIL-held native open/read.
            future = asyncio.wrap_future(pool.submit(read_marked_headers, str(fifo), str(marker)))
            deadline = time.monotonic() + 10
            while not marker.exists():
                assert time.monotonic() < deadline, "FIFO reader failed to open"
                await asyncio.sleep(0.005)
            start = time.monotonic()
            max_gap = 0.0
            previous = start
            while time.monotonic() - start < 0.25:
                await asyncio.sleep(0.005)
                now = time.monotonic()
                max_gap = max(max_gap, now - previous)
                previous = now
                ticks += 1

            # Then the host loop progresses while the reader waits for file data.
            assert not future.done()
            assert ticks >= 10
            writer = subprocess.Popen([sys.executable, "-c", writer_code, str(fifo)])
            with pytest.raises(RuntimeError, match="Unable to open"):
                await asyncio.wait_for(future, timeout=10)
            print(f"reader PID={reader_pid}, heartbeat ticks={ticks}, max gap={max_gap:.4f}s")
        finally:
            if writer is None:
                writer = subprocess.Popen([sys.executable, "-c", writer_code, str(fifo)])
            writer.wait(timeout=5)
