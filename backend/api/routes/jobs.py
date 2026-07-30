"""Job submission and status — PLAN.md 7.3, 7.4, 7.5.

Every rejection here is a specific message with the numbers in it, per 7.3.
The one thing this module will not do is accept a job it cannot run: no
database or no queue means 503 at submission rather than a job id that will
never produce anything.
"""

from __future__ import annotations

import logging
import uuid

from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import FileResponse, RedirectResponse

from backend import storage
from backend.api import errors, schemas
from backend.api.deps import get_catalogue, get_job_store, get_scene_store
from backend.api.ratelimit import client_key, get_job_limiter
from backend.db import SceneStore
from backend.db.jobs import JobStatus, JobStore, JobsUnavailable, TooManyActiveJobs
from backend.queue import connection as queue_connection
from backend.queue import processes
from backend.queue.tasks import run_job
from backend.resolve import resolve_scene
from catalogue import Catalogue
from processing import raster_utils

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/jobs", tags=["jobs"])

#: An AOI computed to be inside a footprint can land a hair under 1.0 through
#: floating point alone. 0.999 of a 500 km² AOI is half a square kilometre --
#: below any real scene-boundary crossing, which loses whole percentages.
COVERAGE_TOLERANCE = 0.999


@router.post("", response_model=schemas.JobCreateResponse, status_code=202,
             summary="Submit a processing job")
