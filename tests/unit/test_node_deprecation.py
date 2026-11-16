"""Deprecated load nodes retain their registrations and emit migration warnings."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from griptape_nodes.node_library.library_registry import LibraryNameAndVersion, LibraryRegistry, LibrarySchema
from griptape_nodes.node_library.workflow_registry import (
    LibraryNameAndNodeType,
    WorkflowMetadata,
)
from griptape_nodes.retained_mode.engine import Engine
from griptape_nodes.retained_mode.managers.fitness_problems.workflows.deprecated_node_in_workflow_problem import (
    DeprecatedNodeInWorkflowProblem,
)

MANIFEST = Path(__file__).parents[2] / "griptape-nodes-library.json"
LEGACY_NODES = ("LoadEXR", "LoadEXRSequence")


@pytest.fixture()
def library_schema() -> LibrarySchema:
    return LibrarySchema.model_validate_json(MANIFEST.read_text(encoding="utf-8"))


@pytest.mark.parametrize("node_type", LEGACY_NODES)
def test_manifest_declares_deprecation_without_removal_release(library_schema: LibrarySchema, node_type: str) -> None:
    node = next(node for node in library_schema.nodes if node.class_name == node_type)
    notice = node.metadata.deprecation
    assert notice is not None
    assert notice.removal_version is None
    assert notice.deprecation_message is not None
    assert "Read EXR" in notice.deprecation_message
    assert "migrating missing functionality" in notice.deprecation_message
    assert "remain available" in notice.deprecation_message
    declarations = node.metadata.model_dump(mode="json")["declarations"]
    assert {"type": "lifecycle_stage", "stage": "DEPRECATED"} in declarations
    assert node.metadata.description.startswith("Deprecated.")
    assert node.file_path == (
        "griptape_nodes_openexr/nodes/load_exr.py"
        if node_type == "LoadEXR"
        else "griptape_nodes_openexr/nodes/load_exr_sequence.py"
    )


def test_read_exr_remains_non_deprecated(library_schema: LibrarySchema) -> None:
    node = next(node for node in library_schema.nodes if node.class_name == "ReadEXR")
    assert node.metadata.deprecation is None


def test_library_and_saved_workflow_receive_warnings(library_schema: LibrarySchema) -> None:
    # Given a current library and workflow metadata saved before deprecation.
    engine = Engine()
    previous = LibraryRegistry._libraries.copy()
    LibraryRegistry._libraries.pop(library_schema.name, None)
    try:
        library = LibraryRegistry.generate_new_library(library_data=library_schema)
        from griptape_nodes_openexr.nodes.load_exr import LoadEXR
        from griptape_nodes_openexr.nodes.load_exr_sequence import LoadEXRSequence

        for node_class in (LoadEXR, LoadEXRSequence):
            metadata = next(node.metadata for node in library_schema.nodes if node.class_name == node_class.__name__)
            assert library.register_new_node_type(node_class, metadata) is None
        workflow = WorkflowMetadata(
            name="legacy_load_workflow",
            schema_version=WorkflowMetadata.LATEST_SCHEMA_VERSION,
            engine_version_created_with="0.104.0",
            node_libraries_referenced=[
                LibraryNameAndVersion(library_name=library_schema.name, library_version="0.3.0")
            ],
            node_types_used={
                LibraryNameAndNodeType(library_name=library_schema.name, node_type=node_type)
                for node_type in LEGACY_NODES
            },
        )

        # When compatibility checks resolve the retained node registrations.
        manager = engine.version_compatibility_manager
        library_issues = manager._check_library_for_deprecated_nodes(library_schema)
        workflow_issues = asyncio.run(
            manager._check_workflow_for_deprecated_nodes(workflow, registered_libraries=[library_schema.name])
        )

        # Then both nodes produce warnings with migration guidance, without a removal release.
        assert len(library_issues) == len(LEGACY_NODES)
        assert len(workflow_issues) == len(LEGACY_NODES)
        for issue in [*library_issues, *workflow_issues]:
            assert issue.severity.name == "FLAWED"
            assert issue.problem.removal_version is None
            assert "Read EXR" in issue.problem.deprecation_message
        assert all(isinstance(issue.problem, DeprecatedNodeInWorkflowProblem) for issue in workflow_issues)
    finally:
        LibraryRegistry._libraries.clear()
        LibraryRegistry._libraries.update(previous)
