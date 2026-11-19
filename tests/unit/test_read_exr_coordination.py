"""Independent ReadEXR metadata reads and stale-result regressions."""

import asyncio
import threading
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from tests.unit.test_read_exr import _NODE_MODULE, _make_node, _single_file
from tests.unit.test_read_exr import harness as harness  # Re-export the shared pytest fixture.


@pytest.mark.asyncio
async def test_wait_from_another_thread_uses_host_loop(harness, tmp_path: Path) -> None:
    node = _make_node()
    harness.scan_delay = 0.05
    node.set_parameter_value("path", str(_single_file(tmp_path)))
    await asyncio.to_thread(lambda: asyncio.run(node.wait_for_pending_scan()))
    assert node.parameter_output_values["compression"] != ""


@pytest.mark.asyncio
async def test_cancelled_edit_read_can_be_refreshed(harness, tmp_path: Path) -> None:
    node = _make_node()
    harness.scan_delay = 0.05
    node.set_parameter_value("path", str(_single_file(tmp_path)))
    pending = node._pending_metadata
    assert pending is not None
    pending.cancel()
    await node.wait_for_pending_scan()
    assert node.parameter_output_values["compression"] != ""


@pytest.mark.asyncio
async def test_library_unload_prevents_late_metadata_update(harness, tmp_path: Path) -> None:
    from griptape_nodes_openexr.advanced_library import OpenEXRLibrary

    node = _make_node()
    harness.scan_delay = 0.05
    node.set_parameter_value("path", str(_single_file(tmp_path)))
    await asyncio.sleep(0.01)
    OpenEXRLibrary().before_library_unregistered(MagicMock(), MagicMock())
    await asyncio.sleep(0.08)
    assert node.parameter_output_values["compression"] == ""


@pytest.mark.asyncio
async def test_execution_reads_independently_of_running_edit_read(harness, tmp_path: Path) -> None:
    node = _make_node()
    path = _single_file(tmp_path)
    edit_started = threading.Event()
    execution_started = threading.Event()
    release_edit = threading.Event()
    lock = threading.Lock()
    reads = 0

    def read(file_path):
        nonlocal reads
        with lock:
            reads += 1
            first_read = reads == 1
        if first_read:
            edit_started.set()
            assert release_edit.wait(5), "edit read was not released"
        else:
            execution_started.set()
        return harness.scan_exr_header(file_path)

    with patch(f"{_NODE_MODULE}.read_metadata", side_effect=read):
        execution = None
        edit_task = None
        try:
            node.set_parameter_value("path", str(path))
            assert await asyncio.to_thread(edit_started.wait, 5)
            edit_task = node._pending_metadata
            execution = asyncio.create_task(node.aprocess())
            assert await asyncio.to_thread(execution_started.wait, 2), "execution joined the blocked edit read"
            await asyncio.wait_for(asyncio.shield(execution), 5)
            assert not release_edit.is_set()
            assert node.parameter_output_values["was_successful"] is True
        finally:
            release_edit.set()
            if execution is not None:
                await execution
            if isinstance(edit_task, asyncio.Task):
                await edit_task
            await node.wait_for_pending_scan()
    assert reads == 2
    assert harness.scan_calls == [str(path), str(path)]


@pytest.mark.asyncio
async def test_node_b_progresses_while_node_a_read_blocks(harness, tmp_path: Path) -> None:
    node_a, node_b = _make_node(), _make_node()
    path_a = _single_file(tmp_path)
    path_b = tmp_path / "second.exr"
    path_b.write_bytes(b"")
    started_a = threading.Event()
    started_b = threading.Event()
    release_a = threading.Event()
    loop_thread = threading.get_ident()
    blocked_threads = []

    def read(file_path):
        if file_path == str(path_a):
            blocked_threads.append(threading.get_ident())
            started_a.set()
            # Event.wait releases the GIL. B must not queue behind this read.
            assert release_a.wait(5), "node A read was not released"
        else:
            started_b.set()
        return harness.scan_exr_header(file_path)

    with patch(f"{_NODE_MODULE}.read_metadata", side_effect=read):
        try:
            node_a.set_parameter_value("path", str(path_a))
            assert await asyncio.to_thread(started_a.wait, 5)
            node_b.set_parameter_value("path", str(path_b))
            assert await asyncio.to_thread(started_b.wait, 2), "node B queued behind node A"
            await asyncio.wait_for(node_b.wait_for_pending_scan(), 5)
            assert not release_a.is_set()
            assert node_b.parameter_output_values["compression"] == "ZIP_COMPRESSION"
            assert node_a.parameter_output_values["compression"] == ""
            assert loop_thread not in blocked_threads
        finally:
            release_a.set()
            await node_a.wait_for_pending_scan()
            await node_b.wait_for_pending_scan()
    assert sorted(harness.scan_calls) == sorted([str(path_a), str(path_b)])


