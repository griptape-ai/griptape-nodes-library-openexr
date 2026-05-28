"""Unit tests for LoadEXR save/reload behaviour.

Tests cover:
- _compute_file_hash correctness
- Fresh node (no file) leaves dynamic groups empty
- after_value_set persists metadata and populates dynamic groups
- Reload from metadata recreates dynamic groups (the save/reload fix)
- Reload with changed file hash warns in aprocess result_details
- Reload with missing file is graceful
"""

from __future__ import annotations

import asyncio
import shutil
from pathlib import Path
from unittest.mock import patch

import pytest

import json

from griptape_nodes_openexr.nodes.load_exr import LoadEXR, _compute_file_hash, _sanitize_for_json

DATA = Path(__file__).parents[1] / "data"
SINGLE_PART = DATA / "single_part_rgba.exr"  # 1 part, 4 channels: A B G R
MULTI_PART = DATA / "multi_part.exr"  # 3 parts: rgba(4ch), depth(1ch), normal(3ch)
INFINITY_ATTRS = DATA / "infinity_attributes.exr"  # custom attrs with Infinity / -Infinity


@pytest.fixture()
def mock_config():
    """Mock GriptapeNodes.ConfigManager so _on_inputs_changed works without a runtime."""
    with patch("griptape_nodes_openexr.nodes.load_exr.GriptapeNodes") as m:
        m.ConfigManager.return_value.get_config_value.return_value = True  # header_only
        yield m


def _make_node(metadata: dict | None = None) -> LoadEXR:
    return LoadEXR("test_node", metadata=metadata)


def _result_details(node: LoadEXR) -> str:
    return node.parameter_output_values.get("result_details", "") or ""


# ---------------------------------------------------------------------------
# _compute_file_hash — pure function, no mock needed
# ---------------------------------------------------------------------------


class TestComputeFileHash:
    def test_returns_64_char_hex(self) -> None:
        result = _compute_file_hash(str(SINGLE_PART))
        assert result is not None
        assert len(result) == 64
        assert all(c in "0123456789abcdef" for c in result)

    def test_deterministic(self) -> None:
        assert _compute_file_hash(str(SINGLE_PART)) == _compute_file_hash(str(SINGLE_PART))

    def test_different_files_differ(self) -> None:
        assert _compute_file_hash(str(SINGLE_PART)) != _compute_file_hash(str(MULTI_PART))

    def test_missing_file_returns_none(self) -> None:
        assert _compute_file_hash("/nonexistent/completely/missing.exr") is None


# ---------------------------------------------------------------------------
# Fresh node — no file path set, GriptapeNodes never called
# ---------------------------------------------------------------------------


class TestFreshNode:
    def test_no_dynamic_params(self) -> None:
        node = _make_node()
        assert node._parts_group.children == []
        assert node._channels_group.children == []

    def test_cached_data_none(self) -> None:
        node = _make_node()
        assert node._cached_exr_data is None

    def test_file_content_changed_flag_false(self) -> None:
        node = _make_node()
        assert node._file_content_changed is False

    def test_metadata_file_path_empty(self) -> None:
        node = _make_node()
        assert node.metadata.get("_file_path", "") == ""


# ---------------------------------------------------------------------------
# after_value_set — metadata persistence and group population
# ---------------------------------------------------------------------------


