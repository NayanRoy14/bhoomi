"""OGC API - Processes Part 1: Core (PLAN.md 7.6).

The claim this file has to defend is not "these URLs return 200". It is 7.6's
own: **a thin standards-compliant facade over the same queue -- not a parallel
implementation.** So the tests that matter are the ones that would fail if the
two paths ever diverged: a job submitted through `/ogc` must be visible and
identical through `/api/v1`, and every limit must apply to both.

Discovery is tested without infrastructure, because a client reads the process
description *before* it can submit anything, and a description that only works
when Postgres is up is a description no one can rely on.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from backend.api.main import app
from backend.db.jobs import JobStatus
from backend.queue import processes
from tests.conftest import needs_db
from tests.test_catalogue import KOLKATA_AOI
from tests.test_jobs import CLIENT, SCENE_ID, api  # noqa: F401


@pytest.fixture
def bare():
    """No database, no queue. Enough for discovery."""
    return TestClient(app, client=CLIENT, raise_server_exceptions=False)


def execute(client: TestClient, process="fake", **inputs):
    body = {"inputs": {"aoi": KOLKATA_AOI, "scene_ids": [SCENE_ID], **inputs}}
    return client.post(f"/ogc/processes/{process}/execution", json=body)


# ------------------------------------------------------------------ discovery

class TestLandingAndConformance:
    def test_the_landing_page_links_onward(self, bare):
        """A client starts at / and follows links; a dead end is unusable."""
        body = bare.get("/ogc").json()
        rels = {link["rel"] for link in body["links"]}
        assert "self" in rels
        assert any(r.endswith("/conformance") for r in rels)
        assert any(r.endswith("/processes") for r in rels)

    def test_every_advertised_link_resolves(self, bare):
        """Discovery that 404s is worse than no discovery.

        A link may answer 503 when the deployment is incomplete -- the job list
        needs a database, and saying so is honest. What it must never be is
        404: that means the link was wrong, which no amount of infrastructure
        would fix.
        """
        for link in bare.get("/ogc").json()["links"]:
            href = link["href"]
            if not href.startswith("/"):
                continue
            status = bare.get(href).status_code
            assert status in (200, 503), f"{href} -> {status}"
            assert status != 404, href

    def test_conformance_sits_under_the_landing_page(self, bare):
        """`{root}/conformance`, where the root is wherever the landing page is.

        The landing page is at `/ogc`, so OGC API - Common puts the conformance
        declaration at `/ogc/conformance`. It used to be served only at
        `/conformance`: link-following clients were fine, but a validator
        applying the standard's path rule to the root it was given got a 404
        from an API that advertises Core. Both paths answer now; this pins the
        one the standard actually asks for.
        """
        assert bare.get("/ogc/conformance").status_code == 200
        assert bare.get("/ogc/conformance").json() == bare.get("/conformance").json()

    def test_conformance_declares_core(self, bare):
        classes = bare.get("/ogc/conformance").json()["conformsTo"]
        assert any(c.endswith("ogcapi-processes-1/1.0/conf/core") for c in classes)
        assert any(c.endswith("conf/ogc-process-description") for c in classes)
        assert any(c.endswith("conf/job-list") for c in classes)

    def test_it_does_not_claim_what_it_cannot_do(self, bare):
        """Declaring an unimplemented class breaks clients that trust the list.

        No synchronous execution (a job can take minutes), and no dismiss --
        DELETE /jobs/{id} does not exist, so a client must not be told it can
        cancel a runaway job.
        """
        classes = bare.get("/conformance").json()["conformsTo"]
        assert not any(c.endswith("conf/sync-execute") for c in classes)
        assert not any(c.endswith("conf/dismiss") for c in classes)
        assert not any(c.endswith("conf/callback") for c in classes)


class TestProcessDescription:
    def test_it_lists_the_same_processes_as_the_registry(self, bare):
        """One registry. A second list would drift the day a process is added."""
        listed = {p["id"] for p in bare.get("/ogc/processes").json()["processes"]}
        assert listed == set(processes.names())

    def test_the_queue_check_is_not_advertised(self, bare):
        """`fake` computes nothing; a catalogue of processes should not offer it.

        Unlisted, not removed. It stays in the registry and stays executable --
        the delivery tests submit it -- so this pins the distinction: absent
        from what clients discover, present to anything that asks for it by
        name.
        """
        listed = {p["id"] for p in bare.get("/ogc/processes").json()["processes"]}
        assert "fake" not in listed
        assert "fake" in processes.names(include_hidden=True)
        assert processes.get("fake") is not None
        assert bare.get("/ogc/processes/fake").status_code == 200

    def test_each_process_describes_itself(self, bare):
        for name in processes.names(include_hidden=True):
            body = bare.get(f"/ogc/processes/{name}").json()
            assert body["id"] == name
            assert "aoi" in body["inputs"]
            assert "scene_ids" in body["inputs"]
            assert body["outputs"], f"{name} declares no output"

    def test_the_scene_count_is_in_the_schema(self, bare):
        """A client should learn change needs two scenes without submitting one."""
        one = bare.get("/ogc/processes/ndvi").json()["inputs"]["scene_ids"]["schema"]
        two = bare.get("/ogc/processes/change").json()["inputs"]["scene_ids"]["schema"]
        assert one["minItems"] == one["maxItems"] == 1
        assert two["minItems"] == two["maxItems"] == 2

    def test_change_declares_its_index_options(self, bare):
        index = bare.get("/ogc/processes/change").json()["inputs"]["index"]
        assert set(index["schema"]["enum"]) == set(processes.CHANGEABLE_INDICES)
        assert index["schema"]["default"] == processes.DEFAULT_CHANGE_INDEX

    def test_change_declares_the_per_date_outputs(self, bare):
        """They are published, so a client reading only the description sees them."""
        outputs = bare.get("/ogc/processes/change").json()["outputs"]
        assert "earlier_index" in outputs and "later_index" in outputs

    def test_async_only_is_declared_honestly(self, bare):
        for p in bare.get("/ogc/processes").json()["processes"]:
            assert p["jobControlOptions"] == ["async-execute"]

    def test_an_unknown_process_names_the_real_ones(self, bare):
        body = bare.get("/ogc/processes/not_a_process").json()
        assert body["detail"]["code"] == "unknown_process"
        assert set(body["detail"]["available"]) == set(processes.names())


# ------------------------------------------------------------------ execution

@needs_db
class TestExecution:
    def test_it_returns_201_with_a_location(self, api):
        """Part 1 Core: async execution answers 201 and a Location header."""
        response = execute(api)
        assert response.status_code == 201
        job_id = response.json()["jobID"]
        assert response.headers["Location"] == f"/ogc/jobs/{job_id}"

    def test_the_status_starts_accepted(self, api):
        """`queued` is Bhoomi's word; `accepted` is the standard's."""
        assert execute(api).json()["status"] == "accepted"

    def test_prefer_respond_async_is_echoed(self, api):
        response = api.post(
            "/ogc/processes/fake/execution",
            json={"inputs": {"aoi": KOLKATA_AOI, "scene_ids": [SCENE_ID]}},
            headers={"Prefer": "respond-async"})
        assert response.headers.get("Preference-Applied") == "respond-async"

    def test_it_enqueues_on_the_same_queue(self, api):
        """The facade claim, at its most literal."""
        before = len(api.queue.enqueued)
        execute(api)
        assert len(api.queue.enqueued) == before + 1

    def test_the_same_job_is_visible_through_both_apis(self, api):
        """If these ever disagree, one of them is a parallel implementation."""
        job_id = execute(api).json()["jobID"]

        ogc = api.get(f"/ogc/jobs/{job_id}").json()
        native = api.get(f"/api/v1/jobs/{job_id}").json()

        assert ogc["jobID"] == native["job_id"]
        assert ogc["processID"] == native["process"]
        assert ogc["progress"] == native["progress"]

    def test_a_missing_aoi_says_what_was_expected(self, api):
        response = api.post("/ogc/processes/fake/execution",
                            json={"inputs": {"scene_ids": [SCENE_ID]}})
        assert response.status_code == 400
        assert response.json()["detail"]["code"] == "missing_input"

    def test_a_missing_scene_list_is_refused(self, api):
        response = api.post("/ogc/processes/fake/execution",
                            json={"inputs": {"aoi": KOLKATA_AOI}})
        assert response.status_code == 400

    def test_an_unknown_process_is_refused_before_the_queue(self, api):
        before = len(api.queue.enqueued)
        assert execute(api, process="not_a_process").status_code == 400
        assert len(api.queue.enqueued) == before

    def test_the_wrong_scene_count_is_refused(self, api):
        """The shared validator, reached through the OGC door."""
        response = api.post(
            "/ogc/processes/change/execution",
            json={"inputs": {"aoi": KOLKATA_AOI, "scene_ids": [SCENE_ID]}})
        assert response.status_code == 400
        assert response.json()["detail"]["code"] == "wrong_scene_count"

    def test_limits_apply_here_too(self, api):
        """8's AOI cap is not something the standards door gets to skip."""
        huge = {"type": "Polygon", "coordinates": [[
            [77.0, 20.0], [90.0, 20.0], [90.0, 30.0], [77.0, 30.0], [77.0, 20.0]]]}
        response = api.post("/ogc/processes/fake/execution",
                            json={"inputs": {"aoi": huge, "scene_ids": [SCENE_ID]}})
        assert response.status_code == 400
        assert response.json()["detail"]["code"] == "aoi_too_large"

    def test_extra_inputs_are_passed_through_not_rejected(self, api):
        """A client sending a field a later version added must not break."""
        assert execute(api, mask_snow=False).status_code == 201