@pytest.mark.parametrize("explicit_read", ["wait_for_pending_scan", "aprocess"])
def test_sync_setter_records_selection_without_reading(harness, tmp_path: Path, explicit_read: str) -> None:
    node = _make_node()
    path = _single_file(tmp_path)
    started = threading.Event()

    def read(file_path):
        started.set()
        return harness.scan_exr_header(file_path)

    with patch(f"{_NODE_MODULE}.read_metadata", side_effect=read) as reader:
        node.set_parameter_value("path", str(path))
        assert node.get_parameter_value("path") == str(path)
        assert node.parameter_output_values["compression"] == ""
        assert not started.wait(0.1), "setter dispatched a read without a running loop"
        reader.assert_not_called()
        asyncio.run(getattr(node, explicit_read)())
        reader.assert_called_once_with(str(path))
    assert node.parameter_output_values["compression"] == "ZIP_COMPRESSION"


@pytest.mark.asyncio
async def test_async_setter_starts_read_without_explicit_wait(harness, tmp_path: Path) -> None:
    node = _make_node()
    started = threading.Event()
    release = threading.Event()
    loop_thread = threading.get_ident()
    read_threads = []

    def read(path):
        read_threads.append(threading.get_ident())
        started.set()
        assert release.wait(5), "automatic edit read was not released"
        return harness.scan_exr_header(path)

    with patch(f"{_NODE_MODULE}.read_metadata", side_effect=read):
        try:
            node.set_parameter_value("path", str(_single_file(tmp_path)))
            assert await asyncio.to_thread(started.wait, 5)
            assert node.parameter_output_values["compression"] == ""
            assert loop_thread not in read_threads
        finally:
            release.set()
            await node.wait_for_pending_scan()
    assert node.parameter_output_values["compression"] == "ZIP_COMPRESSION"


@pytest.mark.parametrize("raw_path", ["{{workspace}}/shot.exr", "shot.exr", "../shot.exr"])
def test_connected_entry_is_resolved_with_engine_file(harness, tmp_path: Path, raw_path: str) -> None:
    from tests.unit.test_read_exr import _make_sequence

    resolved = _single_file(tmp_path)
    sequence = _make_sequence([resolved], start=1001)
    sequence["entries"][0]["path"] = raw_path
    node = _make_node()
    with patch("griptape_nodes.files.file.File.resolve", return_value=str(resolved)) as resolve:
        node.set_parameter_value("path", sequence)
        asyncio.run(node.wait_for_pending_scan())
        assert harness.scan_calls == [str(resolved)]
        harness.scan_calls.clear()
        asyncio.run(node.aprocess())
    assert resolve.called
    assert harness.scan_calls == [str(resolved)]
    assert node.parameter_output_values["was_successful"] is True
    assert node.parameter_output_values["part"].file_path == str(resolved)


def test_metadata_outputs_keep_shared_tooltips_and_types(harness) -> None:
    from griptape_nodes.exe_types.core_types import ParameterMode

    from griptape_nodes_openexr.exr.header_metadata import HEADER_METADATA_DEFINITIONS

    node = _make_node()
    for name, definition in HEADER_METADATA_DEFINITIONS.items():
        parameter = node.get_parameter_by_name(name)
        assert parameter is not None
        assert parameter.tooltip == definition.tooltip
        assert parameter.type == definition.type
        assert parameter.default_value == definition.default
        assert parameter.allowed_modes == {ParameterMode.OUTPUT}


def test_unsupported_metadata_type_fails_explicitly(harness) -> None:
    from types import SimpleNamespace

    definition = SimpleNamespace(type="bool", default=False, display_name="Unsupported", tooltip="Unsupported")
    with patch(f"{_NODE_MODULE}.HEADER_METADATA_DEFINITIONS", {"unsupported": definition}):
        with pytest.raises(ValueError, match="Unsupported metadata output type"):
            _make_node()


