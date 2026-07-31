"""Two-date change detection (PLAN.md 5.4.4), without network or containers.

Same approach as test_index_process: real GeoTIFFs on disk, a Scene pointing at
them, nothing mocked but the offset decision. Two dates instead of one.

The cases worth having are the ones 5.4.4 and 5.3 argue about -- that identical
pixels difference to zero, that the sign follows time rather than argument
order, that the loss/gain asymmetry is reported beside the mean, and that a
processing-baseline mismatch is flagged rather than silently differenced.
"""

from __future__ import annotations

import dataclasses
import json
import uuid
from datetime import datetime, timezone

import pytest
import rasterio

import cache
import pipeline
from backend import storage
from backend.db.jobs import Job, JobStatus
from backend.queue import processes
from processing.raster_utils import grid_for_aoi
from tests.test_index_process import AOI, NIR_DN, RED_DN, Recorder, _write, scene  # noqa: F401

BBOX = (88.40, 22.60, 88.42, 22.62)
LATER_ID = "S2B_45QXF_20260304_0_L2A"
EARLIER_ID = "S2B_45QXF_20200310_0_L2A"


def earlier_scene(base, tmp_path, *, nir=NIR_DN, red=RED_DN, baseline="05.12",
                  extra_assets=None):
    """A second date on the same grid, with band files of its own."""
    grid = grid_for_aoi(BBOX, 10.0)
    assets = {
        "nir": _write(tmp_path / "e_nir.tif", grid, nir),
        "red": _write(tmp_path / "e_red.tif", grid, red),
        "scl": _write(tmp_path / "e_scl.tif", grid, 4, dtype="uint8"),
        **(extra_assets or {}),
    }
    return dataclasses.replace(
        base,
        id=EARLIER_ID,
        acquired_at=datetime(2020, 3, 10, tzinfo=timezone.utc),
        assets=assets,
        properties={**base.properties, "s2:processing_baseline": baseline},
    )


@pytest.fixture
def change_job():
    return Job(
        id=uuid.uuid4(), process="change", status=JobStatus.QUEUED, progress=0,
        aoi=AOI, aoi_area_km2=4.6, scene_ids=[EARLIER_ID, LATER_ID],
        parameters={"index": "ndvi"},
    )


@pytest.fixture
def store(tmp_path):
    storage.set_storage(storage.LocalStorage(tmp_path / "store"))
    yield
    storage.set_storage(None)
    pipeline.set_offset_cache(cache.default_cache())


#: A DN floor low enough that the resolver decides "offset absent" from pixels
#: alone, so these tests never depend on metadata or on a network read. Seeded
#: rather than measured -- 5.3.1c's rule is tested in test_harmonize.py.
SEEDED_FLOOR = 250.0


def wire(monkeypatch, earlier, later):
    """Resolve both ids, with the offset measurement seeded for each."""
    offsets = cache.MemoryOffsetCache()
    for s in (earlier, later):
        offsets.set(s.id, SEEDED_FLOOR)
    pipeline.set_offset_cache(offsets)
    by_id = {earlier.id: earlier, later.id: later}
    monkeypatch.setattr(processes, "resolve_scene", lambda i: by_id[i])


def run_all(job) -> list["processes.OutputSpec"]:
    """Every output a change job publishes: the difference, then the two dates."""
    return processes.get("change").run(Recorder(), job)


def run(job) -> "processes.OutputSpec":
    """The primary output -- the difference raster."""
    outputs = run_all(job)
    assert outputs[0].output_type == "change_raster", (
        "the difference must stay first; it is the result the user asked for")
    return outputs[0]


