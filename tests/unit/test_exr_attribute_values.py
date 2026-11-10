"""Unit tests for EXR header attribute conversion and lookup."""

from __future__ import annotations

import json

import numpy as np
import pytest

from griptape_nodes_openexr.exr.exr_io import _require_exr_attribute
from griptape_nodes_openexr.exr.exr_types import _convert_attribute_value


class TestConvertAttributeValue:
    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            (float("nan"), "NaN"),
            (float("inf"), "Infinity"),
            (float("-inf"), "-Infinity"),
            (np.float32("nan"), "NaN"),
            (np.float32("inf"), "Infinity"),
            (np.float32("-inf"), "-Infinity"),
            (np.float64("nan"), "NaN"),
        ],
    )
    def test_non_finite_scalar_becomes_string(self, value: object, expected: str) -> None:
        assert _convert_attribute_value(value) == expected

    def test_finite_numpy_scalar_becomes_python_float(self) -> None:
        result = _convert_attribute_value(np.float32(1.5))
        assert result == 1.5
        assert type(result) is float

    def test_non_finite_values_in_array_become_strings(self) -> None:
        result = _convert_attribute_value(np.array([np.nan, np.inf, -np.inf, 2.0], dtype=np.float32))
        assert result == ["NaN", "Infinity", "-Infinity", 2.0]

    def test_non_finite_values_in_matrix_become_strings(self) -> None:
        result = _convert_attribute_value(np.array([[1.0, np.nan], [np.inf, 0.0]], dtype=np.float32))
        assert result == [[1.0, "NaN"], ["Infinity", 0.0]]

    def test_non_finite_values_in_tuple_become_strings(self) -> None:
        assert _convert_attribute_value((1.0, float("nan"))) == [1.0, "NaN"]

    def test_passthrough(self) -> None:
        assert _convert_attribute_value("hello") == "hello"
        assert _convert_attribute_value(42) == 42
        assert _convert_attribute_value(True) is True

    def test_output_is_strict_json(self) -> None:
        value = {"a": _convert_attribute_value(np.array([np.nan, np.inf], dtype=np.float32))}
        json.loads(json.dumps(value, allow_nan=False))  # must not raise


class TestRequireExrAttribute:
    def test_returns_array_value(self) -> None:
        value = np.array([0.0, 0.0], dtype=np.float32)
        assert _require_exr_attribute({"screenWindowCenter": value}, "screenWindowCenter") is value

    def test_missing_attribute_raises(self) -> None:
        with pytest.raises(ValueError, match="'dataWindow' is missing"):
            _require_exr_attribute({}, "dataWindow")