def test_sequence_identity_includes_entry_paths() -> None:
    from griptape_nodes_openexr.nodes.read_exr import _scan_key_for
    from tests.unit.test_read_exr import _make_sequence

    first = _make_sequence([Path("/renders/a.exr")], start=1001)
    second = _make_sequence([Path("/renders/b.exr")], start=1001)
    assert _scan_key_for(first) != _scan_key_for(second)


def test_completed_read_is_not_reused_on_execution(harness, tmp_path: Path) -> None:
    from tests.unit.test_read_exr import _make_exr_data

    node = _make_node()
    path = _single_file(tmp_path)
    harness.exr_data.parts[0].header.owner = "previous artist"
    node.set_parameter_value("path", str(path))
    asyncio.run(node.wait_for_pending_scan())
    assert node.parameter_output_values["owner"] == "previous artist"
    replacement = _make_exr_data()
    replacement.parts[0].header.owner = "current artist"
    harness.exr_data_by_path[str(path)] = replacement
    before = len(harness.scan_calls)
    asyncio.run(node.aprocess())
    assert len(harness.scan_calls) == before + 1
    assert node.parameter_output_values["owner"] == "current artist"


@pytest.mark.asyncio
async def test_source_edit_retires_viewer_render_while_header_read_is_blocked(harness, tmp_path: Path) -> None:
    # Given a completed render of A and a replacement B whose header read blocks.
    node = _make_node()
    first = _single_file(tmp_path)
    second = tmp_path / "second.exr"
    second.write_bytes(b"")
    node.set_parameter_value("path", str(first), initial_setup=True)
    await node.aprocess()
    assert node._render is not None
    started, release = threading.Event(), threading.Event()

    def read(path):
        started.set()
        assert release.wait(5), "replacement read was not released"
        return harness.scan_exr_header(path)

    with patch(f"{_NODE_MODULE}.read_metadata", side_effect=read):
        try:
            # When B's edit read starts but cannot finish.
            node.set_parameter_value("path", str(second))
            assert await asyncio.to_thread(started.wait, 5)
            # Then the viewer has no previous source to open before adoption.
            with patch(f"{_NODE_MODULE}.handle_viewer_button_click") as viewer:
                node._on_open_viewer_click(MagicMock(), MagicMock())
            viewer.assert_called_once_with(node.name, None)
            assert node._render is None
        finally:
            release.set()
            await node.wait_for_pending_scan()


def test_source_change_resets_obsolete_part(harness, tmp_path: Path) -> None:
    from tests.unit.test_read_exr import _make_exr_data, _make_part

    harness.exr_data = _make_exr_data(_make_part("beauty"), _make_part("depth", ("Z",)))
    node = _make_node()
    node.set_parameter_value("path", str(_single_file(tmp_path)))
    asyncio.run(node.wait_for_pending_scan())
    node.set_parameter_value("part_name", "1: depth")
    asyncio.run(node.wait_for_pending_scan())
    new_path = tmp_path / "new.exr"
    new_path.write_bytes(b"")
    harness.exr_data_by_path[str(new_path)] = _make_exr_data(_make_part("newbeauty"))
    node.set_parameter_value("path", str(new_path))
    asyncio.run(node.aprocess())
    assert node.parameter_output_values["was_successful"] is True
    assert node.get_parameter_value("part_name") == "0: newbeauty"


@pytest.mark.parametrize("fails", [False, True])
@pytest.mark.asyncio
async def test_selection_change_during_render_does_not_update_old_outputs(harness, tmp_path: Path, fails: bool) -> None:
    from tests.unit.test_read_exr import _RENDER_MODULE

    first = _single_file(tmp_path)
    second = tmp_path / "second.exr"
    second.write_bytes(b"")
    node = _make_node()
    node.set_parameter_value("path", str(first))
    await node.wait_for_pending_scan()
    started, release = threading.Event(), threading.Event()
    original = harness.load_exr_channels

    def load(*args):
        started.set()
        assert release.wait(5)
        if fails:
            raise RuntimeError("old frame render failed")
        return original(*args)

    with patch(f"{_RENDER_MODULE}.load_exr_channels", side_effect=load):
        execution = asyncio.create_task(node.aprocess())
        try:
            assert await asyncio.to_thread(started.wait, 5)
            node.set_parameter_value("path", str(second))
            await node.wait_for_pending_scan()
        finally:
            release.set()
            await execution
    assert harness.written == []
    assert node._render is None
    assert node._scan is not None
    assert node._scan.key == str(second)
    assert node.parameter_output_values["was_successful"] is False
    expected_details = "old frame render failed" if fails else "Selection changed during execution"
    assert expected_details in node.parameter_output_values["result_details"]
    assert node.get_next_control_output() is node.failure_output
    assert node.parameter_output_values["compression"] == "ZIP_COMPRESSION"
    for name in ("image", "part", "channels"):
        assert node.parameter_output_values[name] is None