class TestRegistration:
    def test_change_takes_two_scenes(self):
        assert processes.get("change").scene_count == 2

    def test_only_indices_can_be_differenced(self):
        """Not `change` itself, and not `fake`."""
        assert set(processes.CHANGEABLE_INDICES) == {"ndvi", "ndwi", "ndbi"}

    def test_it_estimates_roughly_twice_an_index(self):
        """Two dates means two index computations and two scenes to measure."""
        one = processes.estimate_for(processes.get("ndvi"), 250.0)
        two = processes.estimate_for(processes.get("change"), 250.0)
        assert 1.8 * one <= two <= 2.2 * one


class TestChange:
    def test_it_reports_every_stage_in_order(self, store, scene, change_job,
                                             tmp_path, monkeypatch):
        wire(monkeypatch, earlier_scene(scene, tmp_path), scene)
        report = Recorder()
        processes.get("change").run(report, change_job)
        assert report.seen == [JobStatus.SEARCHING, JobStatus.READING,
                               JobStatus.PROCESSING, JobStatus.WRITING_COG]

    def test_identical_dates_difference_to_zero(self, store, scene, change_job,
                                                tmp_path, monkeypatch):
        """The control. Anything else here would mean the grids disagree."""
        wire(monkeypatch, earlier_scene(scene, tmp_path), scene)
        output = run(change_job)
        assert output.stats["mean"] == pytest.approx(0.0, abs=1e-6)
        assert output.stats["loss_fraction"] == 0.0
        assert output.stats["gain_fraction"] == 0.0

    def test_vegetation_loss_reads_as_negative(self, store, scene, change_job,
                                               tmp_path, monkeypatch):
        # earlier NDVI (0.45 - 0.08) / 0.53 = 0.6981; later 0.5789 -> -0.1192
        wire(monkeypatch, earlier_scene(scene, tmp_path, nir=4500), scene)
        assert run(change_job).stats["mean"] == pytest.approx(-0.1192, abs=1e-3)

    def test_the_sign_follows_time_not_argument_order(self, store, scene, change_job,
                                                     tmp_path, monkeypatch):
        """A user listing the pair backwards must not get a flipped answer."""
        wire(monkeypatch, earlier_scene(scene, tmp_path, nir=4500), scene)
        backwards = dataclasses.replace(change_job, scene_ids=[LATER_ID, EARLIER_ID])
        assert run(backwards).stats["mean"] == pytest.approx(-0.1192, abs=1e-3)

    def test_it_is_labelled_a_change_raster(self, store, scene, change_job,
                                            tmp_path, monkeypatch):
        wire(monkeypatch, earlier_scene(scene, tmp_path), scene)
        assert run(change_job).output_type == "change_raster"

    def test_the_output_is_a_valid_cog(self, store, scene, change_job, tmp_path,
                                       monkeypatch):
        from processing import cog
        wire(monkeypatch, earlier_scene(scene, tmp_path), scene)
        run(change_job)
        path = storage.get_storage().local_path(storage.key_for(str(change_job.id)))
        ok, messages = cog.validate_cog(path)
        assert ok, messages

    def test_the_cog_names_both_source_scenes(self, store, scene, change_job,
                                              tmp_path, monkeypatch):
        wire(monkeypatch, earlier_scene(scene, tmp_path), scene)
        run(change_job)
        path = storage.get_storage().local_path(storage.key_for(str(change_job.id)))
        with rasterio.open(path) as src:
            sources = src.tags()["BHOOMI_SOURCE_SCENES"]
        assert EARLIER_ID in sources and LATER_ID in sources


