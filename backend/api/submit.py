"""Job submission, shared by the native API and the OGC facade (PLAN.md 7.6).

7.6 asks for "a thin standards-compliant facade over the same queue -- not a
parallel implementation". The only way to mean that is for both routes to run
*this* function: every limit, every rejection message, every state transition
happens once, so a job submitted through `/ogc` cannot behave differently from
the same job submitted through `/api/v1`.

What differs between the two is the shape of the request and the shape of the
reply. That is presentation, and it stays in the routers.
"""

from __future__ import annotations

import logging

from fastapi import Request

from backend.api import errors, schemas
from backend.api.ratelimit import client_key, get_job_limiter
from backend.db import SceneStore
from backend.db.jobs import Job, JobStatus, JobStore, JobsUnavailable, TooManyActiveJobs
from backend.queue import connection as queue_connection
from backend.queue import processes
from backend.queue.tasks import run_job
from backend.resolve import resolve_scene
from catalogue import Catalogue
from processing import raster_utils

logger = logging.getLogger(__name__)

#: An AOI computed to be inside a footprint can land a hair under 1.0 through
#: floating point alone. 0.999 of a 500 km² AOI is half a square kilometre --
#: below any real scene-boundary crossing, which loses whole percentages.
COVERAGE_TOLERANCE = 0.999


def submit_job(
    *,
    process: str,
    aoi: dict,
    scene_ids: list[str],
    parameters: dict,
    http_request: Request,
    jobs: JobStore,
    store: SceneStore,
    catalogue: Catalogue,
) -> Job:
    """Validate, rate-limit, create and enqueue. Returns the created job.

    Ordered deliberately: the cheap structural checks come first, then the
    rate limit, then anything that costs a network call or a row. A malformed
    submission should not consume an hour's job budget.
    """
    if not queue_connection.queue_available():
        raise errors.jobs_unavailable("no job queue is configured")

    spec = processes.get(process)
    if spec is None:
        raise errors.unknown_process(process, processes.names())

    if len(scene_ids) != spec.scene_count:
        raise errors.wrong_scene_count(spec.name, spec.scene_count, len(scene_ids))

    if spec.scene_count > 1 and len(set(scene_ids)) != len(scene_ids):
        raise errors.duplicate_scenes(scene_ids[0])

    # 5.4.4: "same index, same parameters on both sides -- enforce this in the
    # API, do not trust input." The index is the only parameter a change job
    # takes, and it applies to both dates by construction.
    if spec.name == "change":
        index = parameters.get("index", processes.DEFAULT_CHANGE_INDEX)
        if index not in processes.CHANGEABLE_INDICES:
            raise errors.unknown_index(str(index), list(processes.CHANGEABLE_INDICES))

    area_km2 = raster_utils.geometry_area_km2(aoi)
    if area_km2 > schemas.MAX_AOI_KM2:
        raise errors.aoi_too_large(area_km2, schemas.MAX_AOI_KM2)

    limiter, limiter_key = get_job_limiter(), client_key(http_request)
    allowed, retry_after = limiter.check(limiter_key)
    if not allowed:
        error = errors.BhoomiError(
            429, "rate_limited",
            f"Too many jobs. The limit is {schemas.JOB_RATE_LIMIT} per hour. "
            f"Try again in {retry_after} seconds.",
            retry_after=retry_after)
        error.headers = {"Retry-After": str(retry_after)}
        raise error

    # Everything past the charge is refunded if it does not produce a job.
    #
    # The budget in PLAN.md 8 bounds work performed, and a submission that was
    # refused performed none. It stays *charged first* rather than moved after
    # these checks, because what follows costs a catalogue round trip and a row
    # -- the charge is what stops an unbounded stream of those. The per-request
    # search budget still counts every attempt, refused or not, so nothing here
    # opens a way to hammer `resolve_scene` for free.
    try:
        # D3: one scene per analysis, and the AOI must fit inside it. Checked
        # before the job is created so the rejection is immediate rather than a
        # failure the user has to poll for.
        for scene_id in scene_ids:
            scene = resolve_scene(scene_id, store, catalogue)
            coverage = scene.aoi_coverage(aoi)
            if coverage < COVERAGE_TOLERANCE:
                raise errors.aoi_spans_scenes(coverage, scene_id)

        client_ip = http_request.client.host if http_request.client else None
        try:
            job = jobs.create(
                process=spec.name, aoi=aoi, aoi_area_km2=area_km2,
                scene_ids=scene_ids, parameters=parameters,
                client_ip=client_ip,
                max_global=schemas.MAX_CONCURRENT_JOBS,
                max_per_ip=schemas.MAX_CONCURRENT_JOBS_PER_IP,
            )
        except TooManyActiveJobs as exc:
            raise errors.too_many_active_jobs(exc.active, exc.limit, exc.scope) from None
        except JobsUnavailable as exc:
            raise errors.jobs_unavailable(str(exc)) from None

        queue = queue_connection.get_queue()
        try:
            queue.enqueue(run_job, str(job.id),
                          job_timeout=queue_connection.JOB_TIMEOUT_SECONDS,
                          result_ttl=queue_connection.RESULT_TTL_SECONDS)
        except Exception as exc:
            # The row exists but nothing will ever pick it up. Failing it now is
            # the difference between an error the user sees and a job that sits at
            # "queued" until they give up.
            logger.exception("enqueue failed for job %s", job.id)
            jobs.advance(job.id, JobStatus.FAILED,
                         error_message="The job could not be queued. Please retry.",
                         error_detail=repr(exc))
            raise errors.jobs_unavailable("the job queue could not be reached") from None
    except Exception:
        limiter.release(limiter_key)
        raise

    logger.info("job %s submitted: %s over %.1f km²", job.id, spec.name, area_km2)
    return job