@pytest.mark.parametrize("phase", ["read", "render"])
@pytest.mark.parametrize("fails", [False, True])
@pytest.mark.asyncio
async def test_returning_to_original_selection_does_not_revive_execution(harness, tmp_path, phase, fails):
    from tests.unit.test_read_exr import _RENDER_MODULE

    # Given an execution blocked on A while fresh edit reads remain available.
    node = _make_node()
    first = _single_file(tmp_path)
    second = tmp_path / "second.exr"
    second.write_bytes(b"")
    node.set_parameter_value("path", str(first))
    await node.wait_for_pending_scan()
    started, release = threading.Event(), threading.Event()
    original = harness.scan_exr_header if phase == "read" else harness.load_exr_channels
    blocked = False

    def work(*args):
        nonlocal blocked
        if not blocked:
            blocked = True
            started.set()
            assert release.wait(5), "execution was not released"
            if fails:
                raise RuntimeError("superseded execution failed")
        return original(*args)

    target = f"{_NODE_MODULE}.read_metadata" if phase == "read" else f"{_RENDER_MODULE}.load_exr_channels"
    with patch(target, side_effect=work):
        execution = asyncio.create_task(node.aprocess())
        try:
            assert await asyncio.to_thread(started.wait, 5)
            # When the source moves A -> B -> A and the newer A read completes.
            node.set_parameter_value("path", str(second))
            await node.wait_for_pending_scan()
            node.set_parameter_value("path", str(first))
            await node.wait_for_pending_scan()
            scan = node._scan
            assert node.parameter_output_values["compression"] == "ZIP_COMPRESSION"
        finally:
            release.set()
            await execution

    # Then the old run fails without clearing the newer metadata or publishing pixels.
    assert node._scan is scan
    assert node.parameter_output_values["compression"] == "ZIP_COMPRESSION"
    assert node.parameter_output_values["was_successful"] is False
    details = "superseded execution failed" if fails else "Selection changed during execution"
    assert details in node.parameter_output_values["result_details"]
    assert node.get_next_control_output() is node.failure_output
    assert harness.written == []
    for name in ("image", "part", "channels"):
        assert node.parameter_output_values[name] is None


@pytest.mark.parametrize("during_execution", [False, True])
@pytest.mark.parametrize("fails", [False, True])
@pytest.mark.asyncio
async def test_node_deletion_during_read_prevents_output(
    harness, tmp_path: Path, during_execution: bool, fails: bool
) -> None:
    from griptape_nodes_openexr.exr.header_metadata import HEADER_METADATA_DEFAULTS

    node = _make_node()
    started = threading.Event()
    release = threading.Event()
    finished = threading.Event()

    def read(path):
        started.set()
        try:
            assert release.wait(5), "deleted node's read was not released"
            if fails:
                raise RuntimeError("deleted node read failed")
            return harness.scan_exr_header(path)
        finally:
            finished.set()

    with patch(f"{_NODE_MODULE}.read_metadata", side_effect=read):
        execution = None
        try:
            node.set_parameter_value("path", str(_single_file(tmp_path)), initial_setup=during_execution)
            if during_execution:
                execution = asyncio.create_task(node.aprocess())
            assert await asyncio.to_thread(started.wait, 5)
            node.after_node_deleted()
            outputs_after_deletion = dict(node.parameter_output_values)
        finally:
            release.set()
            if execution is not None:
                await execution
            assert await asyncio.to_thread(finished.wait, 5)
            await node.wait_for_pending_scan()
            # Give callbacks queued by the finished worker a turn on the loop.
            await asyncio.sleep(0)
    assert node.parameter_output_values == outputs_after_deletion
    assert {name: node.parameter_output_values[name] for name in HEADER_METADATA_DEFAULTS} == HEADER_METADATA_DEFAULTS
    assert harness.load_calls == []
    assert harness.written == []
    assert node.parameter_output_values.get("was_successful") is not True
