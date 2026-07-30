"""Health check -- GET /health (PLAN.md 7.1)."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends

from backend.api import schemas
from backend.api.deps import get_catalogue
from backend.queue import connection as queue_connection
from catalogue import Catalogue
from processing import __version__

logger = logging.getLogger(__name__)
router = APIRouter(tags=["health"])


def _queue_stats() -> tuple[int | None, int | None]:
    """(depth, workers), or (None, None) when there is no queue or it is down.

    Never raises. This endpoint is what tells an orchestrator whether to
    restart the container, so an unreachable Redis must not be able to turn a
    healthy API into a failing health check -- scene search does not need the
    queue at all.
    """
    queue = queue_connection.get_queue()
    if queue is None:
        return None, None
    try:
        from rq import Worker
        return len(queue), Worker.count(queue=queue)
    except Exception as exc:
        logger.warning("queue stats unavailable: %s", exc)
        return None, None


@router.get("/health", response_model=schemas.HealthResponse,
            summary="Liveness and configuration check")
def health(catalogue: Catalogue = Depends(get_catalogue)) -> schemas.HealthResponse:
    """Does not call the upstream catalogue.

    A health check that depends on a third party reports someone else's outage
    as our own, and orchestrators restart containers over it.
    """
    depth, workers = _queue_stats()
    return schemas.HealthResponse(
        status="ok",
        version=__version__,
        catalogue=catalogue.name,
        queue_depth=depth,
        workers=workers,
    )
