"""Shared rendering-limit configuration and canvas validation."""

from unittest.mock import patch

import pytest
from griptape_nodes.retained_mode.events.config_events import GetConfigValueResultFailure, GetConfigValueResultSuccess
from griptape_nodes.retained_mode.griptape_nodes import GriptapeNodes

from griptape_nodes_openexr.exr import render_limits
from griptape_nodes_openexr.exr.exr_types import WindowCoordinates


@pytest.mark.parametrize("value", [0, -1, True, "100", 1.5])
def test_invalid_settings_fail(value: object) -> None:
    result = GetConfigValueResultSuccess(value=value, result_details="override")
    assert isinstance(result, GetConfigValueResultSuccess)
    with (
        patch.object(GriptapeNodes, "handle_request", return_value=result),
        pytest.raises(ValueError, match="positive integer"),
    ):
        render_limits.get_render_limit()


def test_missing_setting_uses_shared_default() -> None:
    result = GetConfigValueResultFailure(result_details="missing")
    assert isinstance(result, GetConfigValueResultFailure)
    with patch.object(GriptapeNodes, "handle_request", return_value=result):
        assert render_limits.get_render_limit() == 8192 * 8192


def test_setting_is_read_each_time() -> None:
    results = [GetConfigValueResultSuccess(value=value, result_details="override") for value in [8, 16]]
    assert all(isinstance(result, GetConfigValueResultSuccess) for result in results)
    with patch.object(GriptapeNodes, "handle_request", side_effect=results):
        assert render_limits.get_render_limit() == 8
        assert render_limits.get_render_limit() == 16


def test_canvas_limit_reports_required_size() -> None:
    with pytest.raises(ValueError, match="4x4.*16.*openexr.max_render_pixels"):
        render_limits.check_canvas(WindowCoordinates(0, 0, 3, 3), 8)
