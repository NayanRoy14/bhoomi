"""Tests for the job queue (PLAN.md 4.3, 7.3-7.5, 8).

Four layers, each needing less than the last to be believable:

- the state machine, the process registry and IP normalisation are pure;
- the 503 paths need no infrastructure, because their whole point is that
  there is none;
- the store needs Postgres, because the interesting logic is in SQL -- an
  UPDATE whose WHERE clause rejects an illegal transition, and an advisory
  lock that serialises two submissions;
- delivery needs Redis, because "the worker picked it up" is not a claim a
  fake queue can support.
"""

from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient

from backend.api import schemas
from backend.db.jobs import (
    IllegalTransition,
    JobStatus,
    JobStore,
    TooManyActiveJobs,
    allowed_transitions,
    normalize_ip,
)
from backend.queue import processes
from tests.conftest import TEST_DB_URL, TEST_REDIS_URL, needs_db, needs_redis
from tests.test_catalogue import KOLKATA_AOI, TILE_45QXF, item

SCENE_ID = "S2B_45QXF_20260304_0_L2A"
CLIENT = ("203.0.113.5", 12345)


# ------------------------------------------------------------------- pure

class TestStateMachine:
    """PLAN.md 4.3. The machine is what stops a worker resurrecting a job."""

    def test_the_happy_path_is_a_chain(self):
        chain = [JobStatus.QUEUED, JobStatus.SEARCHING, JobStatus.READING,
                 JobStatus.PROCESSING, JobStatus.WRITING_COG, JobStatus.COMPLETED]
        for current, expected_next in zip(chain, chain[1:]):
            assert expected_next in allowed_transitions(current)

    def test_no_stage_may_be_skipped(self):
        assert JobStatus.PROCESSING not in allowed_transitions(JobStatus.QUEUED)
        assert JobStatus.COMPLETED not in allowed_transitions(JobStatus.READING)

    def test_every_active_state_can_abort(self):
        """A failure, a cancellation or the timeout can arrive at any point."""
        for state in (JobStatus.QUEUED, JobStatus.SEARCHING, JobStatus.READING,
                      JobStatus.PROCESSING, JobStatus.WRITING_COG):
            allowed = allowed_transitions(state)
            assert {JobStatus.FAILED, JobStatus.CANCELLED,
                    JobStatus.TIMED_OUT} <= allowed

    def test_terminal_states_are_dead_ends(self):
        for state in (JobStatus.COMPLETED, JobStatus.FAILED,
                      JobStatus.CANCELLED, JobStatus.TIMED_OUT):
            assert allowed_transitions(state) == frozenset()

    def test_completed_cannot_go_backwards(self):
        """The retry-after-success case: it would strand a finished job."""
        assert JobStatus.PROCESSING not in allowed_transitions(JobStatus.COMPLETED)

    def test_progress_never_decreases_along_the_chain(self):
        from backend.db.jobs import PROGRESS
        chain = [JobStatus.QUEUED, JobStatus.SEARCHING, JobStatus.READING,
                 JobStatus.PROCESSING, JobStatus.WRITING_COG, JobStatus.COMPLETED]
        values = [PROGRESS[s] for s in chain]
        assert values == sorted(values)
        assert values[0] == 0 and values[-1] == 100


class TestProcessRegistry:
    def test_fake_is_registered(self):
        assert processes.get("fake") is not None

    def test_the_indices_are_registered(self):
        for name in ("ndvi", "ndwi", "ndbi"):
            assert processes.get(name) is not None

    def test_change_is_not_registered_yet(self):
        """Two-date differencing is February (PLAN.md 11); `pipeline` has it,
        nothing exposes it."""
        assert processes.get("change") is None

    def test_unknown_process_is_none_not_an_error(self):
        assert processes.get("nonsense") is None

    def test_indices_take_one_scene(self):
        for name in ("ndvi", "ndwi", "ndbi"):
            assert processes.get(name).scene_count == 1

    def test_estimate_is_at_least_a_second(self):
        assert processes.estimate_for(processes.FAKE, 0.0) >= 1

    def test_fake_estimate_matches_its_runtime(self):
        assert processes.estimate_for(processes.FAKE, 251.5) == 10

    def test_an_index_estimate_uses_the_measured_fit(self):
        """PLAN.md 8: 3.2 + 2.8 x Mpixels, plus 6 s of offset detection."""
        # 251.5 km2 at 10 m is 2.515 Mpixels -> 3.2 + 7.04 + 6.0 = 16.2
        assert processes.estimate_for(processes.get("ndvi"), 251.5) == 16

    def test_a_bigger_aoi_estimates_longer(self):
        small = processes.estimate_for(processes.get("ndvi"), 10.0)
        large = processes.estimate_for(processes.get("ndvi"), 500.0)
        assert large > small


