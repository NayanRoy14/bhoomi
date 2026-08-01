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

    def test_opposite_seasons_warn_about_phenology(self):
        _, warnings = change.difference(
            np.zeros((2, 2), np.float32), np.zeros((2, 2), np.float32),
            BASE_05, dict(BASE_05, datetime="2026-09-04T04:46:54Z"))
        assert any("apart in the year" in w for w in warnings)

    def test_demo_pair_is_compatible(self):
        """2020-03-10 vs 2026-03-04: six days apart in day-of-year (D11)."""
        warnings = change.check_scene_compatibility(BASE_05, BASE_0512)
        assert not any("apart in the year" in w for w in warnings)


class TestChangeStats:
    def test_asymmetry_measures_one_sidedness(self):
        """How lopsided the change is -- not a noise test. See ChangeStats."""
        diff = np.concatenate([np.full(30, -0.5), np.full(10, 0.5), np.zeros(60)])
        stats = change.change_stats(diff.astype(np.float32))
        assert stats.loss_fraction == pytest.approx(0.30)
        assert stats.gain_fraction == pytest.approx(0.10)
        assert stats.asymmetry == pytest.approx(3.0)

    def test_a_symmetric_distribution_gives_asymmetry_near_one(self):
        """Equal movement up and down. That is what the number reports; whether
        such a distribution is noise or two balanced real signals is not
        something the ratio can tell you (PLAN.md 5.4.4, narrowed 2026-07-31)."""
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

    def test_the_antimeridian_stays_in_utm(self):
        """180 E computed zone 61 -> EPSG:32661, which is not an error code.

        32661 is WGS 84 / UPS North, a polar stereographic CRS -- so a point on
        the antimeridian silently got a polar projection, and every area and
        grid derived from it would have been wrong with nothing raised. Both
        ends of the meridian belong to zone 60.
        """
        assert utm_crs_for(180.0, 22.5).to_epsg() == 32660
        assert utm_crs_for(-180.0, 22.5).to_epsg() == 32601
        assert utm_crs_for(180.0, -22.5).to_epsg() == 32760

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

class TestSeasonalSeparation:
    """PLAN.md 5.4.4's confound, measured in day-of-year rather than by month.

    The month name is a crude proxy that is wrong both ways: 27 Feb and 10 Mar
    are eleven days apart and used to warn, while 1 Mar and 31 Mar are thirty
    days apart and did not. D11 justifies the demo pair as "six days apart in
    day-of-year", so that is what the check measures.
    """

    @staticmethod
    def _props(date, baseline="05.00"):
        return {"datetime": f"{date}T04:00:00Z", "s2:processing_baseline": baseline}

    def _seasonal(self, a, b):
        return [w for w in change.check_scene_compatibility(self._props(a), self._props(b))
                if "apart in the year" in w]

    def test_a_near_anniversary_does_not_warn(self):
        """The real case that exposed this: 11 days apart, across a month
        boundary, phenologically close."""
        assert self._seasonal("2020-03-10", "2026-02-27") == []

    def test_the_demo_pair_does_not_warn(self):
        """D11: six days apart in day-of-year."""
        assert self._seasonal("2020-03-10", "2026-03-04") == []

    def test_a_month_apart_within_one_month_name_does_warn(self):
        """1 Mar vs 31 Mar shares a month and is a whole month apart."""
        assert self._seasonal("2020-03-01", "2026-03-31") != []

    def test_opposite_seasons_warn(self):
        assert self._seasonal("2020-03-10", "2026-09-10") != []

    def test_the_year_wraps(self):
        """31 Dec and 1 Jan are one day apart, not 364."""
        assert self._seasonal("2020-12-31", "2026-01-01") == []

    def test_an_unparseable_date_does_not_invent_a_warning(self):
        assert self._seasonal("not-a-date", "2026-03-04") == []
