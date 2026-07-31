"""The NDVI process, end to end, without network or containers.

`pipeline.compute_index` reads through `raster_utils.read_to_grid`, which is
deliberately indifferent to whether its source is an HTTP COG or a local path.
That is what lets these tests build three small GeoTIFFs, point a Scene's
assets at them, and exercise the whole process -- read, mask, harmonize,
compute, write a COG, publish it, describe it for 7.5 -- with nothing mocked
except the offset decision.

The offset is seeded rather than measured because detection reads a decimated
overview of the *full tile* (PLAN.md 5.3) and a 200x200 synthetic raster is not
one. Its own behaviour is covered in test_harmonize.py; leaving it live here
would test the fixture rather than the process.
"""

from __future__ import annotations

import uuid

import numpy as np
import pytest
import rasterio

import cache
import pipeline
from backend import storage
from backend.db.jobs import Job, JobStatus
from backend.queue import processes
from processing.raster_utils import grid_for_aoi

AOI = {
    "type": "Polygon",
    "coordinates": [[[88.40, 22.60], [88.42, 22.60], [88.42, 22.62],
                     [88.40, 22.62], [88.40, 22.60]]],
}
#: Comfortably contains the AOI, so the D3 coverage check passes.
FOOTPRINT = {
    "type": "Polygon",
    "coordinates": [[[88.30, 22.50], [88.60, 22.50], [88.60, 22.75],
                     [88.30, 22.75], [88.30, 22.50]]],
}

#: DN values. With no BOA offset these scale by 1/10000, giving nir 0.30 and
#: red 0.08 -- an NDVI of (0.30 - 0.08) / 0.38 = 0.5789.
NIR_DN, RED_DN = 3000, 800
EXPECTED_NDVI = (0.30 - 0.08) / (0.30 + 0.08)


def _write(path, grid, value, dtype="uint16"):
    with rasterio.open(
        path, "w", driver="GTiff", width=grid.width, height=grid.height, count=1,
        dtype=dtype, crs=grid.crs, transform=grid.transform,
    ) as dst:
        dst.write(np.full(grid.shape, value, dtype=dtype), 1)
    return str(path)


@pytest.fixture
def scene(tmp_path):
    """A Scene whose bands are real GeoTIFFs on local disk."""
    from datetime import datetime, timezone

    from catalogue import Scene

    grid = grid_for_aoi((88.40, 22.60, 88.42, 22.62), 10.0)
    assets = {
        "nir": _write(tmp_path / "nir.tif", grid, NIR_DN),
        "red": _write(tmp_path / "red.tif", grid, RED_DN),
        "scl": _write(tmp_path / "scl.tif", grid, 4, dtype="uint8"),  # vegetation
    }
    return Scene(
        id="S2B_45QXF_20260304_0_L2A",
        collection="sentinel-2-l2a",
        acquired_at=datetime(2026, 3, 4, tzinfo=timezone.utc),
        bbox=(88.30, 22.50, 88.60, 22.75),
        geometry=FOOTPRINT,
        assets=assets,
        properties={"eo:cloud_cover": 0.0, "s2:processing_baseline": "05.12",
                    "platform": "sentinel-2b"},
        catalogue="earth-search",
    )


@pytest.fixture
def job():
    return Job(
        id=uuid.uuid4(), process="ndvi", status=JobStatus.QUEUED, progress=0,
        aoi=AOI, aoi_area_km2=4.6, scene_ids=["S2B_45QXF_20260304_0_L2A"],
        parameters={},
    )


@pytest.fixture
def wired(tmp_path, scene, monkeypatch):
    """Local storage, a seeded offset cache, and scene resolution stubbed."""
    offsets = cache.MemoryOffsetCache()
    offsets.set(scene.id, False)
    pipeline.set_offset_cache(offsets)

    monkeypatch.setattr(processes, "resolve_scene", lambda _id: scene)
    storage.set_storage(storage.LocalStorage(tmp_path / "store"))
    yield
    storage.set_storage(None)
    pipeline.set_offset_cache(cache.default_cache())


class Recorder:
    """Stands in for the runner's reporter, keeping the order of stages."""

    def __init__(self):
        self.seen: list[JobStatus] = []

    def __call__(self, status: JobStatus) -> None:
        self.seen.append(status)