class TestNormalizeIp:
    """`request.client.host` is not always an address (PLAN.md 6: INET)."""

    def test_ipv4(self):
        assert normalize_ip("203.0.113.5") == "203.0.113.5"

    def test_ipv6(self):
        assert normalize_ip("2001:db8::1") == "2001:db8::1"

    def test_a_hostname_becomes_null(self):
        """Postgres rejects it; a 500 on submission would be the alternative."""
        assert normalize_ip("testclient") is None

    def test_empty_and_none_become_null(self):
        assert normalize_ip("") is None
        assert normalize_ip(None) is None


# -------------------------------------------------- no infrastructure needed

class StubQueue:
    """Records enqueues instead of performing them."""

    def __init__(self, fail: bool = False) -> None:
        self.enqueued: list[tuple] = []
        self.fail = fail

    def enqueue(self, func, *args, **kwargs):
        if self.fail:
            raise RuntimeError("redis is down")
        self.enqueued.append((func, args, kwargs))
        return object()

    def __len__(self) -> int:
        return len(self.enqueued)


def submit(client: TestClient, **overrides) -> "object":
    body = {"process": "fake", "scene_ids": [SCENE_ID], "aoi": KOLKATA_AOI}
    body.update(overrides)
    return client.post("/api/v1/jobs", json=body)


class TestUnavailable:
    """503, not 500 -- the deployment is incomplete, the request was fine."""

    def test_no_database_refuses_submission(self, monkeypatch):
        monkeypatch.delenv("BHOOMI_DATABASE_URL", raising=False)
        monkeypatch.setenv("BHOOMI_REDIS_URL", "redis://localhost:6379/0")
        from backend.api.main import app
        resp = submit(TestClient(app, client=CLIENT, raise_server_exceptions=False))
        assert resp.status_code == 503
        assert "database" in resp.json()["detail"]["message"]

    def test_no_queue_refuses_submission(self, monkeypatch):
        monkeypatch.setenv("BHOOMI_DATABASE_URL",
                           TEST_DB_URL or "postgresql://x@127.0.0.1:1/x")
        monkeypatch.delenv("BHOOMI_REDIS_URL", raising=False)
        from backend.api.main import app
        resp = submit(TestClient(app, client=CLIENT, raise_server_exceptions=False))
        assert resp.status_code == 503
        assert "queue" in resp.json()["detail"]["message"]

    def test_search_still_works_without_either(self, monkeypatch):
        """The point of refusing jobs rather than failing the whole API."""
        monkeypatch.delenv("BHOOMI_DATABASE_URL", raising=False)
        monkeypatch.delenv("BHOOMI_REDIS_URL", raising=False)
        from backend.api.main import app
        from backend.api.deps import get_catalogue
        from tests.test_catalogue import StubCatalogue

        app.dependency_overrides[get_catalogue] = lambda: StubCatalogue([item(SCENE_ID)])
        try:
            resp = TestClient(app, client=CLIENT).post(
                "/api/v1/scenes/search", json={"aoi": KOLKATA_AOI})
            assert resp.status_code == 200
        finally:
            app.dependency_overrides.clear()


# ------------------------------------------------------------ store (needs DB)

@pytest.fixture
def jobs(clean_db):
    return JobStore(engine=clean_db)


