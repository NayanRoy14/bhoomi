"""Tests for index arithmetic and its guards."""

import numpy as np
import pytest

from processing import indices, masking


class TestNDVI:
    def test_known_value(self):
        nir = np.array([[0.40]], dtype=np.float32)
        red = np.array([[0.05]], dtype=np.float32)
        assert indices.ndvi(nir, red).item() == pytest.approx(0.35 / 0.45, abs=1e-6)

    def test_vegetation_high_water_negative(self):
        nir = np.array([0.45, 0.02], dtype=np.float32)
        red = np.array([0.04, 0.06], dtype=np.float32)
        result = indices.ndvi(nir, red)
        assert result[0] > 0.6
        assert result[1] < 0.0

    def test_zero_denominator_is_nan_not_inf(self):
        result = indices.ndvi(np.array([0.0], dtype=np.float32),
                              np.array([0.0], dtype=np.float32))
        assert np.isnan(result).all()
        assert not np.isinf(result).any()

    def test_nan_propagates_through(self):
        """Masked pixels must stay masked."""
        nir = np.array([0.4, np.nan], dtype=np.float32)
        red = np.array([0.05, 0.05], dtype=np.float32)
        result = indices.ndvi(nir, red)
        assert np.isfinite(result[0])
        assert np.isnan(result[1])

    def test_small_excursion_is_clamped_and_logged(self, caplog):
        """A few odd pixels are dark water, not a bug: clamp and record."""
        nir = np.concatenate([np.full(999, 0.4), [0.4]]).astype(np.float32)
        red = np.concatenate([np.full(999, 0.05), [-0.5]]).astype(np.float32)
        result = indices.ndvi(nir, red)
        assert np.all((result >= -1.0) & (result <= 1.0))
        assert "outside [-1, 1]" in caplog.text

    def test_large_excursion_raises(self):
        """The 2025 failure mode: a mis-applied offset must not pass silently.

        A log line was not enough -- the calling script muted it and a median
        NDVI of 1.000 reached the analysis.
        """
        nir = np.full(1000, 0.08, dtype=np.float32)
        red = np.full(1000, -0.02, dtype=np.float32)  # offset wrongly subtracted
        with pytest.raises(indices.ImplausibleIndexError, match="mis-harmonized"):
            indices.ndvi(nir, red)

    def test_check_can_be_disabled_for_diagnostics(self):
        nir = np.full(10, 0.08, dtype=np.float32)
        red = np.full(10, -0.02, dtype=np.float32)
        out = indices.normalized_difference(nir, red, max_invalid_fraction=None)
        assert np.all(out <= 1.0)

    def test_dtype_is_float32(self):
        result = indices.ndvi(np.array([0.4]), np.array([0.05]))
        assert result.dtype == np.float32


class TestOtherIndices:
    def test_ndwi_positive_over_water(self):
        # Water reflects green more than NIR.
        assert indices.ndwi(np.array([0.08], dtype=np.float32),
                            np.array([0.02], dtype=np.float32)).item() > 0

    def test_ndbi_positive_over_builtup(self):
        # Built surfaces reflect SWIR more than NIR.
        assert indices.ndbi(np.array([0.30], dtype=np.float32),
                            np.array([0.22], dtype=np.float32)).item() > 0

    def test_band_requirements_declared(self):
        assert indices.INDEX_BANDS["ndvi"] == (("nir", "red"), 10.0)
        # NDBI is 20 m because B11 is natively 20 m (PLAN.md D4).
        assert indices.INDEX_BANDS["ndbi"][1] == 20.0


class TestMasking:
    def test_default_masks_cloud_and_shadow(self):
        scl = np.array([4, 5, 6, 8, 9, 3, 10], dtype=np.uint8)
        invalid = masking.scl_mask(scl)
        assert list(invalid) == [False, False, False, True, True, True, True]

    def test_snow_can_be_kept(self):
        scl = np.array([11], dtype=np.uint8)
        assert masking.scl_mask(scl).item() is True
        assert masking.scl_mask(scl, mask_snow=False).item() is False

    def test_apply_mask_sets_nan_without_mutating_input(self):
        data = np.array([1.0, 2.0], dtype=np.float32)
        out = masking.apply_mask(data, np.array([False, True]))
        assert np.isnan(out[1]) and out[0] == 1.0
        assert data[1] == 2.0

    def test_valid_fraction(self):
        assert masking.valid_fraction(np.array([False, False, True, False])) == 0.75