class TestAfterValueSet:
    def test_stores_file_path_in_metadata(self, mock_config) -> None:
        node = _make_node()
        node.after_value_set(node._file_path_param, str(SINGLE_PART))
        assert node.metadata["_file_path"] == str(SINGLE_PART)

    def test_stores_file_hash_in_metadata(self, mock_config) -> None:
        node = _make_node()
        node.after_value_set(node._file_path_param, str(SINGLE_PART))
        h = node.metadata.get("_file_hash", "")
        assert len(h) == 64
        assert all(c in "0123456789abcdef" for c in h)

    def test_hash_matches_direct_computation(self, mock_config) -> None:
        node = _make_node()
        node.after_value_set(node._file_path_param, str(SINGLE_PART))
        assert node.metadata["_file_hash"] == _compute_file_hash(str(SINGLE_PART))

    def test_single_part_channels_populated(self, mock_config) -> None:
        node = _make_node()
        node.after_value_set(node._file_path_param, str(SINGLE_PART))
        assert len(node._channels_group.children) == 4  # A, B, G, R
        assert node._parts_group.children == []

    def test_multi_part_parts_populated(self, mock_config) -> None:
        node = _make_node()
        node.after_value_set(node._file_path_param, str(MULTI_PART))
        assert len(node._parts_group.children) == 3  # rgba, depth, normal
        assert node._channels_group.children == []

    def test_empty_path_clears_groups(self, mock_config) -> None:
        node = _make_node()
        node.after_value_set(node._file_path_param, str(SINGLE_PART))
        assert len(node._channels_group.children) == 4
        node.after_value_set(node._file_path_param, "")
        assert node._channels_group.children == []
        assert node._parts_group.children == []

    def test_resets_file_content_changed_flag(self, mock_config) -> None:
        node = _make_node()
        node._file_content_changed = True
        node.after_value_set(node._file_path_param, str(SINGLE_PART))
        assert node._file_content_changed is False


# ---------------------------------------------------------------------------
# Reload from metadata — the core save/reload fix
# ---------------------------------------------------------------------------


class TestReloadSameFile:
    def test_single_part_reload_creates_channels(self, mock_config) -> None:
        node = _make_node(metadata={"_file_path": str(SINGLE_PART)})
        assert len(node._channels_group.children) == 4
        assert node._parts_group.children == []

    def test_multi_part_reload_creates_parts(self, mock_config) -> None:
        node = _make_node(metadata={"_file_path": str(MULTI_PART)})
        assert len(node._parts_group.children) == 3
        assert node._channels_group.children == []

    def test_channel_names_match_after_reload(self, mock_config) -> None:
        fresh = _make_node()
        fresh.after_value_set(fresh._file_path_param, str(SINGLE_PART))
        fresh_names = [c.name for c in fresh._channels_group.children]

        reloaded = _make_node(metadata={"_file_path": str(SINGLE_PART)})
        reloaded_names = [c.name for c in reloaded._channels_group.children]

        assert fresh_names == reloaded_names

    def test_multi_part_group_names_match_after_reload(self, mock_config) -> None:
        fresh = _make_node()
        fresh.after_value_set(fresh._file_path_param, str(MULTI_PART))
        fresh_names = [c.name for c in fresh._parts_group.children]

        reloaded = _make_node(metadata={"_file_path": str(MULTI_PART)})
        reloaded_names = [c.name for c in reloaded._parts_group.children]

        assert fresh_names == reloaded_names

    def test_cached_data_populated_after_reload(self, mock_config) -> None:
        node = _make_node(metadata={"_file_path": str(SINGLE_PART)})
        assert node._cached_exr_data is not None

    def test_file_content_changed_flag_false_before_aprocess(self, mock_config) -> None:
        node = _make_node(metadata={"_file_path": str(SINGLE_PART)})
        assert node._file_content_changed is False


# ---------------------------------------------------------------------------
# Reload with changed file hash — warning in aprocess
# ---------------------------------------------------------------------------


class TestReloadChangedFile:
    def test_aprocess_warns_on_hash_mismatch(self, mock_config, tmp_path) -> None:
        shutil.copy(SINGLE_PART, tmp_path / "test.exr")
        path = str(tmp_path / "test.exr")
        node = _make_node(metadata={"_file_path": path, "_file_hash": "deadbeef" * 8})
        # Simulate the framework's step 3: SetParameterValueRequest(initial_setup=True)
        node.set_parameter_value("file_path", path, initial_setup=True)
        asyncio.run(node.aprocess())
        assert node._file_content_changed is True
        assert "changed" in _result_details(node).lower()

    def test_aprocess_no_warning_on_matching_hash(self, mock_config) -> None:
        real_hash = _compute_file_hash(str(SINGLE_PART))
        node = _make_node(metadata={"_file_path": str(SINGLE_PART), "_file_hash": real_hash})
        node.set_parameter_value("file_path", str(SINGLE_PART), initial_setup=True)
        asyncio.run(node.aprocess())
        assert node._file_content_changed is False
        assert "changed" not in _result_details(node).lower()

    def test_aprocess_succeeds_despite_changed_hash(self, mock_config, tmp_path) -> None:
        shutil.copy(SINGLE_PART, tmp_path / "test.exr")
        path = str(tmp_path / "test.exr")
        node = _make_node(metadata={"_file_path": path, "_file_hash": "deadbeef" * 8})
        node.set_parameter_value("file_path", path, initial_setup=True)
        asyncio.run(node.aprocess())
        # Warning is not a failure — node should still succeed
        result = node.parameter_output_values.get("result", "")
        assert result != "failed"