@needs_db
class TestJobStoreBasics:
    def test_a_new_job_is_queued_at_zero(self, jobs):
        job = jobs.create("fake", KOLKATA_AOI, 251.5, [SCENE_ID])
        assert job.status is JobStatus.QUEUED
        assert job.progress == 0
        assert job.completed_at is None

    def test_round_trip(self, jobs):
        created = jobs.create("fake", KOLKATA_AOI, 251.5, [SCENE_ID],
                              {"mask_snow": False}, client_ip="203.0.113.5")
        loaded = jobs.get(created.id)
        assert loaded.scene_ids == [SCENE_ID]
        assert loaded.parameters == {"mask_snow": False}
        assert loaded.aoi["type"] == "Polygon"
        assert loaded.aoi_area_km2 == pytest.approx(251.5, rel=1e-4)

    def test_a_missing_job_is_none(self, jobs):
        assert jobs.get(uuid.uuid4()) is None

    def test_a_hostname_client_does_not_break_submission(self, jobs):
        """The INET column would reject it; the job must still be accepted."""
        job = jobs.create("fake", KOLKATA_AOI, 10.0, [SCENE_ID],
                          client_ip="testclient")
        assert jobs.get(job.id) is not None

    def test_position_in_queue(self, jobs):
        first = jobs.create("fake", KOLKATA_AOI, 10.0, [SCENE_ID])
        second = jobs.create("fake", KOLKATA_AOI, 10.0, [SCENE_ID])
        assert jobs.position_in_queue(first.id) == 0
        assert jobs.position_in_queue(second.id) == 1


@needs_db
class TestTransitions:
    def test_the_whole_chain(self, jobs):
        job = jobs.create("fake", KOLKATA_AOI, 10.0, [SCENE_ID])
        for status in (JobStatus.SEARCHING, JobStatus.READING, JobStatus.PROCESSING,
                       JobStatus.WRITING_COG, JobStatus.COMPLETED):
            job = jobs.advance(job.id, status)
            assert job.status is status
        assert job.progress == 100
        assert job.completed_at is not None

    def test_started_at_is_set_once_and_kept(self, jobs):
        job = jobs.create("fake", KOLKATA_AOI, 10.0, [SCENE_ID])
        first = jobs.advance(job.id, JobStatus.SEARCHING).started_at
        later = jobs.advance(job.id, JobStatus.READING).started_at
        assert first is not None and later == first

    def test_skipping_a_stage_is_refused(self, jobs):
        job = jobs.create("fake", KOLKATA_AOI, 10.0, [SCENE_ID])
        with pytest.raises(IllegalTransition):
            jobs.advance(job.id, JobStatus.WRITING_COG)

    def test_a_completed_job_cannot_be_resurrected(self, jobs):
        job = jobs.create("fake", KOLKATA_AOI, 10.0, [SCENE_ID])
        for status in (JobStatus.SEARCHING, JobStatus.READING, JobStatus.PROCESSING,
                       JobStatus.WRITING_COG, JobStatus.COMPLETED):
            jobs.advance(job.id, status)
        with pytest.raises(IllegalTransition):
            jobs.advance(job.id, JobStatus.PROCESSING)

    def test_failing_keeps_the_progress_it_reached(self, jobs):
        """More informative than resetting to 0 or jumping to 100."""
        job = jobs.create("fake", KOLKATA_AOI, 10.0, [SCENE_ID])
        jobs.advance(job.id, JobStatus.SEARCHING)
        jobs.advance(job.id, JobStatus.READING)
        failed = jobs.advance(job.id, JobStatus.FAILED, error_message="nope")
        assert failed.progress == 30
        assert failed.error_message == "nope"
        assert failed.completed_at is not None

    def test_the_internal_detail_is_stored_but_not_on_the_job(self, jobs):
        """4.3: error_detail exists and is never served."""
        from sqlalchemy import text
        job = jobs.create("fake", KOLKATA_AOI, 10.0, [SCENE_ID])
        failed = jobs.advance(job.id, JobStatus.FAILED,
                              error_message="user-facing",
                              error_detail="Traceback (most recent call last)...")
        assert not hasattr(failed, "error_detail")
        with clean_engine(jobs) as conn:
            stored = conn.execute(text(
                "SELECT error_detail FROM jobs WHERE id = :i"), {"i": str(job.id)}
            ).scalar_one()
        assert "Traceback" in stored

    def test_advancing_a_missing_job_raises(self, jobs):
        with pytest.raises(IllegalTransition):
            jobs.advance(uuid.uuid4(), JobStatus.SEARCHING)


def clean_engine(store: JobStore):
    return store.engine.connect()


def _complete(engine, job_id) -> None:
    """Walk a job all the way to completed, since no stage may be skipped."""
    store = JobStore(engine=engine)
    for status in (JobStatus.SEARCHING, JobStatus.READING, JobStatus.PROCESSING,
                   JobStatus.WRITING_COG, JobStatus.COMPLETED):
        store.advance(job_id, status)


