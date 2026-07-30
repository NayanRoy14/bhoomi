"""Redis connection and the RQ queue (PLAN.md 4.2, 9.1).

Like the database, Redis is configured by one environment variable and read at
call time so tests can change it. Unlike the scene cache, there is **no
degraded mode**: without a queue there is nowhere to put a job, and running it
inline in the request would reintroduce exactly the minutes-long HTTP request
that 4.2 exists to avoid. The API refuses submission instead.

`is_async=False` is offered for tests, which need the job body to run without a
worker process. It is deliberately not reachable from configuration -- an
operator who set it would get an API that blocks for the length of every job
while appearing to be queue-backed.
"""

from __future__ import annotations

import logging
import os
import threading

from redis import Redis
from rq import Queue

logger = logging.getLogger(__name__)

ENV_VAR = "BHOOMI_REDIS_URL"
QUEUE_NAME = "bhoomi"

#: PLAN.md 8: hard kill at 10 minutes, status `timed_out`. RQ raises
#: JobTimeoutException inside the job at this point, which the runner catches
#: and records -- that is why the value lives here and not only in the worker.
JOB_TIMEOUT_SECONDS = int(os.getenv("BHOOMI_JOB_TIMEOUT", "600"))

#: How long a finished job's RQ record survives. The `jobs` table is the source
#: of truth for status (6), so RQ's copy only needs to outlive debugging.
RESULT_TTL_SECONDS = 3600

_connections: dict[str, Redis] = {}
_lock = threading.Lock()


def redis_url() -> str | None:
    return os.getenv(ENV_VAR) or None


def get_redis() -> Redis | None:
    """The shared connection, or None when no queue is configured."""
    url = redis_url()
    if url is None:
        return None
    with _lock:
        conn = _connections.get(url)
        if conn is None:
            conn = Redis.from_url(url, socket_connect_timeout=5,
                                  socket_timeout=15, health_check_interval=30)
            _connections[url] = conn
        return conn


def get_queue() -> Queue | None:
    """The work queue, or None when no queue is configured."""
    conn = get_redis()
    if conn is None:
        return None
    return Queue(QUEUE_NAME, connection=conn, default_timeout=JOB_TIMEOUT_SECONDS)


def queue_available() -> bool:
    return redis_url() is not None


def reset_connections() -> None:
    """Drop cached connections. For tests that switch Redis mid-session."""
    with _lock:
        for conn in _connections.values():
            try:
                conn.close()
            except Exception:  # pragma: no cover - close is best effort
                pass
        _connections.clear()
