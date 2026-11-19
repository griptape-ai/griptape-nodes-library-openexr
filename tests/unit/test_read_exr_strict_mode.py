"""Read EXR edit scheduling and execution from restored inputs."""

import asyncio
import threading
from unittest.mock import patch

import pytest

from tests.unit.test_read_exr import _NODE_MODULE, _make_node, _single_file
from tests.unit.test_read_exr import harness as harness


@pytest.mark.parametrize("parameter,value", [("path", "replacement"), ("frame", 2), ("part_name", "0: part")])
@pytest.mark.asyncio
async def test_edit_scheduler_does_not_report_execution_state(harness, tmp_path, parameter, value):
    from griptape_nodes.common.strict_mode import STRICT_MODE, StrictModeScopeKind

    node = _make_node()
    if not hasattr(node, "_reporting_state_set_by"):
        pytest.skip("Engine dependency predates hook-state strict-mode checks")
    # Given a populated node, so path edits also exercise existing cached state.
    node.set_parameter_value("path", str(_single_file(tmp_path)), initial_setup=True)
    await node.aprocess()
    if parameter == "path":
        replacement = tmp_path / "replacement.exr"
        replacement.write_bytes(b"")
        value = str(replacement)
    # When the engine calls the value hook under strict mode.
    with STRICT_MODE.open_scope(
        kind=StrictModeScopeKind("edit"), subject=node.name, library_name="OpenEXR Library", is_worker=False
    ) as scope:
        node.set_parameter_value(parameter, value)
    await node.wait_for_pending_scan()
    # Then only explicitly declared local edit state changes.
    assert [violation for violation in scope.violations if violation.rule_id == "state-outside-parameters"] == []


@pytest.mark.parametrize("fails", [False, True])
@pytest.mark.asyncio
async def test_hydrated_selection_change_invalidates_execution_without_hooks(harness, tmp_path, fails):
    # Given a worker/restored node reading its current parameters without value hooks.
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
                raise RuntimeError("old selection failed")
        return harness.scan_exr_header(path)

    with patch(f"{_NODE_MODULE}.read_metadata", side_effect=read):
        execution = asyncio.create_task(node.aprocess())
        try:
            assert await asyncio.to_thread(started.wait, 5)
            # When hydration changes parameters without scheduling edit reads.
            node.parameter_values["path"] = str(second)
            node.parameter_output_values["owner"] = "new selection metadata"
        finally:
            release.set()
            await execution
    # Then execution freshness follows parameters and preserves newer metadata.
    assert node.parameter_output_values["was_successful"] is False
    details = "old selection failed" if fails else "Selection changed during execution"
    assert details in node.parameter_output_values["result_details"]
    assert node.parameter_output_values["owner"] == "new selection metadata"
    assert node.get_next_control_output() is node.failure_output
    assert harness.written == []
    await node.aprocess()
    assert node.parameter_output_values["was_successful"] is True
    assert node.parameter_output_values["part"].file_path == str(second)


@pytest.mark.asyncio
async def test_hook_only_replaces_declared_edit_scheduler_attributes(harness, tmp_path):
    node = _make_node()
    node.set_parameter_value("path", str(_single_file(tmp_path)), initial_setup=True)
    await node.aprocess()
    before = {name: id(value) for name, value in vars(node).items()}
    replacement = tmp_path / "replacement.exr"
    replacement.write_bytes(b"")
    node.set_parameter_value("path", str(replacement))
    changed = {name for name, value in vars(node).items() if before.get(name) != id(value)}
    await node.wait_for_pending_scan()
    # This assertion also runs against engines predating the strict-mode checker.
    assert changed == node.attributes_outside_parameters
