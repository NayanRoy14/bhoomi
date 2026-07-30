"""Job queue (PLAN.md 4.2, 9.1).

Named `queue` per PLAN.md 10, which shadows the stdlib module of that name for
anything doing a *relative* import inside this package. Everything here uses
absolute imports, so `backend.queue` and `queue` stay distinct.
"""

from backend.queue.connection import (
    JOB_TIMEOUT_SECONDS,
    QUEUE_NAME,
    get_queue,
    get_redis,
    queue_available,
    redis_url,
    reset_connections,
)
from backend.queue.processes import ProcessSpec, estimate_for
from backend.queue.tasks import run_job

__all__ = [
    "JOB_TIMEOUT_SECONDS",
    "QUEUE_NAME",
    "ProcessSpec",
    "estimate_for",
    "get_queue",
    "get_redis",
    "queue_available",
    "redis_url",
    "reset_connections",
    "run_job",
]