# ----------------------------------------------------------------- job access

@needs_db
class TestJobs:
    def test_the_job_list_includes_a_new_job(self, api):
        job_id = execute(api).json()["jobID"]
        listed = {j["jobID"] for j in api.get("/ogc/jobs").json()["jobs"]}
        assert job_id in listed

    def test_the_job_list_pages(self, api, clean_db):
        """Created through the store, not the API.

        PLAN.md 8 caps concurrent jobs at one per IP, so a single client
        *cannot* submit three at once -- which the test below asserts on
        purpose. The list endpoint still has to page.
        """
        from backend.db.jobs import JobStore

        store = JobStore(engine=clean_db)
        for _ in range(3):
            store.create("fake", KOLKATA_AOI, 10.0, [SCENE_ID])

        first = api.get("/ogc/jobs?limit=2").json()
        assert len(first["jobs"]) == 2
        assert any(link["rel"] == "next" for link in first["links"])

        second = api.get("/ogc/jobs?limit=2&offset=2").json()
        assert second["jobs"]
        assert any(link["rel"] == "prev" for link in second["links"])
        # No overlap: a page that repeats rows is a page a client cannot walk.
        assert not ({j["jobID"] for j in first["jobs"]}
                    & {j["jobID"] for j in second["jobs"]})

    def test_the_concurrency_cap_applies_through_this_door_too(self, api):
        """8 caps one active job per IP. The standards route is not exempt."""
        assert execute(api).status_code == 201
        second = execute(api)
        assert second.status_code == 429
        assert second.json()["detail"]["code"] == "too_many_active_jobs"

    def test_a_malformed_job_id_is_not_found_not_a_schema_error(self, api):
        assert api.get("/ogc/jobs/not-a-uuid").status_code == 404

    def test_results_are_refused_while_running(self, api):
        job_id = execute(api).json()["jobID"]
        response = api.get(f"/ogc/jobs/{job_id}/results")
        assert response.status_code == 404
        assert response.json()["detail"]["code"] == "result_not_ready"

    def test_results_reference_the_raster_rather_than_inlining_it(self, api, clean_db):
        """`outputTransmission: reference`. A 20 MB COG does not go in JSON."""
        from backend.db.jobs import JobStore

        job_id = execute(api).json()["jobID"]
        store = JobStore(engine=clean_db)
        for status in (JobStatus.SEARCHING, JobStatus.READING, JobStatus.PROCESSING,
                       JobStatus.WRITING_COG):
            store.advance(job_id, status)
        store.add_output(job_id, "index_raster", "file:///tmp/x.tif",
                         KOLKATA_AOI, "EPSG:32645", 10.0)
        store.advance(job_id, JobStatus.COMPLETED)

        results = api.get(f"/ogc/jobs/{job_id}/results").json()
        assert "fake" in results
        entry = results["fake"]
        assert entry["href"].startswith("http")
        assert "geotiff" in entry["type"]

    def test_a_failed_job_is_a_409_not_an_empty_result(self, api, clean_db):
        from backend.db.jobs import JobStore

        job_id = execute(api).json()["jobID"]
        JobStore(engine=clean_db).advance(job_id, JobStatus.FAILED,
                                          error_message="upstream gone")
        response = api.get(f"/ogc/jobs/{job_id}/results")
        assert response.status_code == 409

    def test_a_failed_job_reports_the_standard_status(self, api, clean_db):
        from backend.db.jobs import JobStore

        job_id = execute(api).json()["jobID"]
        JobStore(engine=clean_db).advance(job_id, JobStatus.FAILED,
                                          error_message="upstream gone")
        assert api.get(f"/ogc/jobs/{job_id}").json()["status"] == "failed"

    def test_a_timeout_is_failed_not_dismissed(self, api, clean_db):
        """`dismissed` means the client asked. A 10-minute kill did not."""
        from backend.db.jobs import JobStore

        job_id = execute(api).json()["jobID"]
        JobStore(engine=clean_db).advance(job_id, JobStatus.TIMED_OUT,
                                          error_message="over the limit")
        assert api.get(f"/ogc/jobs/{job_id}").json()["status"] == "failed"


class TestStatusMapping:
    """Every internal state must land on a standard one -- no gaps."""

    def test_every_job_status_maps(self):
        from backend.api.routes.ogc import _STATUS

        for status in JobStatus:
            assert status in _STATUS, f"{status} has no OGC equivalent"

    def test_only_standard_values_are_produced(self):
        from backend.api.routes.ogc import _STATUS

        allowed = {"accepted", "running", "successful", "failed", "dismissed"}
        assert set(_STATUS.values()) <= allowed