class TestStatistics:
    """5.4.4 rule 3: the asymmetry, not the mean alone."""

    def test_asymmetry_is_reported_beside_the_mean(self, store, scene, change_job,
                                                   tmp_path, monkeypatch):
        wire(monkeypatch, earlier_scene(scene, tmp_path, nir=4500), scene)
        stats = run(change_job).stats
        for key in ("mean", "median", "loss_fraction", "gain_fraction",
                    "threshold", "asymmetry"):
            assert key in stats, key

    def test_a_uniform_loss_is_all_loss_and_no_gain(self, store, scene, change_job,
                                                    tmp_path, monkeypatch):
        """Every pixel falls by more than the 0.2 threshold."""
        wire(monkeypatch, earlier_scene(scene, tmp_path, nir=9000), scene)
        stats = run(change_job).stats
        assert stats["loss_fraction"] == pytest.approx(1.0)
        assert stats["gain_fraction"] == 0.0

    def test_asymmetry_stays_json_safe_with_no_gain(self, store, scene, change_job,
                                                    tmp_path, monkeypatch):
        """Loss over zero gain is infinite, and inf is not JSON -- the row would
        fail to insert."""
        wire(monkeypatch, earlier_scene(scene, tmp_path, nir=9000), scene)
        stats = run(change_job).stats
        assert stats["asymmetry"] is None
        json.dumps(stats)

    def test_the_threshold_is_recorded_with_the_fractions(self, store, scene,
                                                          change_job, tmp_path,
                                                          monkeypatch):
        """"9.73 % lost more than 0.2" is meaningless without the 0.2."""
        wire(monkeypatch, earlier_scene(scene, tmp_path), scene)
        assert run(change_job).stats["threshold"] == pytest.approx(0.2)


class TestBaselineMismatch:
    """5.3: differencing across Sen2Cor versions partly measures version drift."""

    def test_a_mismatch_warns(self, store, scene, change_job, tmp_path, monkeypatch):
        wire(monkeypatch, earlier_scene(scene, tmp_path, baseline="02.14"), scene)
        warnings = run(change_job).warnings
        assert any("baseline" in w.lower() for w in warnings), warnings

    def test_a_mismatch_does_not_refuse(self, store, scene, change_job, tmp_path,
                                        monkeypatch):
        """Refusing would make whole year-pairs unusable for a confound the user
        may accept once told. 5.3 asks the API to flag it, not to block it."""
        wire(monkeypatch, earlier_scene(scene, tmp_path, baseline="02.14"), scene)
        assert run(change_job).output_type == "change_raster"

    def test_matching_baselines_produce_no_baseline_warning(self, store, scene,
                                                            change_job, tmp_path,
                                                            monkeypatch):
        wire(monkeypatch, earlier_scene(scene, tmp_path, baseline="05.12"), scene)
        assert not any("baseline" in w.lower() for w in run(change_job).warnings)


class TestIndexSelection:
    def test_it_differences_the_index_requested(self, store, scene, change_job,
                                                tmp_path, monkeypatch):
        grid = grid_for_aoi(BBOX, 10.0)
        earlier = earlier_scene(
            scene, tmp_path,
            extra_assets={"green": _write(tmp_path / "g_early.tif", grid, 1200)})
        later = dataclasses.replace(
            scene,
            assets={**scene.assets,
                    "green": _write(tmp_path / "g_late.tif", grid, 2400)})
        wire(monkeypatch, earlier, later)

        ndwi_job = dataclasses.replace(change_job, parameters={"index": "ndwi"})
        # ndwi earlier (0.12 - 0.30)/0.42 = -0.4286; later (0.24 - 0.30)/0.54 = -0.1111
        assert run(ndwi_job).stats["mean"] == pytest.approx(0.3175, abs=1e-3)

    def test_it_defaults_to_ndvi(self, store, scene, change_job, tmp_path,
                                 monkeypatch):
        """7.3 leaves `parameters` optional; 5.4.4's flagship metric is NDVI."""
        wire(monkeypatch, earlier_scene(scene, tmp_path, nir=4500), scene)
        no_params = dataclasses.replace(change_job, parameters={})
        assert run(no_params).stats["mean"] == pytest.approx(-0.1192, abs=1e-3)


