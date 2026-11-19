"""Transfer functions own the read-only sRGB code-value lookup table."""

from dataclasses import FrozenInstanceError, asdict
from unittest.mock import patch

import numpy as np
import pytest

from griptape_nodes_openexr.exr.colour import icc_profiles, transfer_functions


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        (
            "SRGB",
            {
                "linear_cutoff": 0.0031308,
                "toe_inclusive": True,
                "toe_slope": 12.92,
                "exponent": 1.0 / 2.4,
                "gain": 1.055,
                "offset": 0.055,
            },
        ),
        (
            "REC709",
            {
                "linear_cutoff": 0.018,
                "toe_inclusive": False,
                "toe_slope": 4.5,
                "exponent": 0.45,
                "gain": 1.099,
                "offset": 0.099,
            },
        ),
    ],
)
def test_curve_definitions_are_complete_and_frozen(name: str, expected: dict) -> None:
    curve = getattr(transfer_functions, name)
    assert isinstance(curve, transfer_functions.PiecewiseTransferCurve)
    assert asdict(curve) == expected
    with pytest.raises(FrozenInstanceError):
        curve.gain = 0.0  # type: ignore[misc]  # exercise the frozen dataclass's runtime guard


@pytest.mark.parametrize(("name", "function"), [("SRGB", "linear_to_srgb"), ("REC709", "linear_to_rec709")])
def test_encoder_receives_the_curve_definition(name: str, function: str) -> None:
    source = np.array([0.0, 0.18, 1.0], dtype=np.float32)
    with patch.object(transfer_functions, "_encode_piecewise", wraps=transfer_functions._encode_piecewise) as encode:
        getattr(transfer_functions, function)(source)
    assert encode.call_args.kwargs == {"curve": getattr(transfer_functions, name)}
    assert encode.call_args.args[0] is source


@pytest.mark.parametrize(("name", "encoding"), [("SRGB", "srgb"), ("REC709", "rec709")])
def test_icc_sampling_receives_the_same_curve_definition(name: str, encoding: icc_profiles.TransferCurve) -> None:
    with patch.object(icc_profiles, "_sampled_curve", wraps=icc_profiles._sampled_curve) as sample:
        icc_profiles.build_icc_profile("curve test", encoding)
    sample.assert_called_once_with(getattr(transfer_functions, name))


def test_lookup_table_is_derived_from_the_srgb_decode_function() -> None:
    codes = np.arange(256, dtype=np.uint8)
    expected = transfer_functions.srgb_to_linear(codes.astype(np.float32) / np.float32(255.0))
    table = transfer_functions._SRGB_UINT8_TO_LINEAR
    assert table.dtype == np.float32
    assert table.shape == (256,)
    assert table.flags.writeable is False
    np.testing.assert_array_equal(table, expected)
    np.testing.assert_array_equal(transfer_functions.srgb_uint8_to_linear(codes), expected)
