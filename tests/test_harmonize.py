"""Tests for the reflectance convention -- the highest-risk logic in Bhoomi.

A bug here produces confidently wrong output rather than a visible failure.
Three successive readings of the metadata turned out to be wrong; the settled
answer is that the metadata cannot be trusted and the pixels must be measured.
"""

import numpy as np
import pytest

from processing import HarmonizationError, harmonize, indices, reflectance_params, to_reflectance

SCALE = 1.0 / 10000.0

CORRECTED = {"earthsearch:boa_offset_applied": True, "s2:processing_baseline": "05.00"}
RAW_OFFSET = {"earthsearch:boa_offset_applied": False, "s2:processing_baseline": "04.00"}
PRE_OFFSET = {"earthsearch:boa_offset_applied": False, "s2:processing_baseline": "02.14"}
# The 2025 scene: metadata implies the offset is present, but it is not.
MISLEADING = {"earthsearch:boa_offset_applied": False, "s2:processing_baseline": "05.11"}


def dark_scene(n=50_000):
    """Pixels including dark targets -- offset absent."""
    return np.concatenate([np.full(n // 10, 200.0), np.full(n - n // 10, 1800.0)])


def bright_scene(n=50_000):
    """No pixels below 700 DN -- offset present."""
    return np.concatenate([np.full(n // 10, 1200.0), np.full(n - n // 10, 2800.0)])


class TestPixelDetector:
    def test_detects_offset_absent(self):
        assert harmonize.detect_offset_in_array(dark_scene()) is False

    def test_detects_offset_present(self):
        assert harmonize.detect_offset_in_array(bright_scene()) is True

    def test_nodata_is_excluded(self):
        data = np.concatenate([np.zeros(20_000), bright_scene()])
        assert harmonize.detect_offset_in_array(data) is True

    def test_too_few_pixels_raises(self):
        """Better to fail than to judge the convention from a handful of pixels."""
        with pytest.raises(HarmonizationError, match="need 10,000"):
            harmonize.detect_offset_in_array(np.full(500, 1800.0))


class TestReflectanceParams:
    def test_pixels_take_precedence_over_metadata(self):
        """The 2025 case: metadata says subtract, pixels say do not."""
        scale, offset = reflectance_params(MISLEADING, offset_present=False)
        assert (scale, offset) == pytest.approx((SCALE, 0.0))

    def test_disagreement_is_logged_not_silent(self, caplog):
        reflectance_params(MISLEADING, offset_present=False)
        assert "Metadata disagrees with pixels" in caplog.text

    def test_agreement_logs_nothing(self, caplog):
        reflectance_params(RAW_OFFSET, offset_present=True)
        assert "disagrees" not in caplog.text

    def test_offset_present_subtracts(self):
        scale, offset = reflectance_params(RAW_OFFSET, offset_present=True)
        assert (scale, offset) == pytest.approx((SCALE, -0.1))

    def test_metadata_fallback_warns(self, caplog):
        reflectance_params(CORRECTED)
        assert "observed to be wrong" in caplog.text

    def test_no_evidence_at_all_raises(self):
        with pytest.raises(HarmonizationError, match="detect_offset_in_scene"):
            reflectance_params({"s2:processing_baseline": "05.12"})

    def test_acquisition_date_is_never_consulted(self):
        """A 2020 acquisition can carry baseline 05.00 -- dates prove nothing."""
        props = dict(CORRECTED, datetime="2020-03-10T04:42:43Z")
        assert reflectance_params(props, False) == reflectance_params(CORRECTED, False)


class TestRoundTrip:
    """The regression test PLAN.md 5.3 requires."""

    def test_identical_reflectance_survives_both_encodings(self):
        nir_true, red_true = 0.40, 0.05

        nir_a = np.array([[nir_true * 10000]], dtype=np.float32)
        red_a = np.array([[red_true * 10000]], dtype=np.float32)
        nir_b = np.array([[nir_true * 10000 + 1000]], dtype=np.float32)
        red_b = np.array([[red_true * 10000 + 1000]], dtype=np.float32)

        a = indices.ndvi(to_reflectance(nir_a, CORRECTED, False),
                         to_reflectance(red_a, CORRECTED, False))
        b = indices.ndvi(to_reflectance(nir_b, RAW_OFFSET, True),
                         to_reflectance(red_b, RAW_OFFSET, True))

        expected = (nir_true - red_true) / (nir_true + red_true)
        assert a.item() == pytest.approx(expected, abs=1e-6)
        assert b.item() == pytest.approx(expected, abs=1e-6)

    def test_mishandling_the_offset_changes_the_answer(self):
        """Documents the bug: the error is large and looks plausible."""
        nir = np.full((100, 100), 5000, dtype=np.float32)
        red = np.full((100, 100), 1500, dtype=np.float32)

        correct = indices.ndvi(to_reflectance(nir, RAW_OFFSET, True),
                               to_reflectance(red, RAW_OFFSET, True))
        naive = indices.ndvi(to_reflectance(nir, RAW_OFFSET, False),
                             to_reflectance(red, RAW_OFFSET, False))

        assert correct.mean() == pytest.approx(0.7778, abs=1e-3)
        assert naive.mean() == pytest.approx(0.5385, abs=1e-3)
        # Both sit inside [-1, 1] -- nothing crashes, which is what makes it dangerous.
        assert -1.0 <= naive.mean() <= 1.0

    def test_subtracting_a_missing_offset_now_raises(self):
        """The 2025 failure: it must not produce a plausible number any more."""
        nir = np.full((100, 100), 1800, dtype=np.float32)
        red = np.full((100, 100), 850, dtype=np.float32)
        with pytest.raises(indices.ImplausibleIndexError):
            indices.ndvi(to_reflectance(nir, MISLEADING, True),
                         to_reflectance(red, MISLEADING, True))
