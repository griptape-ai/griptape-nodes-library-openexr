"""Transfer functions own the read-only sRGB code-value lookup table."""

import numpy as np

from griptape_nodes_openexr.exr.colour import transfer_functions


def test_lookup_table_is_derived_from_the_srgb_decode_function() -> None:
    codes = np.arange(256, dtype=np.uint8)
    expected = transfer_functions.srgb_to_linear(codes.astype(np.float32) / np.float32(255.0))
    table = transfer_functions._SRGB_UINT8_TO_LINEAR
    assert table.dtype == np.float32
    assert table.shape == (256,)
    assert table.flags.writeable is False
    np.testing.assert_array_equal(table, expected)
    np.testing.assert_array_equal(transfer_functions.srgb_uint8_to_linear(codes), expected)