@needs_db
class TestConcurrencyCaps:
    """PLAN.md 8: 2 globally, 1 per IP."""

    def test_per_ip_cap(self, jobs):
        jobs.create("fake", KOLKATA_AOI, 10.0, [SCENE_ID], client_ip="203.0.113.5",
                    max_global=2, max_per_ip=1)
        with pytest.raises(TooManyActiveJobs) as exc:
            jobs.create("fake", KOLKATA_AOI, 10.0, [SCENE_ID], client_ip="203.0.113.5",
                        max_global=2, max_per_ip=1)
        assert exc.value.scope == "client"

    def test_a_different_ip_is_unaffected(self, jobs):
        jobs.create("fake", KOLKATA_AOI, 10.0, [SCENE_ID], client_ip="203.0.113.5",
                    max_global=2, max_per_ip=1)
        jobs.create("fake", KOLKATA_AOI, 10.0, [SCENE_ID], client_ip="203.0.113.6",
                    max_global=2, max_per_ip=1)

    def test_global_cap(self, jobs):
        for host in ("203.0.113.5", "203.0.113.6"):
            jobs.create("fake", KOLKATA_AOI, 10.0, [SCENE_ID], client_ip=host,
                        max_global=2, max_per_ip=1)
        with pytest.raises(TooManyActiveJobs) as exc:
            jobs.create("fake", KOLKATA_AOI, 10.0, [SCENE_ID], client_ip="203.0.113.7",
                        max_global=2, max_per_ip=1)
        assert exc.value.scope == "global"

    def test_a_finished_job_frees_its_slot(self, jobs):
        job = jobs.create("fake", KOLKATA_AOI, 10.0, [SCENE_ID], client_ip="203.0.113.5",
                          max_global=2, max_per_ip=1)
        jobs.advance(job.id, JobStatus.CANCELLED)
        jobs.create("fake", KOLKATA_AOI, 10.0, [SCENE_ID], client_ip="203.0.113.5",
                    max_global=2, max_per_ip=1)

    def test_an_unidentifiable_client_still_hits_the_global_cap(self, jobs):
        """No per-IP identity, so only the global bound protects the box."""
        for _ in range(2):
            jobs.create("fake", KOLKATA_AOI, 10.0, [SCENE_ID], client_ip="testclient",
                        max_global=2, max_per_ip=1)
        with pytest.raises(TooManyActiveJobs) as exc:
            jobs.create("fake", KOLKATA_AOI, 10.0, [SCENE_ID], client_ip="testclient",
                        max_global=2, max_per_ip=1)
        assert exc.value.scope == "global"


@needs_db
class TestOutputs:
    def test_round_trip(self, jobs):
        job = jobs.create("fake", KOLKATA_AOI, 10.0, [SCENE_ID])
        jobs.add_output(job.id, "index_raster", "https://cdn.test/a.tif",
                        KOLKATA_AOI, "EPSG:32645", 10.0, size_bytes=2048,
                        valid_fraction=0.94, stats={"mean": 0.42})
        (output,) = jobs.outputs_for(job.id)
        assert output.cog_uri == "https://cdn.test/a.tif"
        assert output.crs == "EPSG:32645"
        assert output.stats == {"mean": 0.42}
        assert output.bounds["type"] == "Polygon"

    def test_a_job_with_no_outputs_returns_empty(self, jobs):
        job = jobs.create("fake", KOLKATA_AOI, 10.0, [SCENE_ID])
        assert jobs.outputs_for(job.id) == []

    def test_outputs_cascade_when_the_job_goes(self, jobs, clean_db):
        from sqlalchemy import text
        job = jobs.create("fake", KOLKATA_AOI, 10.0, [SCENE_ID])
        jobs.add_output(job.id, "index_raster", "https://cdn.test/a.tif",
                        KOLKATA_AOI, "EPSG:32645", 10.0)
        with clean_db.begin() as conn:
            conn.execute(text("DELETE FROM jobs WHERE id = :i"), {"i": str(job.id)})
            remaining = conn.execute(text("SELECT count(*) FROM outputs")).scalar_one()
        assert remaining == 0