class TestNdviProcess:
    def test_it_reports_every_stage_in_order(self, wired, job):
        report = Recorder()
        processes.get("ndvi").run(report, job)
        assert report.seen == [JobStatus.SEARCHING, JobStatus.READING,
                               JobStatus.PROCESSING, JobStatus.WRITING_COG]

    def test_it_produces_exactly_one_output(self, wired, job):
        outputs = processes.get("ndvi").run(Recorder(), job)
        assert len(outputs) == 1
        assert outputs[0].output_type == "index_raster"

    def test_the_numbers_are_the_ndvi_of_the_input(self, wired, job):
        """The point of the whole stack: the arithmetic survives the plumbing."""
        (output,) = processes.get("ndvi").run(Recorder(), job)
        assert output.stats["mean"] == pytest.approx(EXPECTED_NDVI, abs=1e-4)
        assert output.stats["min"] == pytest.approx(EXPECTED_NDVI, abs=1e-4)
        assert output.stats["max"] == pytest.approx(EXPECTED_NDVI, abs=1e-4)

    def test_a_fully_valid_scene_keeps_every_pixel(self, wired, job):
        """SCL is all class 4; nothing should be masked."""
        (output,) = processes.get("ndvi").run(Recorder(), job)
        assert output.valid_fraction == pytest.approx(1.0)

    def test_the_cog_is_written_and_readable(self, wired, job):
        (output,) = processes.get("ndvi").run(Recorder(), job)
        path = storage.get_storage().local_path(storage.key_for(str(job.id)))
        assert path is not None
        with rasterio.open(path) as src:
            assert src.count == 1
            assert np.nanmean(src.read(1)) == pytest.approx(EXPECTED_NDVI, abs=1e-4)

    def test_the_output_is_a_valid_cog(self, wired, job):
        """validate_cog returns (ok, messages) -- assert on the flag, not the
        tuple. A 2-tuple is always truthy, so `assert validate_cog(p)` can
        never fail and this test proved nothing until 2026-07-31."""
        from processing import cog
        processes.get("ndvi").run(Recorder(), job)
        path = storage.get_storage().local_path(storage.key_for(str(job.id)))
        ok, messages = cog.validate_cog(path)
        assert ok, messages

    def test_the_cog_carries_its_provenance(self, wired, job):
        processes.get("ndvi").run(Recorder(), job)
        path = storage.get_storage().local_path(storage.key_for(str(job.id)))
        with rasterio.open(path) as src:
            tags = src.tags()
        assert "S2B_45QXF_20260304_0_L2A" in str(tags)
        # 5.3: whether the offset was applied is recorded, not inferred later.
        assert "BHOOMI_BOA_OFFSET_PRESENT" in tags

    def test_size_bytes_matches_the_stored_file(self, wired, job):
        (output,) = processes.get("ndvi").run(Recorder(), job)
        path = storage.get_storage().local_path(storage.key_for(str(job.id)))
        assert output.size_bytes == path.stat().st_size

    def test_the_scratch_file_does_not_survive(self, wired, job):
        """A leaked scratch dir would fill the output volume one job at a time."""
        processes.get("ndvi").run(Recorder(), job)
        scratch = storage.get_storage().scratch_dir()
        assert list(scratch.iterdir()) == []

    def test_the_scratch_copy_is_not_left_beside_the_stored_object(self, wired, job):
        """put() moves rather than copies; two 20 MB files per job is waste."""
        processes.get("ndvi").run(Recorder(), job)
        root = storage.get_storage().root
        tifs = sorted(p.name for p in root.rglob("*.tif"))
        assert tifs == [storage.key_for(str(job.id))]


class TestOutputDescription:
    """What 7.5 will serve, and what `outputs` (6) has to accept."""

    def test_bounds_come_back_in_wgs84(self, wired, job):
        """The column is GEOMETRY(Polygon, 4326); the grid is UTM."""
        (output,) = processes.get("ndvi").run(Recorder(), job)
        xs = [c[0] for c in output.bounds["coordinates"][0]]
        ys = [c[1] for c in output.bounds["coordinates"][0]]
        assert output.bounds["type"] == "Polygon"
        assert min(xs) == pytest.approx(88.40, abs=0.01)
        assert max(ys) == pytest.approx(22.62, abs=0.01)

    def test_the_crs_is_the_utm_zone_for_kolkata(self, wired, job):
        (output,) = processes.get("ndvi").run(Recorder(), job)
        assert "32645" in output.crs

    def test_resolution_is_the_native_10_m(self, wired, job):
        """D4: an index is computed at the coarsest native resolution."""
        (output,) = processes.get("ndvi").run(Recorder(), job)
        assert output.resolution_m == pytest.approx(10.0)

    def test_cog_uri_is_a_url_not_a_path(self, wired, job):
        """D5, and the reason LocalStorage returns None from url_for."""
        (output,) = processes.get("ndvi").run(Recorder(), job)
        assert output.cog_uri.startswith("http")
        assert str(job.id) in output.cog_uri

    def test_it_expires_in_thirty_days(self, wired, job):
        from datetime import datetime, timezone
        (output,) = processes.get("ndvi").run(Recorder(), job)
        days = (output.expires_at - datetime.now(timezone.utc)).days
        assert 29 <= days <= 30


