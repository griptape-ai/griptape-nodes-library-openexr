"""Unit tests for ICC profile construction.

Tests that convert colour use PIL.ImageCms (littlecms) as an independent
oracle: it confirms our hand-built bytes are a profile that a real colour
management system parses and applies correctly. Checking our profiles with
our own arithmetic would prove nothing. Pillow can be built without
littlecms, hence the skip guard.
"""

from __future__ import annotations

import struct
from io import BytesIO

import numpy as np
import pytest
from PIL import Image

from griptape_nodes_openexr.exr.colour.icc_profiles import (
    PROFILE_DESCRIPTIONS,
    build_icc_profile,
    profile_for_tone_mapping,
)
from griptape_nodes_openexr.exr.colour.tone_mapping import (
    TONE_CHOICES,
    apply_tone_mapping,
    to_uint8,
)
from griptape_nodes_openexr.exr.colour.transfer_functions import (
    REC709,
)

ImageCms = pytest.importorskip("PIL.ImageCms", reason="Pillow built without littlecms")

_ICC_HEADER_SIZE = 128


def _srgb_reference():
    """Pillow's own sRGB profile, to convert into."""
    return ImageCms.getOpenProfile(BytesIO(ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes()))


def _convert_to_srgb(uint8_rgb: np.ndarray, profile: bytes) -> np.ndarray:
    """Tag pixels with `profile` and convert them into sRGB, as a viewer would."""
    # Tile to 8x8: profileToProfile works on images, not single pixels.
    tiled = np.repeat(np.repeat(uint8_rgb, 8, axis=0), 8, axis=1)
    img = Image.fromarray(tiled, mode="RGB")
    src = ImageCms.getOpenProfile(BytesIO(profile))
    converted = ImageCms.profileToProfile(img, src, _srgb_reference(), outputMode="RGB")
    return np.array(converted)[0, 0]


def _render(scene: list[float], tone: str) -> np.ndarray:
    arr = np.array([[scene]], dtype=np.float32)
    return to_uint8(apply_tone_mapping(arr, tone))


