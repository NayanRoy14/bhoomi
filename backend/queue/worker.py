"""Worker entry point:  python -m backend.queue.worker

Runs in its own container (PLAN.md 9.1). Concurrency comes from running more
than one of these, not from threads inside one -- rasterio releases the GIL
unevenly and the memory ceiling in 8 is per process, which only means anything
if a process handles one job at a time.
"""

from __future__ import annotations

import logging
import os
import sys

from rq import SimpleWorker, Worker

from backend.queue.connection import QUEUE_NAME, get_queue

logger = logging.getLogger(__name__)


def worker_class():
    """`Worker` where fork exists, `SimpleWorker` where it does not.

    The default RQ worker runs each job in a forked child, which is what makes
    a hung job killable and keeps a crashing job from taking the worker with
    it. That is the deployment shape (Linux containers, PLAN.md 9.1) and stays
    the default.

    Windows has no `os.fork`, so the default worker cannot start there at all.
    Falling back to SimpleWorker means the queue can be run natively on the dev
    machine instead of only inside Docker. The tradeoff is real and worth
    stating: SimpleWorker executes jobs in its own process, so a segfault in
    GDAL takes the worker down with the job, and the timeout is enforced by a
    timer rather than by killing a child. Fine for development, which is the
    only place it is selected.
    """
    if hasattr(os, "fork"):
        return Worker
    logger.warning("no os.fork on this platform; using SimpleWorker "
                   "(jobs run in-process -- development only)")
    return SimpleWorker


def main() -> int:
    logging.basicConfig(
        level=os.getenv("BHOOMI_LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    )

    queue = get_queue()
    if queue is None:
        # Exit rather than idle: a worker with nowhere to listen is a container
        # that looks healthy while doing nothing, which is worse than a crash
        # loop that shows up in `docker compose ps`.
        logger.error("BHOOMI_REDIS_URL is not set; a worker has nothing to listen to")
        return 1

    cls = worker_class()
    logger.info("worker listening on %r (%s)", QUEUE_NAME, cls.__name__)
    cls([queue], connection=queue.connection).work(
        with_scheduler=False,
        burst=bool(os.getenv("BHOOMI_WORKER_BURST")),
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