def create_job(
    request: schemas.JobCreateRequest,
    http_request: Request,
    response: Response,
    jobs: JobStore = Depends(get_job_store),
    store: SceneStore = Depends(get_scene_store),
    catalogue: Catalogue = Depends(get_catalogue),
) -> schemas.JobCreateResponse:
    if not queue_connection.queue_available():
        raise errors.jobs_unavailable("no job queue is configured")

    spec = processes.get(request.process)
    if spec is None:
        raise errors.unknown_process(request.process, processes.names())

    if len(request.scene_ids) != spec.scene_count:
        raise errors.wrong_scene_count(spec.name, spec.scene_count,
                                       len(request.scene_ids))

    aoi = request.aoi.as_dict()
    area_km2 = raster_utils.geometry_area_km2(aoi)
    if area_km2 > schemas.MAX_AOI_KM2:
        raise errors.aoi_too_large(area_km2, schemas.MAX_AOI_KM2)

    # Rate limited here, after the cheap structural checks and before anything
    # that costs a network call or a row. A malformed submission should not
    # consume an hour's job budget; the global 120/hour middleware already
    # bounds how fast those can be sent.
    allowed, retry_after = get_job_limiter().check(client_key(http_request))
    if not allowed:
        error = errors.BhoomiError(
            429, "rate_limited",
            f"Too many jobs. The limit is {schemas.JOB_RATE_LIMIT} per hour. "
            f"Try again in {retry_after} seconds.",
            retry_after=retry_after)
        error.headers = {"Retry-After": str(retry_after)}
        raise error

    # D3: one scene per analysis, and the AOI must fit inside it. Checked
    # before the job is created so the rejection is immediate rather than a
    # failure the user has to poll for.
    for scene_id in request.scene_ids:
        scene = resolve_scene(scene_id, store, catalogue)
        coverage = scene.aoi_coverage(aoi)
        if coverage < COVERAGE_TOLERANCE:
            raise errors.aoi_spans_scenes(coverage, scene_id)

    client_ip = http_request.client.host if http_request.client else None
    try:
        job = jobs.create(
            process=spec.name, aoi=aoi, aoi_area_km2=area_km2,
            scene_ids=request.scene_ids, parameters=request.parameters,
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

    position = jobs.position_in_queue(job.id)
    logger.info("job %s submitted: %s over %.1f km², position %d",
                job.id, spec.name, area_km2, position)

    response.headers["Location"] = f"/api/v1/jobs/{job.id}"
    return schemas.JobCreateResponse(
        job_id=str(job.id),
        status=job.status.value,
        position_in_queue=position,
        estimated_seconds=processes.estimate_for(spec, area_km2),
        links=[
            schemas.Link(rel="status", href=f"/api/v1/jobs/{job.id}"),
            schemas.Link(rel="result", href=f"/api/v1/jobs/{job.id}/result"),
        ],
    )


def _load(job_id: str, jobs: JobStore):
    """Fetch a job, treating a malformed id as absent rather than as a 422.

    A client following a stale or hand-edited link should get "no such job",
    which is true and actionable, not a schema complaint about UUID format.
    """
    try:
        uuid.UUID(job_id)
    except ValueError:
        raise errors.job_not_found(job_id) from None
    job = jobs.get(job_id)
    if job is None:
        raise errors.job_not_found(job_id)
    return job


@router.get("/{job_id}", response_model=schemas.JobStatusResponse,
            summary="Job status and progress")
def job_status(job_id: str, jobs: JobStore = Depends(get_job_store)
               ) -> schemas.JobStatusResponse:
    job = _load(job_id, jobs)
    return schemas.JobStatusResponse(
        job_id=str(job.id), process=job.process, status=job.status.value,
        progress=job.progress, message=job.message,
        error_message=job.error_message, created_at=job.created_at,
        started_at=job.started_at, completed_at=job.completed_at,
    )


@router.get("/{job_id}/result", response_model=schemas.JobResultResponse,
            summary="Job outputs, once it has completed")
def job_result(job_id: str, jobs: JobStore = Depends(get_job_store)
               ) -> schemas.JobResultResponse:
    job = _load(job_id, jobs)

    if not job.is_terminal:
        raise errors.result_not_ready(str(job.id), job.status.value, job.progress)
    if job.status is not JobStatus.COMPLETED:
        # Terminal but unsuccessful: 409, so a polling client stops rather than
        # retrying a 404 that will never change.
        raise errors.job_failed(str(job.id), job.status.value, job.error_message)

    outputs = jobs.outputs_for(job.id)
    return schemas.JobResultResponse(
        job_id=str(job.id),
        outputs=[
            schemas.OutputOut(
                type=o.output_type, cog=o.cog_uri,
                download=f"/api/v1/jobs/{job.id}/download",
                bounds=list(_bounds_of(o.bounds)), crs=o.crs,
                resolution_m=o.resolution_m, valid_fraction=o.valid_fraction,
                stats=o.stats, expires_at=o.expires_at,
            )
            for o in outputs
        ],
    )


@router.get("/{job_id}/download", summary="Download the output COG",
            response_class=FileResponse)
def job_download(job_id: str, jobs: JobStore = Depends(get_job_store)):
    """Serve the raster itself (PLAN.md 7.5).

    Only meaningful for a storage backend with no public URL of its own -- the
    local filesystem one. When O4 resolves and outputs live in R2, `url_for`
    returns a real URL, `cog_uri` points straight at it, and this route
    redirects rather than streaming bytes through the API.
    """
    job = _load(job_id, jobs)
    if job.status is not JobStatus.COMPLETED:
        raise errors.result_not_ready(str(job.id), job.status.value, job.progress)

    key = storage.key_for(str(job.id))
    backend = storage.get_storage()

    direct = backend.url_for(key)
    if direct:
        return RedirectResponse(direct, status_code=307)

    path = backend.local_path(key)
    if path is None:
        # Completed, but the file is gone: past its 30-day retention (6), or
        # the worker wrote to a filesystem this process cannot see. Saying so
        # beats a 500, which would suggest retrying might help.
        raise errors.output_missing(str(job.id))

    return FileResponse(path, media_type="image/tiff",
                        filename=f"bhoomi_{job.process}_{job.id}.tif")


def _bounds_of(geometry: dict) -> tuple[float, float, float, float]:
    coords = [c for ring in geometry.get("coordinates", []) for c in ring]
    xs = [c[0] for c in coords]
    ys = [c[1] for c in coords]
    return min(xs), min(ys), max(xs), max(ys)
