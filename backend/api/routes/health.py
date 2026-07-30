"""Health check -- GET /health (PLAN.md 7.1)."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from backend.api import schemas
from backend.api.deps import get_catalogue
from catalogue import Catalogue
from processing import __version__

router = APIRouter(tags=["health"])


@router.get("/health", response_model=schemas.HealthResponse,
            summary="Liveness and configuration check")
def health(catalogue: Catalogue = Depends(get_catalogue)) -> schemas.HealthResponse:
    """Does not call the upstream catalogue.

    A health check that depends on a third party reports someone else's outage
    as our own, and orchestrators restart containers over it.
    """
    return schemas.HealthResponse(
        status="ok",
        version=__version__,
        catalogue=catalogue.name,
        queue_depth=None,   # January 2027
        workers=None,
    )