class TestPerDateRasters:
    """A change job also publishes the two sides of its difference.

    PLAN.md 11's February exit criterion is that the 2020-vs-2026 result
    "renders in a swipe comparison", and a difference raster cannot be
    un-differenced: +0.3 could be bare ground becoming scrub or forest
    becoming denser forest, and only the two dates tell them apart.
    """

    def test_three_outputs_difference_first(self, store, scene, change_job,
                                            tmp_path, monkeypatch):
        wire(monkeypatch, earlier_scene(scene, tmp_path), scene)
        outputs = run_all(change_job)
        assert [o.output_type for o in outputs] == [
            "change_raster", "earlier_ndvi", "later_ndvi"]

    def test_the_per_date_rasters_name_the_index_they_carry(
            self, store, scene, change_job, tmp_path, monkeypatch):
        """So the tile ramp can be chosen without another column."""
        grid = grid_for_aoi(BBOX, 10.0)
        earlier = earlier_scene(
            scene, tmp_path,
            extra_assets={"green": _write(tmp_path / "g_early2.tif", grid, 1200)})
        later = dataclasses.replace(
            scene,
            assets={**scene.assets,
                    "green": _write(tmp_path / "g_late2.tif", grid, 2400)})
        wire(monkeypatch, earlier, later)

        outputs = run_all(dataclasses.replace(change_job, parameters={"index": "ndwi"}))
        assert [o.output_type for o in outputs[1:]] == ["earlier_ndwi", "later_ndwi"]

    def test_each_side_gets_its_own_key(self, store, scene, change_job,
                                        tmp_path, monkeypatch):
        """Three rasters on one job id would otherwise overwrite each other."""
        wire(monkeypatch, earlier_scene(scene, tmp_path), scene)
        outputs = run_all(change_job)
        assert len({o.cog_uri for o in outputs}) == 3

    def test_the_sides_carry_index_stats_not_change_stats(
            self, store, scene, change_job, tmp_path, monkeypatch):
        """An index reports a distribution; a difference reports loss vs gain."""
        wire(monkeypatch, earlier_scene(scene, tmp_path), scene)
        change, earlier, later = run_all(change_job)
        assert "loss_fraction" in (change.stats or {})
        for side in (earlier, later):
            assert "loss_fraction" not in (side.stats or {})
            assert "median" in (side.stats or {})

    def test_the_sides_are_chronological(self, store, scene, change_job,
                                         tmp_path, monkeypatch):
        """`earlier` must be the earlier NDVI, or the swipe reads backwards.

        The earlier scene gets a lower NIR than NIR_DN, so its NDVI is lower:
        (2000-800)/2800 = 0.4286 against the later scene's 0.5789.
        """
        wire(monkeypatch, earlier_scene(scene, tmp_path, nir=2000), scene)
        _, earlier, later = run_all(change_job)
        assert earlier.stats["median"] == pytest.approx(0.4286, abs=1e-3)
        assert later.stats["median"] == pytest.approx(0.5789, abs=1e-3)

    def test_all_three_share_one_grid(self, store, scene, change_job,
                                      tmp_path, monkeypatch):
        """A swipe compares pixels by position; two grids would misregister."""
        wire(monkeypatch, earlier_scene(scene, tmp_path), scene)
        outputs = run_all(change_job)
        assert len({(o.crs, o.resolution_m, str(o.bounds)) for o in outputs}) == 1

    def test_a_failed_side_does_not_lose_the_difference(
            self, store, scene, change_job, tmp_path, monkeypatch):
        """The difference is the answer; a supplementary render is not.

        A job that threw away a completed analysis because one extra raster
        failed to write would be trading the result for a picture.
        """
        wire(monkeypatch, earlier_scene(scene, tmp_path), scene)

        real = processes._publish
        calls = {"n": 0}

        def flaky(*args, **kwargs):
            calls["n"] += 1
            if calls["n"] > 1:
                raise OSError("disk full")
            return real(*args, **kwargs)

        monkeypatch.setattr(processes, "_publish", flaky)
        outputs = run_all(change_job)
        assert [o.output_type for o in outputs] == ["change_raster"]
        assert outputs[0].stats["loss_fraction"] is not None
