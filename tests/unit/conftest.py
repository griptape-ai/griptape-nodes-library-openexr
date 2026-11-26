"""Unit test fixtures shared across modules."""

from __future__ import annotations

import pytest

from griptape_nodes_openexr.exr.colour.ocio_helpers import reset_colorspace_transform_request_type_cache


@pytest.fixture(autouse=True)
def _reset_ocio_cache() -> None:
    """Keep the memoised ColorspaceTransformRequest lookup from leaking between tests."""
    reset_colorspace_transform_request_type_cache()
