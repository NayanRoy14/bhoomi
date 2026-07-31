"""The periodic housekeeping PLAN.md specifies and nothing was running.

Three obligations, all written down and none of them wired to a clock until
this module existed:

  1. **Retention** (PLAN.md 6): "anonymous job outputs expire after 30 days. A
     nightly task deletes expired object-storage keys and their rows." Every
     piece was in place -- `expires_at` written by `processes._publish`,
     `Storage.delete` on all three backends -- with nothing calling them, so
     outputs accumulated forever.
  2. **Stalled jobs** (`JobStore.reap_stalled`): written on 2026-07-31 after a
     job died inside a GDAL read and stayed at `reading` permanently, holding
     its submitter's one-job cap (PLAN.md 8) shut with no way to reopen it.
     Also never called, so that bug was still live.
  3. **Client IP purge** (PLAN.md 6, 14): promised publicly, honoured nowhere.

## Why a thread rather than cron or a scheduler

The obvious answers do not fit the deployment. `rq-scheduler` is a dependency
and a second process; a container cron needs a cron daemon in an image built
around one Python process; Render's own cron jobs are a paid feature and the
free tier is what `render.yaml` targets. A daemon thread beside the worker
costs nothing, dies with the process, and needs no infrastructure.

It runs in the *worker*, not the API, for a reason worth stating: on Render
both live in one container (`backend/render-start.sh`) and it would be tempting
to hang it off FastAPI's lifespan. Everywhere else the API runs several
replicas behind a load balancer and the worker is a singleton, so putting it in
the API means N copies of a delete loop racing each other.

## Why the interval is not "nightly"

A free Render service is stopped after 15 minutes without inbound HTTP, and a
stopped service has no thread. A literal 24-hour timer would, in practice,
almost never fire: the container rarely lives that long. So the sweep is due
by wall-clock age rather than by tick, runs once at startup, and re-checks
hourly -- a service that wakes up, sweeps, and sleeps again converges on the
same result as a nightly cron, without depending on uptime it does not have.

`expires_at` is an absolute timestamp in the database, so nothing is
double-deleted or skipped by an irregular schedule; the worst an unlucky
schedule costs is that an object outlives its 30 days by a few hours.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from dataclasses import dataclass

from backend import storage
from backend.db import get_engine
from backend.db.jobs import JobStore

logger = logging.getLogger(__name__)

#: How long a job may sit in an active state before the reaper fails it.
#: Comfortably above BHOOMI_JOB_TIMEOUT (900 s on Render, 600 s by default) so
#: that a job which is merely slow is never reaped out from under a worker that
#: is still going to report on it properly.
STALLED_AFTER_SECONDS = int(os.getenv("BHOOMI_STALLED_AFTER", "1800"))

#: PLAN.md 6 and 14. Separate from output retention on purpose -- see
#: `JobStore.purge_client_ips`.
IP_RETENTION_DAYS = int(os.getenv("BHOOMI_IP_RETENTION_DAYS", "30"))

#: How often the thread wakes to see whether a sweep is due.
SWEEP_INTERVAL_SECONDS = int(os.getenv("BHOOMI_SWEEP_INTERVAL", "3600"))

#: Cap per sweep. A backlog is drained over several passes rather than in one
#: transaction holding hundreds of deletes open while the worker wants the
#: connection for a job.
MAX_JOBS_PER_SWEEP = int(os.getenv("BHOOMI_SWEEP_BATCH", "200"))


@dataclass(frozen=True)
class SweepResult:
    """What one pass did. Returned rather than only logged so tests can assert."""

    expired_jobs: int = 0
    objects_deleted: int = 0
    rows_deleted: int = 0
    jobs_reaped: int = 0
    ips_purged: int = 0

    @property
    def did_anything(self) -> bool:
        return any((self.expired_jobs, self.jobs_reaped, self.ips_purged))


def sweep(store: JobStore | None = None) -> SweepResult:
    """One maintenance pass. Safe to call concurrently and safe to repeat.

    **Objects before rows, deliberately.** The two orders fail differently and
    only one failure is recoverable. Delete the row first and a crash strands
    an object whose key nothing records any more -- unreachable, unbilled to any
    job, and reclaimable only by listing the whole bucket against the whole
    table. Delete the object first and a crash leaves a row pointing at
    something that is gone, which is a state the API already handles: 7.5 answers
    it with 410 `output_missing`, and the next sweep finishes the job.

    So this leans on the asymmetry rather than pretending the two steps are
    atomic. They cannot be -- one is S3 and one is Postgres.
    """
    if store is None:
        if get_engine() is None:
            return SweepResult()
        store = JobStore()

    result = SweepResult(
        jobs_reaped=_reap(store),
        ips_purged=_purge_ips(store),
    )

    job_ids = store.expired_jobs(limit=MAX_JOBS_PER_SWEEP)
    if not job_ids:
        return result

    backend = storage.get_storage()
    objects = 0
    reclaimed: list[str] = []
    for job_id in job_ids:
        try:
            objects += backend.delete_prefix(f"{job_id}.")
        except Exception:
            # One unreachable object must not stop the sweep: the next job's
            # prefix may well delete fine, and this one is retried next pass.
            # Not downgraded to a warning -- a store that cannot be deleted
            # from is how a bill grows quietly.
            logger.exception("could not delete stored outputs for job %s", job_id)
            continue
        reclaimed.append(job_id)

    rows = store.expire_outputs(reclaimed)
    result = SweepResult(
        expired_jobs=len(reclaimed), objects_deleted=objects, rows_deleted=rows,
        jobs_reaped=result.jobs_reaped, ips_purged=result.ips_purged,
    )
    logger.info("retention: expired %d job(s), %d object(s), %d row(s)",
                len(reclaimed), objects, rows)
    return result


def _reap(store: JobStore) -> int:
    try:
        return store.reap_stalled(STALLED_AFTER_SECONDS)
    except Exception:
        logger.exception("reaping stalled jobs failed")
        return 0


def _purge_ips(store: JobStore) -> int:
    try:
        return store.purge_client_ips(IP_RETENTION_DAYS)
    except Exception:
        logger.exception("purging client IPs failed")
        return 0


def start(interval: int = SWEEP_INTERVAL_SECONDS) -> threading.Thread | None:
    """Run `sweep` now and every `interval` seconds, in a daemon thread.

    Returns None when there is no database, which is the development default
    and not an error -- there is nothing to sweep and no rows to reap.

    A daemon thread so it cannot keep a shutting-down worker alive: a sweep
    holds no lock a half-finished pass would leak, and the deletes are
    idempotent, so being killed mid-pass costs nothing but the pass.
    """
    if get_engine() is None:
        logger.info("no database; retention sweep not started")
        return None

    def loop() -> None:
        while True:
            try:
                sweep()
            except Exception:
                # The thread outliving one bad sweep is the whole point. An
                # unhandled exception here would end maintenance silently for
                # the life of the container -- which is exactly the failure
                # this module exists to fix, reintroduced one level up.
                logger.exception("maintenance sweep failed; retrying in %ds", interval)
            time.sleep(interval)

    thread = threading.Thread(target=loop, name="bhoomi-maintenance", daemon=True)
    thread.start()
    logger.info("maintenance sweep every %ds (retention, reaper, IP purge)", interval)
    return thread