# ---------------------------------------------------------------------------
# Reload with missing file — graceful degradation
# ---------------------------------------------------------------------------


class TestReloadMissingFile:
    def test_init_does_not_raise(self) -> None:
        # os.path.exists returns False before GriptapeNodes is ever called
        node = _make_node(metadata={"_file_path": "/nonexistent/path.exr"})
        assert node is not None

    def test_groups_empty(self) -> None:
        node = _make_node(metadata={"_file_path": "/nonexistent/path.exr"})
        assert node._parts_group.children == []
        assert node._channels_group.children == []

    def test_cached_data_none(self) -> None:
        node = _make_node(metadata={"_file_path": "/nonexistent/path.exr"})
        assert node._cached_exr_data is None

    def test_aprocess_fails_with_not_found_message(self, mock_config) -> None:
        node = _make_node(metadata={"_file_path": "/nonexistent/path.exr"})
        node.set_parameter_value("file_path", "/nonexistent/path.exr", initial_setup=True)
        asyncio.run(node.aprocess())
        details = _result_details(node)
        assert "not found" in details.lower()


# ---------------------------------------------------------------------------
# _sanitize_for_json — pure function, no mocks, no EXR files
# ---------------------------------------------------------------------------


class TestSanitizeForJson:
    def test_infinity_becomes_string(self) -> None:
        assert _sanitize_for_json(float("inf")) == "Infinity"

    def test_negative_infinity_becomes_string(self) -> None:
        assert _sanitize_for_json(float("-inf")) == "-Infinity"

    def test_nan_becomes_string(self) -> None:
        assert _sanitize_for_json(float("nan")) == "NaN"

    def test_finite_float_unchanged(self) -> None:
        assert _sanitize_for_json(1.5) == 1.5

    def test_nested_dict(self) -> None:
        result = _sanitize_for_json({"a": float("inf"), "b": {"c": float("-inf")}})
        assert result == {"a": "Infinity", "b": {"c": "-Infinity"}}

    def test_list(self) -> None:
        result = _sanitize_for_json([float("inf"), 1.0, float("nan")])
        assert result == ["Infinity", 1.0, "NaN"]

    def test_non_float_passthrough(self) -> None:
        assert _sanitize_for_json("hello") == "hello"
        assert _sanitize_for_json(42) == 42
        assert _sanitize_for_json(None) is None

    def test_output_is_valid_json(self) -> None:
        sanitized = _sanitize_for_json({"focus": float("inf"), "normal": 1.0})
        json.loads(json.dumps(sanitized))  # must not raise


# ---------------------------------------------------------------------------
# Infinity EXR fixture — node produces valid JSON custom_attributes
# ---------------------------------------------------------------------------


class TestInfinityJsonSerialization:
    def test_infinity_custom_attr_is_valid_json(self, mock_config) -> None:
        node = _make_node()
        node.after_value_set(node._file_path_param, str(INFINITY_ATTRS))
        raw = node.parameter_output_values.get("custom_attributes", "{}")
        parsed = json.loads(raw)  # raises ValueError if not valid JSON
        assert "focus" in parsed

    def test_infinity_serialized_as_string(self, mock_config) -> None:
        node = _make_node()
        node.after_value_set(node._file_path_param, str(INFINITY_ATTRS))
        raw = node.parameter_output_values.get("custom_attributes", "{}")
        assert json.loads(raw)["focus"] == "Infinity"

    def test_negative_infinity_serialized_as_string(self, mock_config) -> None:
        node = _make_node()
        node.after_value_set(node._file_path_param, str(INFINITY_ATTRS))
        raw = node.parameter_output_values.get("custom_attributes", "{}")
        assert json.loads(raw)["near_clip"] == "-Infinity"
