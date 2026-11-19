"""Completion ownership for Read EXR metadata and execution."""

import asyncio
import logging
import threading
from unittest.mock import patch

import pytest

from tests.unit.test_read_exr import _NODE_MODULE, _RENDER_MODULE, _make_exr_data, _make_node, _make_part, _single_file
from tests.unit.test_read_exr import harness as harness


@pytest.mark.parametrize("operation", ["edit", "header", "render"])
@pytest.mark.asyncio
async def test_failed_completion_after_unload_leaves_node_unchanged(harness, tmp_path, operation):
    # Given a successful node and a blocked operation that will fail.
    node = _make_node()
    node.set_parameter_value("path", str(_single_file(tmp_path)), initial_setup=True)
    await node.aprocess()
    started, release = threading.Event(), threading.Event()

    def fail(*args, **kwargs):
        started.set()
        assert release.wait(5)
        raise RuntimeError("late failure")

    target = f"{_RENDER_MODULE}.load_exr_channels" if operation == "render" else f"{_NODE_MODULE}.read_metadata"
    with patch(target, side_effect=fail):
        task = None
        try:
            if operation == "edit":
                node.set_parameter_value("frame", 1)
                task = node._pending_metadata
                assert isinstance(task, asyncio.Task)
            else:
                node._render = None
                task = asyncio.create_task(node.aprocess())
            assert await asyncio.to_thread(started.wait, 5)
            # When the library is unloaded before the failure completes.
            from griptape_nodes_openexr.exr.metadata_reader import invalidate_metadata_reads

            invalidate_metadata_reads()
            outputs = dict(node.parameter_output_values)
            scan, render = node._scan, node._render
        finally:
            release.set()
            if task is not None:
                assert isinstance(task, asyncio.Task)
                await task
    # Then neither outputs nor cached state are changed by the completion.
    assert node.parameter_output_values == outputs
    assert node._scan is scan
    assert node._render is render


@pytest.mark.parametrize("fails", [False, True])
@pytest.mark.asyncio
async def test_selection_change_during_execution_header_read_reports_failure(harness, tmp_path, fails):
    # Given an execution reading the old source.
    node = _make_node()
    first = _single_file(tmp_path)
    second = tmp_path / "new.exr"
    second.write_bytes(b"")
    node.set_parameter_value("path", str(first), initial_setup=True)
    started, release = threading.Event(), threading.Event()

    def read(path):
        if path == str(first):
            started.set()
            assert release.wait(5)
            if fails:
                raise RuntimeError("old header failed")
        return harness.scan_exr_header(path)

    with patch(f"{_NODE_MODULE}.read_metadata", side_effect=read):
        execution = asyncio.create_task(node.aprocess())
        try:
            assert await asyncio.to_thread(started.wait, 5)
            # When an edit refreshes the new source before execution finishes.
            node.set_parameter_value("path", str(second))
            await node.wait_for_pending_scan()
        finally:
            release.set()
            await execution
    # Then failure routing is explicit and the new metadata survives.
    assert node.parameter_output_values["was_successful"] is False
    details = "old header failed" if fails else "Selection changed during execution"
    assert details in node.parameter_output_values["result_details"]
    assert node.get_next_control_output() is node.failure_output
    assert node._scan is not None
    assert node._scan.key == str(second)
    assert node.parameter_output_values["compression"] == "ZIP_COMPRESSION"
    assert harness.written == []


@pytest.mark.asyncio
async def test_current_edit_read_failure_logs_traceback(harness, tmp_path, caplog):
    node = _make_node()
    with patch(f"{_NODE_MODULE}.read_metadata", side_effect=AttributeError("reader defect")):
        with caplog.at_level(logging.WARNING, logger="griptape_nodes"):
            node.set_parameter_value("path", str(_single_file(tmp_path)))
            await node.wait_for_pending_scan()
    assert "reader defect" in caplog.text
    assert any(record.exc_info is not None for record in caplog.records)


@pytest.mark.parametrize("operation", ["header", "preview"])
@pytest.mark.asyncio
async def test_cancelled_edit_waits_for_running_worker(harness, tmp_path, operation):
    # Given an edit whose worker has started.
    node = _make_node()
    started, release = threading.Event(), threading.Event()
    target = (
        f"{_NODE_MODULE}.read_metadata" if operation == "header" else f"{_NODE_MODULE}.ReadEXR._request_canvas_preview"
    )

    def work(*args):
        started.set()
        assert release.wait(5)
        return harness.exr_data if operation == "header" else None

    with patch(target, side_effect=work):
        pending = None
        try:
            node.set_parameter_value("path", str(_single_file(tmp_path)))
            assert await asyncio.to_thread(started.wait, 5)
            pending = node._pending_metadata
            assert isinstance(pending, asyncio.Task)
            # When its caller is cancelled, it retains ownership of native work.
            pending.cancel()
            await asyncio.sleep(0)
            await asyncio.sleep(0)
            assert not pending.done()
        finally:
            release.set()
            if pending is not None:
                assert isinstance(pending, asyncio.Task)
                with pytest.raises(asyncio.CancelledError):
                    await pending
    # Then cancellation propagates after the worker finishes.
    assert pending is not None
    assert pending.cancelled()


@pytest.mark.parametrize("fails", [False, True])
@pytest.mark.parametrize("operation", ["header", "render"])
@pytest.mark.asyncio
async def test_older_execution_cannot_overwrite_newer_execution(harness, tmp_path, fails, operation):
    # Given an execution still reading or rendering the old source.
    node = _make_node()
    first = _single_file(tmp_path)
    second = tmp_path / "new.exr"
    second.write_bytes(b"")
    node.set_parameter_value("path", str(first), initial_setup=True)
    started, release = threading.Event(), threading.Event()
    original = harness.scan_exr_header if operation == "header" else harness.load_exr_channels
    target = f"{_NODE_MODULE}.read_metadata" if operation == "header" else f"{_RENDER_MODULE}.load_exr_channels"

    def read_or_render(path, *args):
        if path == str(first):
            started.set()
            assert release.wait(5)
            if fails:
                raise RuntimeError("older execution failed")
        return original(path, *args)

    with patch(target, side_effect=read_or_render):
        older = asyncio.create_task(node.aprocess())
        try:
            assert await asyncio.to_thread(started.wait, 5)
            # When a newer execution publishes its own result first.
            node.set_parameter_value("path", str(second))
            await node.wait_for_pending_scan()
            await node.aprocess()
            outputs = dict(node.parameter_output_values)
            render = node._render
        finally:
            release.set()
            await older
    # Then the older completion cannot change outputs, cache or control routing.
    assert node.parameter_output_values == outputs
    assert node._render is render
    assert node.get_next_control_output() is node.control_parameter_out


def test_sync_saved_selections_survive_deferred_controls(harness, tmp_path):
    node = _make_node()
    harness.exr_data = _make_exr_data(_make_part("beauty"), _make_part("depth", ("depth.Z",)))
    node.set_parameter_value("path", str(_single_file(tmp_path)))
    node.set_parameter_value("part_name", "1: depth")
    node.set_parameter_value("layer", "depth")
    node.set_parameter_value("frame", 1001)
    asyncio.run(node.wait_for_pending_scan())
    assert node.get_parameter_value("part_name") == "1: depth"
    assert node.get_parameter_value("layer") == "depth"
    assert node.get_parameter_value("frame") == 1001
