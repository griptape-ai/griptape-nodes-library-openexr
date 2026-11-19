"""Recover metadata work whose owning loop has stopped."""

import asyncio
from unittest.mock import patch

import pytest

from tests.unit.test_read_exr import _make_node, _single_file
from tests.unit.test_read_exr import harness as harness


@pytest.mark.parametrize("pending_kind", ["task", "future"])
@pytest.mark.parametrize("fails", [False, True])
def test_stopped_loop_read_is_replaced_and_retired(harness, tmp_path, pending_kind, fails):
    # Given a read suspended on a host loop that then stops.
    node = _make_node()
    node.set_parameter_value("path", str(_single_file(tmp_path)))
    loop = asyncio.new_event_loop()
    release = asyncio.Event()
    started = asyncio.Event()
    original = node._read_current_selection
    reads = 0

    async def read():
        nonlocal reads
        reads += 1
        if reads == 1:
            started.set()
            await release.wait()
            if fails:
                raise RuntimeError("retired read failed")
        return await original()

    with patch.object(node, "_read_current_selection", side_effect=read):
        coroutine = node._refresh_metadata(node._metadata_generation, preview=True)
        pending = (
            loop.create_task(coroutine) if pending_kind == "task" else asyncio.run_coroutine_threadsafe(coroutine, loop)
        )
        node._pending_metadata = pending
        node._pending_loop = loop
        try:
            loop.run_until_complete(started.wait())

            # When a caller on another loop explicitly waits for metadata.
            async def recover():
                await asyncio.wait_for(node.wait_for_pending_scan(), 1)

            asyncio.run(recover())
            # Then a fresh read publishes metadata and retains preview intent.
            assert node.parameter_output_values["compression"] == "ZIP_COMPRESSION"
            assert len(harness.preview_requests) == 1
            outputs = dict(node.parameter_output_values)
            scan = node._scan
        finally:
            release.set()
            loop.run_until_complete(asyncio.gather(*asyncio.all_tasks(loop)))
            loop.run_until_complete(loop.shutdown_default_executor())
            loop.close()
    # Restarting the retired loop cannot clear the replacement's state.
    assert node.parameter_output_values == outputs
    assert node._scan is scan
    assert reads == 2