# --------------------------------------------------------- API (needs DB)

@pytest.fixture
def api(clean_db, monkeypatch):
    """TestClient with a real database and a queue that only records."""
    from backend.api.deps import get_catalogue, get_scene_store
    from backend.api.main import app
    from backend.api.ratelimit import get_job_limiter, get_limiter
    from backend.db.scenes import PostgresSceneStore
    from backend.queue import connection as queue_connection
    from tests.test_catalogue import StubCatalogue

    monkeypatch.setenv("BHOOMI_DATABASE_URL", TEST_DB_URL)
    queue = StubQueue()
    monkeypatch.setattr(queue_connection, "queue_available", lambda: True)
    monkeypatch.setattr(queue_connection, "get_queue", lambda: queue)

    app.dependency_overrides[get_catalogue] = lambda: StubCatalogue([item(SCENE_ID)])
    app.dependency_overrides[get_scene_store] = lambda: PostgresSceneStore(engine=clean_db)
    get_limiter().reset()
    get_job_limiter().reset()
    try:
        client = TestClient(app, client=CLIENT, raise_server_exceptions=False)
        client.queue = queue
        yield client
    finally:
        app.dependency_overrides.clear()


@needs_db
class TestSubmission:
    def test_a_valid_submission_is_accepted(self, api):
        resp = submit(api)
        assert resp.status_code == 202
        body = resp.json()
        assert body["status"] == "queued"
        assert body["estimated_seconds"] == 10
        assert {link["rel"] for link in body["links"]} == {"status", "result"}

    def test_it_carries_a_location_header(self, api):
        resp = submit(api)
        assert resp.headers["Location"] == f"/api/v1/jobs/{resp.json()['job_id']}"

    def test_it_reaches_the_queue(self, api):
        resp = submit(api)
        assert len(api.queue) == 1
        _, args, _ = api.queue.enqueued[0]
        assert args == (resp.json()["job_id"],)

    def test_an_unregistered_process_is_rejected(self, api):
        resp = submit(api, process="ndvvi")
        assert resp.status_code == 400
        assert "ndvi" in resp.json()["detail"]["message"]
        assert len(api.queue) == 0

    def test_a_real_index_is_accepted_and_estimated(self, api):
        resp = submit(api, process="ndvi")
        assert resp.status_code == 202
        assert resp.json()["estimated_seconds"] == 16

    def test_the_wrong_number_of_scenes_is_rejected(self, api):
        resp = submit(api, scene_ids=[SCENE_ID, SCENE_ID])
        assert resp.status_code == 400
        assert resp.json()["detail"]["code"] == "wrong_scene_count"

    def test_an_oversized_aoi_is_rejected_with_both_numbers(self, api):
        big = {"type": "Polygon", "coordinates": [[[85, 20], [90, 20], [90, 25],
                                                   [85, 25], [85, 20]]]}
        resp = submit(api, aoi=big)
        assert resp.status_code == 400
        assert resp.json()["detail"]["code"] == "aoi_too_large"
        assert resp.json()["detail"]["limit_km2"] == schemas.MAX_AOI_KM2

    def test_an_aoi_crossing_a_scene_boundary_is_rejected(self, api):
        """D3: one scene per job. Rejected at submission, not by a failed job."""
        far = {"type": "Polygon", "coordinates": [[[70.0, 10.0], [70.05, 10.0],
                                                   [70.05, 10.05], [70.0, 10.05],
                                                   [70.0, 10.0]]]}
        resp = submit(api, aoi=far)
        assert resp.status_code == 400
        assert resp.json()["detail"]["code"] == "aoi_spans_scenes"

    def test_a_second_job_from_the_same_client_is_refused(self, api):
        assert submit(api).status_code == 202
        resp = submit(api)
        assert resp.status_code == 429
        assert resp.headers["Retry-After"] == "5"

    def test_a_failed_enqueue_fails_the_job_rather_than_stranding_it(self, api,
                                                                    monkeypatch):
        from backend.queue import connection as queue_connection
        monkeypatch.setattr(queue_connection, "get_queue", lambda: StubQueue(fail=True))
        resp = submit(api)
        assert resp.status_code == 503
        # The row exists and is terminal, not sitting at "queued" forever.
        store = JobStore()
        recent = store.count_active()
        assert recent == 0


