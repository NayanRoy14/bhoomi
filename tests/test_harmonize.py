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
    """Pixels including dark targets -- floor well below 800 DN, offset absent."""
    return np.concatenate([np.full(n // 10, 200.0), np.full(n - n // 10, 1800.0)])


def bright_scene(n=50_000):
    """No pixels below 800 DN. Consistent with the offset, and with bare desert."""
    return np.concatenate([np.full(n // 10, 1200.0), np.full(n - n // 10, 2800.0)])


class TestFloorMeasurement:
    def test_a_low_floor_is_conclusive(self):
        assert harmonize.measure_offset_floor(dark_scene()).conclusive is True

    def test_a_high_floor_is_not_conclusive(self):
        """The test is one-sided: a high floor proves nothing either way."""
        assert harmonize.measure_offset_floor(bright_scene()).conclusive is False

    def test_nodata_is_excluded(self):
        """Zeros are nodata; counting them would floor every scene at 0."""
        data = np.concatenate([np.zeros(20_000), bright_scene()])
        assert harmonize.measure_offset_floor(data).conclusive is False

    def test_too_few_pixels_raises(self):
        """Better to fail than to judge the convention from a handful of pixels."""
        with pytest.raises(HarmonizationError, match="need 10,000"):
            harmonize.measure_offset_floor(np.full(500, 1800.0))

    def test_a_handful_of_outliers_cannot_flip_the_floor(self):
        """The real defeat of a min()-based rule, reproduced without network.

        `S2A_44PMV_20220204_0_L2A` carries the offset and holds exactly 2 valid
        pixels out of 7,535,025 below 800 DN. Reading the minimum classifies it
        offset-absent -- and subtracting nothing shifts its NDVI by 0.13. The
        0.1st percentile ignores them.
        """
        n = 1_000_000
        data = np.concatenate([np.full(2, 795.0), np.full(n - 2, 1100.0)])
        assert data.min() < harmonize.FLOOR_DN          # a min() rule is fooled
        assert harmonize.measure_offset_floor(data).conclusive is False

    def test_averaging_costs_conclusiveness_but_never_correctness(self):
        """How this statistic degrades under decimation, and why that is safe.

        Averaging raises the floor -- measured on real tiles at decimation 4, 8,
        16, 32 (2026-07-31): Kolkata 2019-04-30 goes 698 -> 764 -> 800 -> 846,
        Delhi 2022-04-19 goes 648 -> 798 -> 1031 -> 1299. Both cross the 800 DN
        line and stop being conclusive.

        That is a real cost, and it is why DEFAULT_DECIMATION is 4. But it is
        not the old failure: the floor only ever moves *up*, and the pixel
        test's only positive claim is "absent", so a coarser look loses a
        verdict rather than inverting one. The dark-fraction rule it replaced
        flipped scenes to the opposite answer.
        """
        n = 400_000
        dark = int(n * 0.025)
        full = np.concatenate([np.full(dark, 250.0), np.full(n - dark, 2400.0)])
        fine = harmonize.measure_offset_floor(full)
        assert fine.conclusive is True

        # Worst case: dark pixels scattered so no block is wholly dark. Real
        # water is contiguous and degrades far more gently than this.
        blocks = full.copy()
        np.random.default_rng(0).shuffle(blocks)
        coarse = harmonize.measure_offset_floor(blocks.reshape(-1, 16).mean(axis=1))

        assert coarse.floor_dn > fine.floor_dn, "averaging must not lower the floor"
        assert coarse.conclusive is False  # conclusiveness lost, as expected
        # The verdict is never inverted: an inconclusive floor cannot assert
        # "present" on its own -- only metadata can, and it is warned about.
        assert harmonize.resolve_offset(coarse, PRE_OFFSET).present is False


class TestResolveOffset:
    """Pixels first, metadata only where pixels are silent."""

    def test_pixels_override_metadata_claiming_present(self):
        """Kolkata 2022-03-20, Delhi 2022-04-19, Sundarbans 2022-03-22.

        All three carry `boa_offset_applied: False` on a post-04.00 baseline --
        metadata implying the offset is in the pixels -- while their floors sit
        at 240, 648 and 96 DN, which an offset-bearing product cannot reach.
        """
        evidence = harmonize.measure_offset_floor(dark_scene())
        decision = harmonize.resolve_offset(evidence, RAW_OFFSET)
        assert decision.present is False
        assert decision.basis == "pixels"
        assert decision.warning is None

    def test_overruling_metadata_is_logged(self, caplog):
        evidence = harmonize.measure_offset_floor(dark_scene())
        with caplog.at_level("WARNING"):
            harmonize.resolve_offset(evidence, RAW_OFFSET)
        assert "Trusting the pixels" in caplog.text

    def test_a_pre_offset_baseline_settles_it_without_pixels(self):
        """The convention did not exist before 04.00, so nothing to detect."""
        decision = harmonize.resolve_offset(None, PRE_OFFSET)
        assert decision.present is False
        assert decision.basis == "baseline"

    def test_an_inconclusive_floor_falls_back_to_metadata(self):
        """The bright-arid case: Thar, Kutch, Delhi. No dark target exists."""
        evidence = harmonize.measure_offset_floor(bright_scene())
        decision = harmonize.resolve_offset(evidence, RAW_OFFSET)
        assert decision.present is True
        assert decision.basis == "metadata"

    def test_the_fallback_carries_a_warning(self):
        """Uncertainty must travel with the result, not stop at the log."""
        evidence = harmonize.measure_offset_floor(bright_scene())
        decision = harmonize.resolve_offset(evidence, RAW_OFFSET)
        assert decision.warning is not None
        assert "could not be determined from pixels" in decision.warning

    def test_corrected_metadata_with_an_inconclusive_floor_means_absent(self):
        evidence = harmonize.measure_offset_floor(bright_scene())
        decision = harmonize.resolve_offset(evidence, CORRECTED)
        assert decision.present is False
        assert decision.basis == "metadata"

    def test_no_pixels_and_no_metadata_raises(self):
        """Guessing here is worse than failing -- the error stays invisible."""
        with pytest.raises(HarmonizationError, match="Refusing to guess"):
            harmonize.resolve_offset(None, {"s2:processing_baseline": "05.12"})

    def test_an_inconclusive_floor_and_no_metadata_raises(self):
        evidence = harmonize.measure_offset_floor(bright_scene())
        with pytest.raises(HarmonizationError, match="too high to be conclusive"):
            harmonize.resolve_offset(evidence, {"s2:processing_baseline": "05.12"})


class TestOneSidedness:
    """The property the 48-scene sample forced, and the reason for it.

    Offset-absent scenes measured floors of 922, 942, 983, 1094, 1464, 1777,
    1814, 1938, 2045 and 2048 DN. The one offset-present scene measured 1003 --
    inside that range. No threshold on a high floor separates the classes, so
    the pixel test must never claim "present".
    """

    ABSENT_FLOORS_ABOVE_THRESHOLD = [922, 942, 983, 1094, 1464, 1777, 1814,
                                     1938, 2045, 2048]
    PRESENT_FLOOR = 1003

    def test_the_present_scene_is_bracketed_by_absent_ones(self):
        below = [f for f in self.ABSENT_FLOORS_ABOVE_THRESHOLD if f < self.PRESENT_FLOOR]
        above = [f for f in self.ABSENT_FLOORS_ABOVE_THRESHOLD if f > self.PRESENT_FLOOR]
        assert below and above, (
            "if this ever separates, the pixel test could claim 'present' -- "
            "re-derive the rule rather than assuming it still cannot")

    def test_no_measured_absent_scene_is_ever_called_present(self):
        """The whole guarantee: a conclusive floor is never wrong."""
        for floor in (1, 20, 96, 138, 240, 357, 648, 698):
            evidence = harmonize.OffsetEvidence(floor_dn=float(floor), sample_pixels=10**6)
            assert harmonize.resolve_offset(evidence, RAW_OFFSET).present is False

    def test_the_threshold_leaves_room_below_it(self):
        """698 DN is the highest conclusive absent floor measured; 800 is the line."""
        assert harmonize.FLOOR_DN - 698 >= 100


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
        with pytest.raises(HarmonizationError, match="resolve_offset"):
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
