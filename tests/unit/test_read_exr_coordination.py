"""Read EXR request ownership regressions."""

import asyncio
import threading
from pathlib import Path
from unittest.mock import patch

import pytest

from tests.unit.test_read_exr import _NODE_MODULE, _make_node, _single_file
from tests.unit.test_read_exr import harness as harness  # Re-export the shared pytest fixture.


@pytest.mark.asyncio
async def test_execution_joins_running_edit_read(harness, tmp_path: Path) -> None:
    node = _make_node()
    started = threading.Event()
    release = threading.Event()
    original = harness.scan_exr_header

    def read(path):
        started.set()
        assert release.wait(5)
        return original(path)

    with patch(f"{_NODE_MODULE}.read_metadata", side_effect=read):
        node.set_parameter_value("path", str(_single_file(tmp_path)))
        assert await asyncio.to_thread(started.wait, 5)
        execution = asyncio.create_task(node.aprocess())
        await asyncio.sleep(0.02)
        release.set()
        await execution
    assert len(harness.scan_calls) == 1
    assert node.parameter_output_values["was_successful"] is True


def test_sync_setter_returns_before_disk_read(harness, tmp_path: Path) -> None:
    node = _make_node()
    release = threading.Event()
    original = harness.scan_exr_header

    def read(path):
        assert release.wait(5)
        return original(path)

    with patch(f"{_NODE_MODULE}.read_metadata", side_effect=read):
        node.set_parameter_value("path", str(_single_file(tmp_path)))
        assert node.parameter_output_values["compression"] == ""
        release.set()
        asyncio.run(node.wait_for_pending_scan())
    assert node.parameter_output_values["compression"] != ""


@pytest.mark.parametrize("raw_path", ["{{workspace}}/shot.exr", "shot.exr"])
def test_connected_entry_is_resolved_with_engine_file(harness, tmp_path: Path, raw_path: str) -> None:
    from tests.unit.test_read_exr import _make_sequence

    resolved = _single_file(tmp_path)
    sequence = _make_sequence([resolved], start=1001)
    sequence["entries"][0]["path"] = raw_path
    node = _make_node()
    with patch("griptape_nodes.files.file.File.resolve", return_value=str(resolved)) as resolve:
        node.set_parameter_value("path", sequence)
        asyncio.run(node.aprocess())
    assert resolve.called
    assert harness.scan_calls == [str(resolved)]
    assert node.parameter_output_values["was_successful"] is True
    assert node.parameter_output_values["part"].file_path == str(resolved)


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
    node = _make_node()
    node.set_parameter_value("path", str(_single_file(tmp_path)))
    asyncio.run(node.wait_for_pending_scan())
    before = len(harness.scan_calls)
    asyncio.run(node.aprocess())
    assert len(harness.scan_calls) == before + 1


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
        assert await asyncio.to_thread(started.wait, 5)
        node.set_parameter_value("path", str(second))
        await node.wait_for_pending_scan()
        release.set()
        await execution
    assert harness.written == []
    assert node._render is None
    assert node._scan is not None
    assert node._scan.key == str(second)


@pytest.mark.asyncio
async def test_shutdown_during_read_prevents_execution(harness, tmp_path: Path) -> None:
    node = _make_node()
    started = threading.Event()
    release = threading.Event()
    original = harness.scan_exr_header

    def read(path):
        started.set()
        assert release.wait(5)
        return original(path)

    with patch(f"{_NODE_MODULE}.read_metadata", side_effect=read):
        node.set_parameter_value("path", str(_single_file(tmp_path)))
        assert await asyncio.to_thread(started.wait, 5)
        execution = asyncio.create_task(node.aprocess())
        await asyncio.sleep(0.02)
        node._service.shutdown()
        release.set()
        await execution
    assert harness.load_calls == []
    assert node.parameter_output_values.get("was_successful") is not True