@needs_db
class TestStatusAndResult:
    def test_status_of_a_fresh_job(self, api):
        job_id = submit(api).json()["job_id"]
        body = api.get(f"/api/v1/jobs/{job_id}").json()
        assert body["status"] == "queued"
        assert body["progress"] == 0
        assert body["message"] == "Waiting for a worker"

    def test_an_unknown_job_is_404(self, api):
        assert api.get(f"/api/v1/jobs/{uuid.uuid4()}").status_code == 404

    def test_a_malformed_id_is_404_not_422(self, api):
        """A stale link should say "no such job", not complain about UUIDs."""
        resp = api.get("/api/v1/jobs/not-a-uuid")
        assert resp.status_code == 404
        assert resp.json()["detail"]["code"] == "job_not_found"

    def test_the_result_is_404_while_incomplete_and_says_why(self, api):
        job_id = submit(api).json()["job_id"]
        resp = api.get(f"/api/v1/jobs/{job_id}/result")
        assert resp.status_code == 404
        assert resp.json()["detail"]["status"] == "queued"

    def test_a_failed_job_gives_409_so_a_poller_stops(self, api, clean_db):
        job_id = submit(api).json()["job_id"]
        JobStore(engine=clean_db).advance(job_id, JobStatus.FAILED,
                                          error_message="it broke")
        resp = api.get(f"/api/v1/jobs/{job_id}/result")
        assert resp.status_code == 409
        assert "it broke" in resp.json()["detail"]["message"]

    def test_the_traceback_is_never_served(self, api, clean_db):
        job_id = submit(api).json()["job_id"]
        JobStore(engine=clean_db).advance(
            job_id, JobStatus.FAILED, error_message="Processing failed.",
            error_detail="Traceback: secret internal path /app/backend/x.py")
        body = api.get(f"/api/v1/jobs/{job_id}").text
        assert "Traceback" not in body
        assert "error_detail" not in body

    def test_a_completed_job_with_no_outputs_returns_an_empty_list(self, api, clean_db):
        """What every fake job produces -- honest rather than a fabricated URL."""
        job_id = submit(api).json()["job_id"]
        _complete(clean_db, job_id)
        resp = api.get(f"/api/v1/jobs/{job_id}/result")
        assert resp.status_code == 200
        assert resp.json()["outputs"] == []

    def test_an_output_is_serialised_with_bounds_as_a_bbox(self, api, clean_db):
        """Exercises the path real processing will use: 7.5 wants a bbox, the
        column holds a polygon."""
        job_id = submit(api).json()["job_id"]
        store = JobStore(engine=clean_db)
        store.add_output(job_id, "index_raster", "https://cdn.test/a.tif",
                         KOLKATA_AOI, "EPSG:32645", 10.0, valid_fraction=0.94,
                         stats={"mean": 0.42})
        _complete(clean_db, job_id)

        (output,) = api.get(f"/api/v1/jobs/{job_id}/result").json()["outputs"]
        assert output["cog"] == "https://cdn.test/a.tif"
        assert output["crs"] == "EPSG:32645"
        assert output["valid_fraction"] == pytest.approx(0.94)
        assert output["download"].endswith(f"/{job_id}/download")
        assert len(output["bounds"]) == 4
        assert output["bounds"] == pytest.approx([88.35, 22.55, 88.52, 22.68])
        # Null until TiTiler is wired up; better absent than a URL that 404s.
        assert output["tiles"] is None


# ------------------------------------------------------- the runner (needs DB)