class TestMaskingAndFailure:
    def test_cloud_is_masked_out_of_the_result(self, wired, job, scene, tmp_path):
        """SCL 9 is high-probability cloud; those pixels must not reach NDVI."""
        grid = grid_for_aoi((88.40, 22.60, 88.42, 22.62), 10.0)
        scl = np.full(grid.shape, 4, dtype="uint8")
        scl[: grid.height // 2, :] = 9
        with rasterio.open(scene.assets["scl"], "r+") as dst:
            dst.write(scl, 1)

        (output,) = processes.get("ndvi").run(Recorder(), job)
        assert output.valid_fraction == pytest.approx(0.5, abs=0.02)

    def test_a_scene_without_scl_still_runs_but_warns(self, wired, job, scene,
                                                     monkeypatch):
        """5.2 wants masking always; an unmasked result must announce itself.

        This warning is the reason `outputs.warnings` exists: it reached the
        log and the GeoTIFF tags but not the API, so a browser user got an
        unmasked raster with nothing on screen to say so.
        """
        stripped = {k: v for k, v in scene.assets.items() if k != "scl"}
        import dataclasses
        monkeypatch.setattr(processes, "resolve_scene",
                            lambda _id: dataclasses.replace(scene, assets=stripped))
        (output,) = processes.get("ndvi").run(Recorder(), job)
        assert any("NOT masked" in w for w in output.warnings)

    def test_a_masked_scene_produces_no_warnings(self, wired, job):
        """The normal case is silence -- a warning shown always is ignored."""
        (output,) = processes.get("ndvi").run(Recorder(), job)
        assert output.warnings == []

    def test_an_aoi_outside_the_scene_is_refused(self, wired, job, monkeypatch):
        """D3, enforced again in the worker -- the API check can be bypassed by
        a scene that changed between submission and execution."""
        import dataclasses
        elsewhere = dataclasses.replace(job, aoi={
            "type": "Polygon",
            "coordinates": [[[70.0, 10.0], [70.02, 10.0], [70.02, 10.02],
                             [70.0, 10.02], [70.0, 10.0]]]})
        with pytest.raises(pipeline.PipelineError, match="inside scene"):
            processes.get("ndvi").run(Recorder(), elsewhere)

    def test_a_scene_missing_a_band_is_refused(self, wired, job, scene, monkeypatch):
        import dataclasses
        no_nir = {k: v for k, v in scene.assets.items() if k != "nir"}
        monkeypatch.setattr(processes, "resolve_scene",
                            lambda _id: dataclasses.replace(scene, assets=no_nir))
        with pytest.raises(pipeline.PipelineError, match="lacks bands"):
            processes.get("ndvi").run(Recorder(), job)

    def test_an_oversized_output_is_refused_rather_than_stored(self, wired, job,
                                                              monkeypatch):
        monkeypatch.setattr(storage, "MAX_OUTPUT_BYTES", 16)
        with pytest.raises(storage.OutputTooLarge):
            processes.get("ndvi").run(Recorder(), job)
        assert storage.get_storage().local_path(storage.key_for(str(job.id))) is None


class TestOtherIndices:
    def test_ndwi_runs_on_the_same_scene_shape(self, wired, job, scene, tmp_path,
                                               monkeypatch):
        import dataclasses
        grid = grid_for_aoi((88.40, 22.60, 88.42, 22.62), 10.0)
        assets = dict(scene.assets)
        assets["green"] = _write(tmp_path / "green.tif", grid, 1200)
        monkeypatch.setattr(processes, "resolve_scene",
                            lambda _id: dataclasses.replace(scene, assets=assets))
        ndwi_job = dataclasses.replace(job, process="ndwi")
        (output,) = processes.get("ndwi").run(Recorder(), ndwi_job)
        # green 0.12, nir 0.30 -> (0.12 - 0.30) / 0.42 = -0.4286
        assert output.stats["mean"] == pytest.approx(-0.4286, abs=1e-3)

    def test_ndbi_is_computed_at_20_m(self, wired, job, scene, tmp_path, monkeypatch):
        """D4: B11 is 20 m, and upsampling SWIR would invent detail."""
        import dataclasses
        grid20 = grid_for_aoi((88.40, 22.60, 88.42, 22.62), 20.0)
        assets = dict(scene.assets)
        assets["swir16"] = _write(tmp_path / "swir.tif", grid20, 2000)
        monkeypatch.setattr(processes, "resolve_scene",
                            lambda _id: dataclasses.replace(scene, assets=assets))
        ndbi_job = dataclasses.replace(job, process="ndbi")
        (output,) = processes.get("ndbi").run(Recorder(), ndbi_job)
        assert output.resolution_m == pytest.approx(20.0)
