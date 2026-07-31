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


def _live_workers(connection) -> int:
    """Workers with an unexpired heartbeat key.

    Not `Worker.count()`, which reads RQ's `rq:workers:<queue>` set -- and that
    set undercounts. Every worker runs `clean_worker_registry` at startup, so
    replicas starting at the same instant race: one worker's cleanup can prune
    another's registration written moments earlier. Observed directly with two
    compose replicas, both logging "Listening on bhoomi" and both consuming
    jobs, while the set held one of them.

    Consuming does not depend on set membership -- workers block on the queue
    itself -- so the set being wrong costs nothing except this number. But this
    number is what an operator reads to decide whether the deployment is
    healthy, and "1 worker" when two are running invites chasing a phantom.

    The heartbeat keys are the thing RQ actually refreshes while a worker
    lives, and they expire on their own when it dies. `rq:worker:*` does not
    match the registry sets (`rq:workers`, `rq:workers:<queue>`), whose next
    character is "s" rather than ":".

    This trades a persistent undercount for a brief overcount, which is the
    better error: a stopped worker's key lingers until it expires, measured at
    ~10 s of TTL against ~435 s for a live one, so a restart shows the old and
    new workers together for a few seconds and then settles. Wrong for seconds
    and self-correcting beats wrong until the next deploy.
    """
    return sum(1 for _ in connection.scan_iter(match="rq:worker:*", count=100))


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
        return len(queue), _live_workers(queue.connection)
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