class TestProfileStructure:
    """Checks that need no colour management, only the byte layout."""

    @pytest.mark.parametrize("tone", TONE_CHOICES)
    def test_every_choice_has_a_profile(self, tone: str) -> None:
        # A new display transform without a profile would otherwise silently
        # write an untagged file.
        assert profile_for_tone_mapping(tone) is not None

    @pytest.mark.parametrize("tone", TONE_CHOICES)
    def test_declared_size_matches_actual(self, tone: str) -> None:
        blob = profile_for_tone_mapping(tone)
        assert blob is not None
        assert struct.unpack(">I", blob[:4])[0] == len(blob)

    @pytest.mark.parametrize("tone", TONE_CHOICES)
    def test_header_signature(self, tone: str) -> None:
        blob = profile_for_tone_mapping(tone)
        assert blob is not None
        assert blob[36:40] == b"acsp"
        assert len(blob) > _ICC_HEADER_SIZE

    @pytest.mark.parametrize("tone", TONE_CHOICES)
    def test_desc_tag_size_matches_spec(self, tone: str) -> None:
        # ICC v2 textDescriptionType: signature, reserved, ASCII count and string, then
        # 78 bytes of empty Unicode and ScriptCode fields.
        blob = profile_for_tone_mapping(tone)
        assert blob is not None
        (count,) = struct.unpack(">I", blob[_ICC_HEADER_SIZE : _ICC_HEADER_SIZE + 4])
        for i in range(count):
            entry = _ICC_HEADER_SIZE + 4 + i * 12
            signature, offset, size = struct.unpack(">4sII", blob[entry : entry + 12])
            if signature == b"desc":
                (ascii_count,) = struct.unpack(">I", blob[offset + 8 : offset + 12])
                assert size == 12 + ascii_count + 78
                return
        pytest.fail("no desc tag")

    @pytest.mark.parametrize("tone", TONE_CHOICES)
    def test_copyright_tag_names_rights_holder_and_terms(self, tone: str) -> None:
        # ICC v2 textType: signature, reserved, then a null-terminated ASCII string.
        blob = profile_for_tone_mapping(tone)
        assert blob is not None
        (count,) = struct.unpack(">I", blob[_ICC_HEADER_SIZE : _ICC_HEADER_SIZE + 4])
        for i in range(count):
            entry = _ICC_HEADER_SIZE + 4 + i * 12
            signature, offset, size = struct.unpack(">4sII", blob[entry : entry + 12])
            if signature == b"cprt":
                assert blob[offset : offset + 4] == b"text"
                text = blob[offset + 8 : offset + size].rstrip(b"\0").decode("ascii")
                assert text == (
                    "Copyright 2026 The Foundry Visionmongers Ltd. This profile may be copied, "
                    "distributed, embedded, made, used and sold without restriction. Altered "
                    "versions must remove the original identification and copyright information "
                    "and must not be misrepresented as the original profile."
                )
                return
        pytest.fail("no cprt tag")

    def test_unknown_tone_returns_none(self) -> None:
        assert profile_for_tone_mapping("reinhard") is None

    def test_lookup_is_case_insensitive(self) -> None:
        assert profile_for_tone_mapping("SRGB") == profile_for_tone_mapping("sRGB")

    def test_profiles_are_stable_across_calls(self) -> None:
        # Built once at import; a per-call rebuild would be wasted work on
        # every frame of a sequence.
        assert profile_for_tone_mapping("rec709") is profile_for_tone_mapping("rec709")

    def test_distinct_curves_give_distinct_profiles(self) -> None:
        assert profile_for_tone_mapping("rec709") != profile_for_tone_mapping("rec1886")

    @pytest.mark.parametrize(
        ("tone", "base"),
        [
            ("ACEScg to sRGB", "Linear sRGB to sRGB"),
            ("ACEScg to rec709", "Linear sRGB to rec709"),
            ("ACES2065-1 to sRGB", "Linear sRGB to sRGB"),
        ],
    )
    def test_aces_choice_shares_its_base_profile(self, tone: str, base: str) -> None:
        # The output is Rec.709 primaries with the base curve, which is what that profile says.
        assert profile_for_tone_mapping(tone) is profile_for_tone_mapping(base)
        assert PROFILE_DESCRIPTIONS[tone.lower()] == PROFILE_DESCRIPTIONS[base.lower()]

    @pytest.mark.parametrize(
        ("alias", "current"),
        [("srgb", "linear srgb to srgb"), ("rec709", "linear srgb to rec709"), ("rec1886", "linear srgb to rec1886")],
    )
    def test_earlier_alias_keys_remain_in_profile_descriptions(self, alias: str, current: str) -> None:
        assert PROFILE_DESCRIPTIONS[alias] == PROFILE_DESCRIPTIONS[current]

    def test_wtpt_records_d65_with_bradford_chad_and_exact_d50_primary_sum(self) -> None:
        blob = profile_for_tone_mapping("Linear sRGB to sRGB")
        assert blob is not None
        pcs_d50 = struct.unpack(">iii", blob[68:80])
        (count,) = struct.unpack(">I", blob[_ICC_HEADER_SIZE : _ICC_HEADER_SIZE + 4])
        tags: dict[bytes, tuple[int, int]] = {}
        for i in range(count):
            entry = _ICC_HEADER_SIZE + 4 + i * 12
            sig, off, sz = struct.unpack(">4sII", blob[entry : entry + 12])
            tags[sig] = (off, sz)

        # ICC v2 mediaWhitePointTag records the D65 display white point (~0.9505, 1.0, 1.0891).
        wtpt_off, _ = tags[b"wtpt"]
        wtpt = [v / 65536.0 for v in struct.unpack(">iii", blob[wtpt_off + 8 : wtpt_off + 20])]
        np.testing.assert_allclose(wtpt, [0.950455, 1.0, 1.089050], atol=1e-4)

        # chromaticAdaptationTag (sf32) adapts D65 media white into the D50 PCS illuminant.
        assert b"chad" in tags
        chad_off, chad_sz = tags[b"chad"]
        assert chad_sz == 44
        assert blob[chad_off : chad_off + 4] == b"sf32"
        chad = (
            np.array(struct.unpack(">9i", blob[chad_off + 8 : chad_off + 44]), dtype=np.float64).reshape(3, 3) / 65536.0
        )
        np.testing.assert_allclose(chad @ np.array(wtpt), np.array(pcs_d50) / 65536.0, atol=1e-4)

        # D50-adapted rXYZ + gXYZ + bXYZ fixed-point columns sum identically to the header PCS D50 XYZ.
        prim_fixed = [
            struct.unpack(">iii", blob[tags[sig][0] + 8 : tags[sig][0] + 20]) for sig in (b"rXYZ", b"gXYZ", b"bXYZ")
        ]
        col_sums = tuple(sum(col[i] for col in prim_fixed) for i in range(3))
        assert col_sums == pcs_d50


class TestProfileParsing:
    @pytest.mark.parametrize("tone", TONE_CHOICES)
    def test_littlecms_accepts_profile(self, tone: str) -> None:
        blob = profile_for_tone_mapping(tone)
        assert blob is not None
        profile = ImageCms.getOpenProfile(BytesIO(blob))
        assert ImageCms.getProfileDescription(profile).strip() == PROFILE_DESCRIPTIONS[tone.lower()]

    def test_build_rejects_unknown_curve(self) -> None:
        with pytest.raises(ValueError, match="curve"):
            build_icc_profile("Bad", "not-a-curve")  # type: ignore[arg-type]