@needs_db
class TestRunner:
    """`run_job` without RQ: the state machine as the worker drives it."""

    def test_it_walks_the_whole_machine(self, jobs, monkeypatch):
        from backend.queue import tasks
        monkeypatch.setenv("BHOOMI_DATABASE_URL", TEST_DB_URL)
        monkeypatch.setattr(processes.FAKE.run.__globals__["time"], "sleep",
                            lambda _s: None)

        job = jobs.create("fake", KOLKATA_AOI, 10.0, [SCENE_ID])
        assert tasks.run_job(str(job.id)) == "completed"
        assert jobs.get(job.id).status is JobStatus.COMPLETED

    def test_a_missing_row_is_not_an_exception(self, jobs, monkeypatch):
        from backend.queue import tasks
        monkeypatch.setenv("BHOOMI_DATABASE_URL", TEST_DB_URL)
        assert tasks.run_job(str(uuid.uuid4())) == "missing"

    def test_an_already_cancelled_job_is_not_run(self, jobs, monkeypatch):
        from backend.queue import tasks
        monkeypatch.setenv("BHOOMI_DATABASE_URL", TEST_DB_URL)
        job = jobs.create("fake", KOLKATA_AOI, 10.0, [SCENE_ID])
        jobs.advance(job.id, JobStatus.CANCELLED)
        assert tasks.run_job(str(job.id)) == "cancelled"

    def test_a_raising_process_is_recorded_as_failed(self, jobs, monkeypatch):
        from backend.queue import processes as proc_module
        from backend.queue import tasks

        def explode(report, job):
            report(JobStatus.SEARCHING)
            raise ValueError("boom")

        monkeypatch.setenv("BHOOMI_DATABASE_URL", TEST_DB_URL)
        monkeypatch.setitem(
            proc_module.REGISTRY, "fake",
            proc_module.ProcessSpec("fake", 1, "", lambda m: 1.0, explode))

        job = jobs.create("fake", KOLKATA_AOI, 10.0, [SCENE_ID])
        with pytest.raises(ValueError):
            tasks.run_job(str(job.id))

        failed = jobs.get(job.id)
        assert failed.status is JobStatus.FAILED
        assert failed.error_message == tasks.GENERIC_FAILURE
        assert "boom" not in (failed.error_message or "")

    def test_a_timeout_is_recorded_as_timed_out_not_failed(self, jobs, monkeypatch):
        """PLAN.md 8 wants the two distinguishable; they mean different things."""
        from rq.timeouts import JobTimeoutException

        from backend.queue import processes as proc_module
        from backend.queue import tasks

        def hang(report, job):
            report(JobStatus.SEARCHING)
            raise JobTimeoutException("too slow")

        monkeypatch.setenv("BHOOMI_DATABASE_URL", TEST_DB_URL)
        monkeypatch.setitem(
            proc_module.REGISTRY, "fake",
            proc_module.ProcessSpec("fake", 1, "", lambda m: 1.0, hang))

        job = jobs.create("fake", KOLKATA_AOI, 10.0, [SCENE_ID])
        with pytest.raises(JobTimeoutException):
            tasks.run_job(str(job.id))
        assert jobs.get(job.id).status is JobStatus.TIMED_OUT

    def test_a_process_removed_after_submission_fails_cleanly(self, jobs, monkeypatch):
        from backend.queue import tasks
        monkeypatch.setenv("BHOOMI_DATABASE_URL", TEST_DB_URL)
        job = jobs.create("gone", KOLKATA_AOI, 10.0, [SCENE_ID])
        assert tasks.run_job(str(job.id)) == "failed"
        assert "gone" in jobs.get(job.id).error_message


# ----------------------------------------------- delivery (needs DB + Redis)

@needs_db
@needs_redis
class TestQueueDelivery:
    """That a worker actually receives the job is not a fake's claim to make."""

    def test_a_worker_drains_the_queue_and_completes_the_job(
            self, clean_db, redis_conn, monkeypatch):
        from rq import Queue, SimpleWorker

        from backend.queue import tasks

        monkeypatch.setenv("BHOOMI_DATABASE_URL", TEST_DB_URL)
        monkeypatch.setattr(processes.FAKE.run.__globals__["time"], "sleep",
                            lambda _s: None)

        store = JobStore(engine=clean_db)
        job = store.create("fake", KOLKATA_AOI, 10.0, [SCENE_ID])

        queue = Queue("bhoomi-test", connection=redis_conn)
        queue.enqueue(tasks.run_job, str(job.id))
        assert len(queue) == 1

        SimpleWorker([queue], connection=redis_conn).work(burst=True)

        assert len(queue) == 0
        assert store.get(job.id).status is JobStatus.COMPLETED

    def test_health_reports_queue_depth(self, monkeypatch, redis_conn):
        from backend.api.main import app
        from backend.queue import connection as queue_connection

        monkeypatch.setenv("BHOOMI_REDIS_URL", TEST_REDIS_URL)
        queue_connection.reset_connections()
        body = TestClient(app, client=CLIENT).get("/health").json()
        assert body["queue_depth"] == 0
        assert body["status"] == "ok"
