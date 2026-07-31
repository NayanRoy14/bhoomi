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
from fastapi.responses import FileResponse, RedirectResponse, StreamingResponse

from backend import storage, tiles
from backend.api import errors, schemas
from backend.api.deps import get_catalogue, get_job_store, get_scene_store
from backend.api.submit import submit_job
from backend.db import SceneStore
from backend.db.jobs import JobStatus, JobStore
from backend.queue import processes
from catalogue import Catalogue

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/jobs", tags=["jobs"])


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
    """Submit a job (7.3).

    Every check lives in `submit_job`, shared with the OGC facade (7.6), so a
    job cannot be validated one way here and another way there.
    """
    job = submit_job(
        process=request.process, aoi=request.aoi.as_dict(),
        scene_ids=request.scene_ids, parameters=request.parameters,
        http_request=http_request, jobs=jobs, store=store, catalogue=catalogue,
    )
    position = jobs.position_in_queue(job.id)

    response.headers["Location"] = f"/api/v1/jobs/{job.id}"
    return schemas.JobCreateResponse(
        job_id=str(job.id),
        status=job.status.value,
        position_in_queue=position,
        estimated_seconds=processes.estimate_for(
            processes.get(job.process), job.aoi_area_km2),
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
    backend = storage.get_storage()

    def described(o) -> schemas.OutputOut:
        # Built here rather than stored on the row: the tile server's address
        # and the colour ramp are deployment configuration, and baking them
        # into a 30-day-lived row would mean old outputs pointing at a tile
        # server that has since moved.
        variant = _variant_of(o.output_type)
        source = backend.tile_source(storage.key_for(str(job.id), variant))
        download = f"/api/v1/jobs/{job.id}/download"
        return schemas.OutputOut(
            type=o.output_type, cog=o.cog_uri,
            download=download if variant is None else f"{download}?output={variant}",
            bounds=list(_bounds_of(o.bounds)), crs=o.crs,
            resolution_m=o.resolution_m, valid_fraction=o.valid_fraction,
            stats=o.stats, expires_at=o.expires_at,
            tiles=tiles.tiles_url(tiles.render_key(job.process, o.output_type), source),
            warnings=o.warnings,
        )

    return schemas.JobResultResponse(
        job_id=str(job.id),
        outputs=[described(o) for o in outputs],
    )


#: Which storage variants a download may name. Closed rather than free-form:
#: the value reaches `key_for`, and an unbounded one there is a path the caller
#: chooses.
_DOWNLOADABLE_VARIANTS = {"earlier", "later"}


def _variant_of(output_type: str) -> str | None:
    """The storage variant an output row was written under, or None if primary."""
    for prefix in ("earlier_", "later_"):
        if output_type.startswith(prefix):
            return prefix.rstrip("_")
    return None


@router.get("/{job_id}/download", summary="Download the output COG",
            response_class=FileResponse)
def job_download(job_id: str, output: str | None = None,
                 jobs: JobStore = Depends(get_job_store)):
    """Serve the raster itself (PLAN.md 7.5).

    Three routes to the same bytes, cheapest first: redirect to a public URL if
    the backend has one, serve the file directly if it is on this filesystem,
    otherwise stream it out of object storage. A private R2 bucket takes the
    third -- it has no stable public URL by design, because a presigned one
    would expire inside `outputs.cog_uri` (see `Storage.url_for`).

    `?output=earlier|later` selects one of a change job's two per-date rasters.
    Omitted, it serves the job's primary output, which is what every existing
    link means.
    """
    job = _load(job_id, jobs)
    if job.status is not JobStatus.COMPLETED:
        raise errors.result_not_ready(str(job.id), job.status.value, job.progress)

    if output is not None and output not in _DOWNLOADABLE_VARIANTS:
        raise errors.unknown_output(str(job.id), output,
                                    sorted(_DOWNLOADABLE_VARIANTS))

    key = storage.key_for(str(job.id), output)
    backend = storage.get_storage()
    suffix = f"_{output}" if output else ""
    filename = f"bhoomi_{job.process}{suffix}_{job.id}.tif"

    direct = backend.url_for(key)
    if direct:
        return RedirectResponse(direct, status_code=307)

    path = backend.local_path(key)
    if path is not None:
        return FileResponse(path, media_type="image/tiff", filename=filename)

    stream = backend.open_stream(key)
    if stream is None:
        # Completed, but the object is gone: past its 30-day retention (6), or
        # written somewhere this process cannot reach. Saying so beats a 500,
        # which would suggest retrying might help.
        raise errors.output_missing(str(job.id))

    return StreamingResponse(
        stream, media_type="image/tiff",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


def _bounds_of(geometry: dict) -> tuple[float, float, float, float]:
    coords = [c for ring in geometry.get("coordinates", []) for c in ring]
    xs = [c[0] for c in coords]
    ys = [c[1] for c in coords]
    return min(xs), min(ys), max(xs), max(ys)
