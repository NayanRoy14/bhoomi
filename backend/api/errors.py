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