class TestColourRoundTrip:
    """The property that justifies the feature.

    A tagged image converted to sRGB must reproduce the direct sRGB render.
    If it does, the file is correctly labelled and any colour-managed
    application will display it the same way.
    """

    SCENE = [0.05, 0.18, 0.5]

    @pytest.mark.parametrize("tone", ["rec709", "rec1886"])
    def test_tagged_render_converts_to_srgb_render(self, tone: str) -> None:
        # Within 1 LSB rather than exact: the render is already quantized to
        # 8 bits, so decoding and re-encoding it cannot land on the same
        # integer every time. rec709 stores scene 0.05 as 0.0507, which
        # re-encodes 0.4 LSB from the direct sRGB value and rounds the other
        # way. A real curve error would be tens of levels, not one.
        blob = profile_for_tone_mapping(tone)
        assert blob is not None
        converted = _convert_to_srgb(_render(self.SCENE, tone), blob)
        np.testing.assert_allclose(converted, _render(self.SCENE, "sRGB")[0, 0], atol=1)

    @pytest.mark.parametrize("tone", ["rec709", "rec1886"])
    def test_tagging_is_what_makes_them_match(self, tone: str) -> None:
        # Guards the tolerance above from hiding a broken profile: untagged,
        # these renders differ from sRGB by far more than 1 LSB.
        untagged = _render(self.SCENE, tone)[0, 0].astype(int)
        reference = _render(self.SCENE, "sRGB")[0, 0].astype(int)
        assert np.abs(untagged - reference).max() > 1

    def test_srgb_tagged_is_an_identity(self) -> None:
        blob = profile_for_tone_mapping("sRGB")
        assert blob is not None
        rendered = _render(self.SCENE, "sRGB")
        np.testing.assert_array_equal(_convert_to_srgb(rendered, blob), rendered[0, 0])

    def test_raw_tagged_linear_converts_to_srgb(self) -> None:
        # raw writes the scene value itself, so a linear profile must be what
        # tells a viewer to apply the sRGB encode: 0.5 -> 128 -> 188.
        blob = profile_for_tone_mapping("raw")
        assert blob is not None
        converted = _convert_to_srgb(np.array([[[128, 128, 128]]], dtype=np.uint8), blob)
        np.testing.assert_allclose(converted, [188, 188, 188], atol=1)

    # Scene values under the rec709 breakpoint of 0.018, which encode into the
    # curve's linear toe. SCENE above sits entirely on the power segment, so
    # without these the toe parameters of the parametric tag go unexercised.
    SHADOW_SCENE = [0.002, 0.008, 0.016]

    def test_tagged_shadows_convert_to_srgb_render(self) -> None:
        # The 'd' breakpoint is the parameter most easily got wrong: ICC wants
        # it in the encoded domain while tone_mapping states it as a linear
        # value. Stating d as the linear cutoff instead passes every other
        # test here and still shifts code 5 to 20 against a correct 14.
        blob = profile_for_tone_mapping("rec709")
        assert blob is not None
        converted = _convert_to_srgb(_render(self.SHADOW_SCENE, "rec709"), blob)
        np.testing.assert_allclose(converted, _render(self.SHADOW_SCENE, "sRGB")[0, 0], atol=1)

    def test_shadow_renders_straddle_the_breakpoint(self) -> None:
        # Guards the test above: it only exercises the toe while these values
        # really do encode below the breakpoint.
        encoded = _render(self.SHADOW_SCENE, "rec709")[0, 0]
        breakpoint_code = _render([REC709.linear_cutoff] * 3, "rec709")[0, 0, 0]
        assert encoded.max() < breakpoint_code

    def test_untagged_and_tagged_pixels_are_identical(self) -> None:
        # The profile describes the pixels; it must never alter them.
        rendered = _render(self.SCENE, "rec1886")
        blob = profile_for_tone_mapping("rec1886")
        buf = BytesIO()
        Image.fromarray(rendered, mode="RGB").save(buf, format="PNG", icc_profile=blob)
        reloaded = np.array(Image.open(BytesIO(buf.getvalue())))
        np.testing.assert_array_equal(reloaded, rendered)

    def test_acescg_curves_match_when_tagged_while_different_source_primaries_differ(self) -> None:
        coloured = [0.5, 0.2, 0.1]
        rec709_blob = profile_for_tone_mapping("ACEScg to rec709")
        srgb_blob = profile_for_tone_mapping("ACEScg to sRGB")
        assert rec709_blob is not None
        assert srgb_blob is not None
        aces_from_rec709 = _convert_to_srgb(_render(coloured, "ACEScg to rec709"), rec709_blob)
        aces_srgb = _convert_to_srgb(_render(coloured, "ACEScg to sRGB"), srgb_blob)
        lin_srgb = _convert_to_srgb(_render(coloured, "Linear sRGB to sRGB"), srgb_blob)
        np.testing.assert_allclose(aces_from_rec709, aces_srgb, atol=1)
        assert np.abs(aces_srgb.astype(int) - lin_srgb.astype(int)).max() > 10
