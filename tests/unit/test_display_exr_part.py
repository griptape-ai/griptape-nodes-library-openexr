"""Unit tests for DisplayEXRPart node — color mode, metadata persistence, and visibility."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from griptape_nodes_openexr.exr.ocio_helpers import COLOR_MODE_BASIC, COLOR_MODE_OCIO

_NODE_MODULE = "griptape_nodes_openexr.nodes.display_exr_part"


def _make_node(ocio_available: bool = False, metadata: dict | None = None):
    """Instantiate DisplayEXRPart with OCIO availability controlled by ocio_available."""
    from griptape_nodes_openexr.nodes.display_exr_part import DisplayEXRPart

    detected = MagicMock() if ocio_available else None
    with patch(f"{_NODE_MODULE}.find_colorspace_transform_request_type", return_value=detected):
        return DisplayEXRPart("test_node", metadata=metadata)


# ---------------------------------------------------------------------------
# Initial visibility — new node (no metadata)
# ---------------------------------------------------------------------------


class TestInitialVisibility:
    def test_new_node_no_ocio_starts_in_basic_mode(self) -> None:
        with patch(f"{_NODE_MODULE}.apply_color_mode_visibility") as mock_vis:
            _make_node(ocio_available=False)

        mock_vis.assert_called_once()
        assert mock_vis.call_args[0][1] is False

    def test_new_node_with_ocio_starts_in_ocio_mode(self) -> None:
        with patch(f"{_NODE_MODULE}.apply_color_mode_visibility") as mock_vis:
            _make_node(ocio_available=True)

        mock_vis.assert_called_once()
        assert mock_vis.call_args[0][1] is True

    def test_new_node_no_metadata_color_mode_not_yet_set(self) -> None:
        node = _make_node(ocio_available=False)
        assert node.metadata.get("_color_mode") is None

    def test_new_node_with_ocio_metadata_not_set_yet(self) -> None:
        node = _make_node(ocio_available=True)
        assert node.metadata.get("_color_mode") is None


# ---------------------------------------------------------------------------
# Initial visibility — reload (metadata present)
# ---------------------------------------------------------------------------


class TestReloadVisibility:
    def test_reload_with_saved_basic_uses_basic_even_when_ocio_available(self) -> None:
        """The reload fix: metadata overrides _default_mode for initial visibility."""
        with patch(f"{_NODE_MODULE}.apply_color_mode_visibility") as mock_vis:
            _make_node(ocio_available=True, metadata={"_color_mode": COLOR_MODE_BASIC})

        mock_vis.assert_called_once()
        assert mock_vis.call_args[0][1] is False

    def test_reload_with_saved_ocio_uses_ocio_even_when_ocio_unavailable(self) -> None:
        """Saved 'ocio' mode restores OCIO visibility even if library is now absent."""
        with patch(f"{_NODE_MODULE}.apply_color_mode_visibility") as mock_vis:
            _make_node(ocio_available=False, metadata={"_color_mode": COLOR_MODE_OCIO})

        mock_vis.assert_called_once()
        assert mock_vis.call_args[0][1] is True


# ---------------------------------------------------------------------------
# after_value_set — metadata persistence
# ---------------------------------------------------------------------------


class TestAfterValueSetMetadata:
    def test_switching_to_basic_persists_metadata(self) -> None:
        node = _make_node()
        node.after_value_set(node._color_mode_param, COLOR_MODE_BASIC)
        assert node.metadata["_color_mode"] == COLOR_MODE_BASIC

    def test_switching_to_ocio_persists_metadata(self) -> None:
        node = _make_node()
        node.after_value_set(node._color_mode_param, COLOR_MODE_OCIO)
        assert node.metadata["_color_mode"] == COLOR_MODE_OCIO

    def test_other_parameter_does_not_write_metadata(self) -> None:
        node = _make_node()
        node.after_value_set(node._exposure_param, 2.0)
        assert "_color_mode" not in node.metadata


# ---------------------------------------------------------------------------
# after_value_set — visibility dispatch
# ---------------------------------------------------------------------------


class TestAfterValueSetVisibility:
    def test_switching_to_ocio_calls_visibility_with_true(self) -> None:
        node = _make_node()
        with patch(f"{_NODE_MODULE}.apply_color_mode_visibility") as mock_vis:
            node.after_value_set(node._color_mode_param, COLOR_MODE_OCIO)

        mock_vis.assert_called_once_with(
            node,
            True,
            node._tone_mapping_param.name,
            node._color_params_param.name,
        )

    def test_switching_to_basic_calls_visibility_with_false(self) -> None:
        node = _make_node()
        with patch(f"{_NODE_MODULE}.apply_color_mode_visibility") as mock_vis:
            node.after_value_set(node._color_mode_param, COLOR_MODE_BASIC)

        mock_vis.assert_called_once_with(
            node,
            False,
            node._tone_mapping_param.name,
            node._color_params_param.name,
        )

    def test_other_parameter_does_not_call_visibility(self) -> None:
        node = _make_node()
        with patch(f"{_NODE_MODULE}.apply_color_mode_visibility") as mock_vis:
            node.after_value_set(node._exposure_param, 1.0)

        mock_vis.assert_not_called()
