"""Retained request sequence from a workflow saved before node deprecation.

Paths and sequence entries are supplied by the test so the fixture is portable.
The saved node metadata deliberately contains no deprecation declarations.
"""

from griptape_nodes.retained_mode.events.connection_events import (
    CreateConnectionRequest,
    CreateConnectionResultSuccess,
)
from griptape_nodes.retained_mode.events.flow_events import CreateFlowRequest, CreateFlowResultSuccess
from griptape_nodes.retained_mode.events.node_events import CreateNodeRequest, CreateNodeResultSuccess
from griptape_nodes.retained_mode.events.parameter_events import (
    SetParameterValueRequest,
    SetParameterValueResultSuccess,
)
from griptape_nodes.retained_mode.griptape_nodes import GriptapeNodes


async def build_workflow(file_path: str, sequence: dict, mismatch_policy: str) -> None:
    result = await GriptapeNodes.ahandle_request(CreateFlowRequest(parent_flow_name=None, flow_name="legacy_exr"))
    assert isinstance(result, CreateFlowResultSuccess)
    for node_type, name, metadata in (
        (
            "LoadEXR",
            "legacy_file",
            {
                "_file_path": file_path,
                "library_node_metadata": {"deprecation": None, "declarations": []},
            },
        ),
        ("LoadEXRSequence", "legacy_sequence", {"library_node_metadata": {"deprecation": None}}),
        ("DisplayEXRChannel", "legacy_channel", {}),
        ("DisplayEXRPart", "legacy_part", {}),
    ):
        result = await GriptapeNodes.ahandle_request(
            CreateNodeRequest(
                node_type=node_type,
                specific_library_name="OpenEXR Library",
                node_name=name,
                metadata=metadata,
                initial_setup=True,
            )
        )
        assert isinstance(result, CreateNodeResultSuccess)

    for name, parameter, value in (
        ("legacy_file", "file_path", file_path),
        ("legacy_sequence", "sequence", sequence),
        ("legacy_sequence", "part_index", 0),
        ("legacy_sequence", "threads", 2),
        ("legacy_sequence", "on_mismatch", mismatch_policy),
    ):
        result = await GriptapeNodes.ahandle_request(
            SetParameterValueRequest(node_name=name, parameter_name=parameter, value=value, initial_setup=True)
        )
        assert isinstance(result, SetParameterValueResultSuccess)

    channel = "channel_rgba_R" if "multi_part" in file_path else "channel_R"
    for source, target, parameter in (
        (channel, "legacy_channel", "channel_r"),
        ("image_width", "legacy_part", "exposure"),
    ):
        result = await GriptapeNodes.ahandle_request(
            CreateConnectionRequest(
                source_node_name="legacy_file",
                source_parameter_name=source,
                target_node_name=target,
                target_parameter_name=parameter,
                initial_setup=True,
            )
        )
        assert isinstance(result, CreateConnectionResultSuccess)
    if "multi_part" in file_path:
        result = await GriptapeNodes.ahandle_request(
            CreateConnectionRequest(
                source_node_name="legacy_file",
                source_parameter_name="part_rgba",
                target_node_name="legacy_part",
                target_parameter_name="part",
                initial_setup=True,
            )
        )
        assert isinstance(result, CreateConnectionResultSuccess)
