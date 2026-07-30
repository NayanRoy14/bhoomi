"""Error responses that say what to do about it.

PLAN.md 7.3 requires specific messages, never a generic failure. A user who
draws too large an AOI should be told the size they drew and the limit, not
"400 Bad Request".
"""

from __future__ import annotations

from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse

from catalogue import CatalogueError, SceneNotFoundError


class BhoomiError(HTTPException):
    """An HTTPException carrying a machine-readable code alongside the message."""

    def __init__(self, status_code: int, code: str, message: str, **extra) -> None:
        super().__init__(status_code=status_code,
                         detail={"code": code, "message": message, **extra})


def aoi_too_large(area_km2: float, limit_km2: float) -> BhoomiError:
    return BhoomiError(
        400, "aoi_too_large",
        f"AOI is {area_km2:,.0f} km²; the maximum is {limit_km2:,.0f} km². "
        "Draw a smaller area.",
        area_km2=round(area_km2, 1), limit_km2=limit_km2)


def aoi_spans_scenes(coverage: float, scene_id: str) -> BhoomiError:
    return BhoomiError(
        400, "aoi_spans_scenes",
        f"This AOI is only {coverage:.0%} inside scene {scene_id}. Bhoomi processes "
        "one scene at a time — reduce the AOI, or pick a scene that fully contains it.",
        coverage=round(coverage, 4), scene_id=scene_id)


def date_range_too_long(days: int, limit_days: int) -> BhoomiError:
    return BhoomiError(
        400, "date_range_too_long",
        f"Date range spans {days} days; the maximum is {limit_days}. "
        "Narrow the range.",
        days=days, limit_days=limit_days)


def invalid_date_range(start: str, end: str) -> BhoomiError:
    return BhoomiError(
        400, "invalid_date_range",
        f"start_date ({start}) is after end_date ({end}).")


def unknown_process(process: str, available: list[str]) -> BhoomiError:
    return BhoomiError(
        400, "unknown_process",
        f"Unknown process {process!r}. Available: {', '.join(sorted(available))}.",
        available=sorted(available))


def wrong_scene_count(process: str, expected: int, got: int) -> BhoomiError:
    return BhoomiError(
        400, "wrong_scene_count",
        f"Process {process!r} requires exactly {expected} "
        f"scene{'s' if expected != 1 else ''}; got {got}.",
        expected=expected, got=got)


def jobs_unavailable(reason: str) -> BhoomiError:
    """503, not 500: the deployment is incomplete, the request was fine.

    Says which dependency is missing because the only person who can act on
    this is whoever is running the deployment.
    """
    return BhoomiError(
        503, "jobs_unavailable",
        f"Job submission is not available: {reason}. Scene search still works.")


def too_many_active_jobs(active: int, limit: int, scope: str) -> BhoomiError:
    """429 with Retry-After, per PLAN.md 8's concurrency caps."""
    detail = ("You already have a job running. Wait for it to finish."
              if scope == "client" else
              f"All {limit} worker slots are busy. Try again shortly.")
    error = BhoomiError(
        429, "too_many_active_jobs", detail, active=active, limit=limit, scope=scope)
    # Half the fake job's runtime: long enough not to hammer, short enough that
    # a polite client is not left waiting well past the slot freeing up.
    error.headers = {"Retry-After": "5"}
    return error


def job_not_found(job_id: str) -> BhoomiError:
    return BhoomiError(404, "job_not_found", f"No job {job_id}.")


def result_not_ready(job_id: str, status: str, progress: int) -> BhoomiError:
    """404 while incomplete, carrying the current status (PLAN.md 7.5)."""
    return BhoomiError(
        404, "result_not_ready",
        f"Job {job_id} is {status} ({progress}%); no result yet.",
        status=status, progress=progress)


def job_failed(job_id: str, status: str, message: str | None) -> BhoomiError:
    """A finished job with nothing to serve. 409: it will never be ready.

    Distinct from result_not_ready so a polling client can stop rather than
    retry forever.
    """
    return BhoomiError(
        409, "job_did_not_complete",
        f"Job {job_id} {status.replace('_', ' ')}"
        + (f": {message}" if message else "."),
        status=status)


def output_missing(job_id: str) -> BhoomiError:
    """410, not 404: it existed. Retrying will not bring it back.

    Either the 30-day retention (PLAN.md 6) has passed, or the worker wrote to
    a filesystem this process cannot read -- the failure mode that object
    storage removes and that LocalStorage cannot.
    """
    return BhoomiError(
        410, "output_missing",
        f"The output for job {job_id} is no longer available. Outputs are kept "
        "for 30 days; submit the job again to regenerate it.")


async def catalogue_error_handler(request: Request, exc: CatalogueError) -> JSONResponse:
    """Upstream catalogue failures become 502/404, never an opaque 500."""
    if isinstance(exc, SceneNotFoundError):
        return JSONResponse(
            status_code=404,
            content={"code": "scene_not_found", "message": str(exc)})
    return JSONResponse(
        status_code=502,
        content={"code": "catalogue_unavailable",
                 "message": f"The satellite catalogue could not be reached: {exc}"})
