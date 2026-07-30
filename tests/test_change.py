"""Tests for two-date change detection and grid alignment."""

import numpy as np
import pytest

from processing import change, grid_for_aoi, raster_utils, utm_crs_for

BASE_05 = {"s2:processing_baseline": "05.00", "datetime": "2020-03-10T04:42:43Z"}
BASE_0512 = {"s2:processing_baseline": "05.12", "datetime": "2026-03-04T04:46:54Z"}
BASE_0214 = {"s2:processing_baseline": "02.14", "datetime": "2020-03-10T04:42:43Z"}


class TestDifference:
    def test_shape_mismatch_is_rejected(self):
        with pytest.raises(ValueError, match="same Grid"):
            change.difference(np.zeros((2, 2), np.float32), np.zeros((3, 3), np.float32))

    def test_mask_union_via_nan(self):
        """A pixel is valid only if valid on BOTH dates."""
        a = np.array([0.5, np.nan, 0.5], dtype=np.float32)
        b = np.array([0.3, 0.3, np.nan], dtype=np.float32)
        diff, _ = change.difference(a, b)
        assert diff[0] == pytest.approx(-0.2)
        assert np.isnan(diff[1]) and np.isnan(diff[2])

    def test_baseline_mismatch_warns(self):
        _, warnings = change.difference(
            np.zeros((2, 2), np.float32), np.zeros((2, 2), np.float32),
            BASE_05, BASE_0214)
        assert any("baselines differ" in w for w in warnings)

    def test_matching_baselines_do_not_warn(self):
        _, warnings = change.difference(
            np.zeros((2, 2), np.float32), np.zeros((2, 2), np.float32),
            BASE_05, dict(BASE_05))
        assert warnings == []

    def test_different_months_warn_about_seasonality(self):
        _, warnings = change.difference(
            np.zeros((2, 2), np.float32), np.zeros((2, 2), np.float32),
            BASE_05, dict(BASE_05, datetime="2026-09-04T04:46:54Z"))
        assert any("different months" in w for w in warnings)

    def test_demo_pair_is_compatible(self):
        """2020-03-10 vs 2026-03-04: same month, both baseline 05.x."""
        warnings = change.check_scene_compatibility(BASE_05, BASE_0512)
        assert not any("different months" in w for w in warnings)


class TestChangeStats:
    def test_asymmetry_detects_one_sided_loss(self):
        """Noise moves both ways; real loss is lopsided (PLAN.md 5.4.4)."""
        diff = np.concatenate([np.full(30, -0.5), np.full(10, 0.5), np.zeros(60)])
        stats = change.change_stats(diff.astype(np.float32))
        assert stats.loss_fraction == pytest.approx(0.30)
        assert stats.gain_fraction == pytest.approx(0.10)
        assert stats.asymmetry == pytest.approx(3.0)

    def test_symmetric_noise_gives_asymmetry_near_one(self):
        rng = np.random.default_rng(0)
        stats = change.change_stats(rng.normal(0, 0.3, 100_000).astype(np.float32))
        assert 0.9 < stats.asymmetry < 1.1

    def test_all_nan_is_handled(self):
        stats = change.change_stats(np.full(10, np.nan, dtype=np.float32))
        assert stats.valid_fraction == 0.0


class TestGrid:
    def test_kolkata_resolves_to_utm_45n(self):
        assert utm_crs_for(88.36, 22.57).to_epsg() == 32645

    def test_southern_hemisphere_uses_327xx(self):
        assert utm_crs_for(88.36, -22.57).to_epsg() == 32745

    def test_origin_is_snapped_to_resolution(self):
        grid = grid_for_aoi((88.35, 22.55, 88.52, 22.68), resolution=10.0)
        left, bottom, _, top = grid.bounds()
        assert left % 10 == 0 and top % 10 == 0
        assert grid.resolution == 10.0

    def test_demo_aoi_is_within_limits(self):
        """D13's AOI must fit under the pixel cap at 10 m."""
        grid = grid_for_aoi((88.35, 22.55, 88.52, 22.68), resolution=10.0)
        assert grid.pixel_count < raster_utils.MAX_PIXELS
        assert grid.crs.to_epsg() == 32645

    def test_oversized_aoi_is_rejected(self):
        with pytest.raises(raster_utils.GridTooLargeError, match="Reduce the AOI"):
            grid_for_aoi((80.0, 15.0, 90.0, 25.0), resolution=10.0)

    def test_same_aoi_gives_identical_grid(self):
        """Determinism is what makes two dates comparable."""
        aoi = (88.35, 22.55, 88.52, 22.68)
        assert grid_for_aoi(aoi, 10.0) == grid_for_aoi(aoi, 10.0)
