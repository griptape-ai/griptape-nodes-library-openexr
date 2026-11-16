"""Replay saved legacy node requests against the current manifest and real engine."""

from __future__ import annotations

import asyncio
import runpy
from pathlib import Path

import pytest
from griptape_nodes.node_library.library_registry import LibraryRegistry, LibrarySchema
from griptape_nodes.retained_mode.events.flow_events import DeleteFlowRequest, DeleteFlowResultSuccess
from griptape_nodes.retained_mode.griptape_nodes import GriptapeNodes

from griptape_nodes_openexr.nodes.display_exr_channel import DisplayEXRChannel
from griptape_nodes_openexr.nodes.display_exr_part import DisplayEXRPart
from griptape_nodes_openexr.nodes.load_exr import LoadEXR
from griptape_nodes_openexr.nodes.load_exr_sequence import LoadEXRSequence

ROOT = Path(__file__).parents[2]


@pytest.mark.parametrize("filename", ["single_part_rgba.exr", "multi_part.exr"])
@pytest.mark.parametrize("policy", ["skip", "fail"])
def test_saved_legacy_requests_retain_connections_and_execution(filename: str, policy: str) -> None:
    # Given the real manifest and request sequence saved before deprecation.
    schema = LibrarySchema.model_validate_json((ROOT / "griptape-nodes-library.json").read_text(encoding="utf-8"))
    previous = LibraryRegistry._libraries.copy()
    LibraryRegistry._libraries.pop(schema.name, None)
    library = LibraryRegistry.generate_new_library(library_data=schema)
    for node_class in (LoadEXR, LoadEXRSequence, DisplayEXRPart, DisplayEXRChannel):
        metadata = next(node.metadata for node in schema.nodes if node.class_name == node_class.__name__)
        assert library.register_new_node_type(node_class, metadata) is None
    context = GriptapeNodes.ContextManager()
    context.push_workflow("legacy_exr_test")
    path = str(ROOT / "tests" / "data" / filename)
    sequence = {
        "directory": str(ROOT / "tests" / "data"),
        "pattern": "test.####.exr",
        "entries": [{"number": number, "padded_number": str(number), "path": path} for number in (1001, 1003)],
        "first": 1001,
        "last": 1003,
        "discovered_first": 1001,
        "discovered_last": 1003,
        "padding": 4,
        "policy": "skip",
        "present_numbers": [1001, 1003],
        "missing_numbers": [1002],
        "dropped_negative_number_count": 0,
    }
    build = runpy.run_path(str(ROOT / "tests" / "workflows" / "legacy_exr_loads.py"))["build_workflow"]
    try:
        # When saved requests recreate nodes, parameters and connections.
        asyncio.run(build(path, sequence, policy))
        file_node = GriptapeNodes.ObjectManager().get_object_by_name("legacy_file")
        sequence_node = GriptapeNodes.ObjectManager().get_object_by_name("legacy_sequence")
        assert isinstance(file_node, LoadEXR)
        assert isinstance(sequence_node, LoadEXRSequence)
        for node in (file_node, sequence_node):
            notice = node.metadata["library_node_metadata"]["deprecation"]
            assert notice is not None
            assert notice["removal_version"] is None
            assert "Read EXR" in notice["deprecation_message"]

        # Then saved scalar and dynamic connections still target the original parameters.
        connections = GriptapeNodes.FlowManager().get_connections().get_all_outgoing_connections(file_node)
        assert {connection.source_parameter.name for connection in connections} == (
            {"channel_rgba_R", "image_width", "part_rgba"}
            if filename == "multi_part.exr"
            else {"channel_R", "image_width"}
        )
        asyncio.run(file_node.aprocess())
        asyncio.run(sequence_node.aprocess())
        assert file_node.parameter_output_values["was_successful"] is True
        assert sequence_node.parameter_output_values["was_successful"] is True, sequence_node.parameter_output_values[
            "result_details"
        ]
        channel = "channel_rgba_R" if filename == "multi_part.exr" else "channel_R"
        assert file_node.parameter_output_values[channel].channel.name == "R"
        assert file_node.parameter_output_values["compression"] == "ZIP_COMPRESSION"
        if filename == "multi_part.exr":
            assert file_node.parameter_output_values["part_rgba"].name == "rgba"
        parts = sequence_node.parameter_output_values["parts"]
        assert [part.frame_number for part in parts] == [1001, 1003]
        assert sequence_node.parameter_output_values["frame_count"] == 2
        assert sequence_node.get_parameter_value("threads") == 2
        assert sequence_node.get_parameter_value("on_mismatch") == policy
    finally:
        result = GriptapeNodes.handle_request(DeleteFlowRequest(flow_name="legacy_exr"))
        assert isinstance(result, DeleteFlowResultSuccess)
        context.pop_workflow()
        LibraryRegistry._libraries.clear()
        LibraryRegistry._libraries.update(previous)
