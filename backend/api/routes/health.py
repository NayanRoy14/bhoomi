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

    This trades a persistent undercount for a temporary overcount, which is the
    better error: the key expires on its own, so the number settles without
    anyone intervening. Wrong for a while and self-correcting beats wrong until
    the next deploy.

    **How long "temporary" is depends on how the old worker went away, and the
    two cases are far apart.** A clean local restart is quick: the worker exits
    gracefully, RQ shortens the key's TTL, and it clears in ~10 s against the
    ~435 s a live worker refreshes. A platform deploy is not, because the old
    instance is not stopped -- Render keeps it serving until the new one passes
    its health check, so for the length of that overlap *both* workers really
    are alive and both are heartbeating. The count is then correct rather than
    wrong, and it stays high until the old instance is drained and its key runs
    out the live TTL.

    Observed on the Render free tier on 2026-08-01, after the blueprint sync
    that added the tile service: `workers: 2` across four checks where
    render-start.sh starts one, settling to 1 later. Sampling was too sparse to
    time it -- somewhere between fifteen and forty minutes -- so treat this as
    "minutes, not seconds" rather than as a figure.

    The operational consequence is the point: on a hosted deploy, give this
    number tens of minutes to settle before reading it as a fault. Two workers
    on a 512 MB container would be worth chasing; two workers because a deploy
    has not finished draining is not.
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
