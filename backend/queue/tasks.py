"""The function RQ actually runs.

One entry point for every process, so the state machine is written once. The
process itself only reports which stage it has reached; entering the terminal
state is not its job, which keeps a process implementation from being able to
mark itself completed after failing halfway.

The worker is a separate process from the API, so nothing here may assume any
in-process state: the job id is the entire argument, and everything else is
read back from the database.
"""

from __future__ import annotations

import logging
import traceback

from rq.timeouts import JobTimeoutException

from backend.db.jobs import IllegalTransition, JobStatus, JobStore
from backend.queue import processes

logger = logging.getLogger(__name__)

#: What a user is told when something we did not anticipate went wrong. The
#: traceback goes to error_detail, which the API never serves (PLAN.md 4.3).
GENERIC_FAILURE = "Processing failed. This has been logged."


def run_job(job_id: str) -> str:
    """Execute one job, recording every transition. Returns the final status."""
    store = JobStore()
    job = store.get(job_id)

    if job is None:
        # Nothing to fail: the row is the job, and there is no row.
        logger.error("job %s was enqueued but has no row; dropping", job_id)
        return "missing"

    if job.is_terminal:
        # Cancelled while queued, or a duplicate delivery of the same message.
        logger.info("job %s is already %s; not running", job_id, job.status.value)
        return job.status.value

    spec = processes.get(job.process)
    if spec is None:
        # Reachable if a process is removed between submission and execution.
        _fail(store, job_id, f"Unknown process {job.process!r}.",
              f"process {job.process!r} not in registry {processes.names()}")
        return JobStatus.FAILED.value

    def report(status: JobStatus) -> None:
        store.advance(job_id, status)

    try:
        spec.run(report, job)
    except JobTimeoutException:
        # RQ's death penalty fires inside the job, so this is the one chance to
        # record it. PLAN.md 8 wants `timed_out`, not a generic failure -- the
        # two mean different things to whoever reads the status.
        logger.warning("job %s exceeded its timeout", job_id)
        _terminate(store, job_id, JobStatus.TIMED_OUT,
                   "Job exceeded the 10 minute limit and was stopped.", None)
        raise
    except Exception as exc:
        logger.exception("job %s failed", job_id)
        _fail(store, job_id, GENERIC_FAILURE, traceback.format_exc())
        raise

    store.advance(job_id, JobStatus.COMPLETED)
    logger.info("job %s completed", job_id)
    return JobStatus.COMPLETED.value


def _fail(store: JobStore, job_id: str, message: str, detail: str | None) -> None:
    _terminate(store, job_id, JobStatus.FAILED, message, detail)


def _terminate(store: JobStore, job_id: str, status: JobStatus,
               message: str, detail: str | None) -> None:
    """Record a terminal state, never raising on top of the original problem.

    If the job has already reached a terminal state -- cancelled underneath us,
    say -- `advance` refuses, and that refusal must not replace the failure
    being reported with a confusing second one.
    """
    try:
        store.advance(job_id, status, error_message=message, error_detail=detail)
    except IllegalTransition as exc:
        logger.warning("could not mark job %s as %s: %s", job_id, status.value, exc)
