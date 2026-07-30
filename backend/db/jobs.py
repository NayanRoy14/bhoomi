"""Job records and the state machine that moves them (PLAN.md 4.3, 6).

## Why there is no NullJobStore

`scenes` has one, because a cache that always misses is still correct. Jobs are
the opposite: the row *is* the job. A null store would accept a submission,
return a job id, and lose it — the client would then poll a 404 forever for
work that was never going to happen. So when no database is configured the API
refuses job submission outright (503), which is a worse user experience and an
honest one. Scene search keeps working, because it does not need this table.

## Why transitions are checked

The status column is what the API reports and what the frontend polls. A
worker that wrote `processing` after `completed` — a retry landing after a
success, say — would resurrect a finished job and leave it stuck, because
nothing would ever move it again. `advance` therefore refuses any transition
the machine in 4.3 does not allow, and refuses every transition out of a
terminal state. The check is in SQL, in the UPDATE's WHERE clause, so two
workers racing cannot both win.
"""

from __future__ import annotations

import ipaddress
import json
import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Iterable

from sqlalchemy import text

from backend.db.engine import get_engine

logger = logging.getLogger(__name__)


class JobStatus(str, Enum):
    """Mirrors the `job_status` SQL enum. Values are the wire format too."""

    QUEUED = "queued"
    SEARCHING = "searching"
    READING = "reading"
    PROCESSING = "processing"
    WRITING_COG = "writing_cog"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    TIMED_OUT = "timed_out"


TERMINAL = frozenset({JobStatus.COMPLETED, JobStatus.FAILED,
                      JobStatus.CANCELLED, JobStatus.TIMED_OUT})

#: Any active state may end in one of these -- a failure, a cancellation or the
#: 10-minute timeout (PLAN.md 8) can arrive at any point.
_ABORTS = frozenset({JobStatus.FAILED, JobStatus.CANCELLED, JobStatus.TIMED_OUT})

#: The happy path of 4.3. Each key may also move to any of _ABORTS.
_NEXT: dict[JobStatus, frozenset[JobStatus]] = {
    JobStatus.QUEUED: frozenset({JobStatus.SEARCHING}),
    JobStatus.SEARCHING: frozenset({JobStatus.READING}),
    JobStatus.READING: frozenset({JobStatus.PROCESSING}),
    JobStatus.PROCESSING: frozenset({JobStatus.WRITING_COG}),
    JobStatus.WRITING_COG: frozenset({JobStatus.COMPLETED}),
}

#: Progress written on entering each state (4.3: "each transition writes
#: progress"). Failure states keep whatever progress they had reached, which is
#: more informative than resetting to 0 or jumping to 100.
PROGRESS: dict[JobStatus, int] = {
    JobStatus.QUEUED: 0,
    JobStatus.SEARCHING: 10,
    JobStatus.READING: 30,
    JobStatus.PROCESSING: 60,
    JobStatus.WRITING_COG: 85,
    JobStatus.COMPLETED: 100,
}

#: Human-readable status for 7.4's `message`. Derived rather than stored: 6 has
#: no such column, and a message that is a pure function of status does not
#: need one.
MESSAGES: dict[JobStatus, str] = {
    JobStatus.QUEUED: "Waiting for a worker",
    JobStatus.SEARCHING: "Resolving scenes",
    JobStatus.READING: "Reading bands",
    JobStatus.PROCESSING: "Computing index",
    JobStatus.WRITING_COG: "Writing the output COG",
    JobStatus.COMPLETED: "Done",
    JobStatus.FAILED: "Failed",
    JobStatus.CANCELLED: "Cancelled",
    JobStatus.TIMED_OUT: "Timed out",
}


def allowed_transitions(current: JobStatus) -> frozenset[JobStatus]:
    """States reachable from `current`. Empty once terminal."""
    if current in TERMINAL:
        return frozenset()
    return _NEXT.get(current, frozenset()) | _ABORTS


class IllegalTransition(RuntimeError):
    """A transition the 4.3 machine does not permit, or one that lost a race."""


class JobsUnavailable(RuntimeError):
    """Job submission was attempted with no database configured."""


@dataclass(frozen=True)
class Job:
    id: uuid.UUID
    process: str
    status: JobStatus
    progress: int
    aoi: dict
    aoi_area_km2: float
    scene_ids: list[str]
    parameters: dict = field(default_factory=dict)
    error_message: str | None = None
    created_at: datetime | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None

    @property
    def is_terminal(self) -> bool:
        return self.status in TERMINAL

    @property
    def message(self) -> str:
        return MESSAGES.get(self.status, self.status.value)


@dataclass(frozen=True)
class Output:
    """One product of a completed job (PLAN.md 7.5)."""

    id: uuid.UUID
    job_id: uuid.UUID
    output_type: str
    cog_uri: str
    bounds: dict
    crs: str
    resolution_m: float
    size_bytes: int | None = None
    valid_fraction: float | None = None
    stats: dict | None = None
    expires_at: datetime | None = None


_INSERT = text("""
    INSERT INTO jobs (process, aoi, aoi_area_km2, scene_ids, parameters, client_ip)
    VALUES (
        :process,
        ST_SetSRID(ST_GeomFromGeoJSON(:aoi), 4326),
        :aoi_area_km2,
        CAST(:scene_ids AS TEXT[]),
        CAST(:parameters AS JSONB),
        CAST(:client_ip AS INET)
    )
    RETURNING id, process, status, progress, ST_AsGeoJSON(aoi) AS aoi, aoi_area_km2,
              scene_ids, parameters, error_message, created_at, started_at, completed_at
""")

_SELECT = text("""
    SELECT id, process, status, progress, ST_AsGeoJSON(aoi) AS aoi, aoi_area_km2,
           scene_ids, parameters, error_message, created_at, started_at, completed_at
    FROM jobs WHERE id = :id
""")

#: Active means "occupying a worker slot or about to". PLAN.md 8 caps this at 2
#: globally and 1 per IP.
_ACTIVE_STATES = "('queued','searching','reading','processing','writing_cog')"


def normalize_ip(value: str | None) -> str | None:
    """A value the INET column will accept, or None.

    `request.client.host` is whatever the ASGI server reports, and that is not
    always an address: a test client says "testclient", a unix-socket
    deployment has no peer address, and a misconfigured proxy can put a
    hostname there. Postgres rejects all of those, and an unparseable peer
    would otherwise turn every submission into a 500 -- a crash caused by how
    the client connected rather than by anything it asked for.

    None means "not identifiable", which also switches off the per-IP
    concurrency cap for that caller: a limit cannot be applied to an identity
    we do not have. The global cap still holds, so the deployment stays bounded.
    """
    if not value:
        return None
    try:
        return str(ipaddress.ip_address(value))
    except ValueError:
        logger.debug("client host %r is not an IP address; storing NULL", value)
        return None


def _job_from_row(row) -> Job:
    m = row._mapping
    return Job(
        id=m["id"],
        process=m["process"],
        status=JobStatus(m["status"]),
        progress=m["progress"],
        aoi=json.loads(m["aoi"]),
        aoi_area_km2=m["aoi_area_km2"],
        scene_ids=list(m["scene_ids"] or []),
        parameters=dict(m["parameters"] or {}),
        error_message=m["error_message"],
        created_at=m["created_at"],
        started_at=m["started_at"],
        completed_at=m["completed_at"],
    )


class JobStore:
    """The `jobs` and `outputs` tables."""

    def __init__(self, engine=None) -> None:
        self._engine = engine

    @property
    def engine(self):
        engine = self._engine if self._engine is not None else get_engine()
        if engine is None:
            raise JobsUnavailable(
                "No database configured; job submission needs one. "
                "Set BHOOMI_DATABASE_URL."
            )
        return engine

    # ------------------------------------------------------------ submission

    def create(self, process: str, aoi: dict, aoi_area_km2: float,
               scene_ids: Iterable[str], parameters: dict | None = None,
               client_ip: str | None = None,
               max_global: int | None = None, max_per_ip: int | None = None) -> Job:
        """Insert a queued job, refusing if it would exceed a concurrency cap.

        The count and the insert share one transaction behind a transaction-scoped
        advisory lock. Counting and then inserting without one is the classic
        check-then-act race: two submissions arriving together would both see one
        active job, both decide there was room, and both insert -- which is
        exactly the case a concurrency limit exists to stop.
        """
        client_ip = normalize_ip(client_ip)
        params = {
            "process": process,
            "aoi": json.dumps(aoi),
            "aoi_area_km2": aoi_area_km2,
            "scene_ids": list(scene_ids),
            "parameters": json.dumps(parameters or {}),
            "client_ip": client_ip,
        }
        with self.engine.begin() as conn:
            if max_global is not None or max_per_ip is not None:
                # One arbitrary but fixed key; every submission serialises on it.
                conn.execute(text("SELECT pg_advisory_xact_lock(0x62686D31)"))

                if max_global is not None:
                    active = conn.execute(text(
                        f"SELECT count(*) FROM jobs WHERE status::text IN {_ACTIVE_STATES}"
                    )).scalar_one()
                    if active >= max_global:
                        raise TooManyActiveJobs(active, max_global, scope="global")

                if max_per_ip is not None and client_ip:
                    mine = conn.execute(text(
                        f"""SELECT count(*) FROM jobs
                            WHERE status::text IN {_ACTIVE_STATES}
                              AND client_ip = CAST(:ip AS INET)"""
                    ), {"ip": client_ip}).scalar_one()
                    if mine >= max_per_ip:
                        raise TooManyActiveJobs(mine, max_per_ip, scope="client")

            row = conn.execute(_INSERT, params).first()
        return _job_from_row(row)

    # ---------------------------------------------------------------- reads

    def get(self, job_id: uuid.UUID | str) -> Job | None:
        with self.engine.connect() as conn:
            row = conn.execute(_SELECT, {"id": str(job_id)}).first()
        return _job_from_row(row) if row is not None else None

    def position_in_queue(self, job_id: uuid.UUID | str) -> int:
        """How many queued jobs are ahead of this one. 0 means next."""
        with self.engine.connect() as conn:
            return conn.execute(text("""
                SELECT count(*) FROM jobs
                WHERE status = 'queued'
                  AND created_at < (SELECT created_at FROM jobs WHERE id = :id)
            """), {"id": str(job_id)}).scalar_one()

    def count_active(self, client_ip: str | None = None) -> int:
        sql = f"SELECT count(*) FROM jobs WHERE status::text IN {_ACTIVE_STATES}"
        params: dict = {}
        client_ip = normalize_ip(client_ip)
        if client_ip:
            sql += " AND client_ip = CAST(:ip AS INET)"
            params["ip"] = client_ip
        with self.engine.connect() as conn:
            return conn.execute(text(sql), params).scalar_one()

    # ---------------------------------------------------------- transitions

    def advance(self, job_id: uuid.UUID | str, to: JobStatus,
                error_message: str | None = None,
                error_detail: str | None = None,
                progress: int | None = None) -> Job:
        """Move a job to `to`, or raise IllegalTransition.

        The legality check lives in the WHERE clause rather than in a preceding
        SELECT, so the database decides the winner when two workers try at once:
        the loser updates zero rows and is told, instead of silently overwriting.
        """
        if progress is None:
            progress = PROGRESS.get(to)

        with self.engine.begin() as conn:
            current = conn.execute(
                text("SELECT status FROM jobs WHERE id = :id"), {"id": str(job_id)}
            ).scalar_one_or_none()
            if current is None:
                raise IllegalTransition(f"No job {job_id}")

            legal = allowed_transitions(JobStatus(current))
            if to not in legal:
                raise IllegalTransition(
                    f"{current} -> {to.value} is not a legal transition"
                    + (f"; allowed: {sorted(s.value for s in legal)}" if legal
                       else " (terminal state)")
                )

            allowed_sql = ",".join(f"'{s}'" for s in
                                   sorted(_from_states_reaching(to)))
            row = conn.execute(text(f"""
                UPDATE jobs SET
                    status = CAST(:to AS job_status),
                    progress = COALESCE(:progress, progress),
                    error_message = COALESCE(:error_message, error_message),
                    error_detail = COALESCE(:error_detail, error_detail),
                    started_at = CASE WHEN started_at IS NULL AND :to <> 'queued'
                                      THEN now() ELSE started_at END,
                    completed_at = CASE WHEN :to IN ('completed','failed','cancelled','timed_out')
                                        THEN now() ELSE completed_at END
                WHERE id = :id AND status::text IN ({allowed_sql})
                RETURNING id, process, status, progress, ST_AsGeoJSON(aoi) AS aoi,
                          aoi_area_km2, scene_ids, parameters, error_message,
                          created_at, started_at, completed_at
            """), {"id": str(job_id), "to": to.value, "progress": progress,
                   "error_message": error_message, "error_detail": error_detail}).first()

        if row is None:
            raise IllegalTransition(
                f"job {job_id} changed state underneath this transition to {to.value}")
        return _job_from_row(row)

    # --------------------------------------------------------------- outputs

    def add_output(self, job_id, output_type: str, cog_uri: str, bounds: dict,
                   crs: str, resolution_m: float, size_bytes: int | None = None,
                   valid_fraction: float | None = None, stats: dict | None = None,
                   expires_at: datetime | None = None) -> uuid.UUID:
        with self.engine.begin() as conn:
            return conn.execute(text("""
                INSERT INTO outputs (job_id, output_type, cog_uri, bounds, crs,
                                     resolution_m, size_bytes, valid_fraction,
                                     stats, expires_at)
                VALUES (:job_id, :output_type, :cog_uri,
                        ST_SetSRID(ST_GeomFromGeoJSON(:bounds), 4326),
                        :crs, :resolution_m, :size_bytes, :valid_fraction,
                        CAST(:stats AS JSONB), :expires_at)
                RETURNING id
            """), {"job_id": str(job_id), "output_type": output_type,
                   "cog_uri": cog_uri, "bounds": json.dumps(bounds), "crs": crs,
                   "resolution_m": resolution_m, "size_bytes": size_bytes,
                   "valid_fraction": valid_fraction,
                   "stats": json.dumps(stats) if stats is not None else None,
                   "expires_at": expires_at}).scalar_one()

    def outputs_for(self, job_id) -> list[Output]:
        with self.engine.connect() as conn:
            rows = conn.execute(text("""
                SELECT id, job_id, output_type, cog_uri, ST_AsGeoJSON(bounds) AS bounds,
                       crs, resolution_m, size_bytes, valid_fraction, stats, expires_at
                FROM outputs WHERE job_id = :job_id ORDER BY created_at
            """), {"job_id": str(job_id)}).all()
        return [
            Output(
                id=r._mapping["id"], job_id=r._mapping["job_id"],
                output_type=r._mapping["output_type"], cog_uri=r._mapping["cog_uri"],
                bounds=json.loads(r._mapping["bounds"]), crs=r._mapping["crs"],
                resolution_m=r._mapping["resolution_m"],
                size_bytes=r._mapping["size_bytes"],
                valid_fraction=r._mapping["valid_fraction"],
                stats=r._mapping["stats"], expires_at=r._mapping["expires_at"],
            )
            for r in rows
        ]

    def clear(self) -> None:
        """Forget every job. Tests only -- outputs cascade."""
        with self.engine.begin() as conn:
            conn.execute(text("TRUNCATE TABLE jobs CASCADE"))


class TooManyActiveJobs(RuntimeError):
    """A concurrency cap from PLAN.md 8 would have been exceeded."""

    def __init__(self, active: int, limit: int, scope: str) -> None:
        self.active, self.limit, self.scope = active, limit, scope
        super().__init__(f"{active} active {scope} jobs; limit is {limit}")


def _from_states_reaching(to: JobStatus) -> set[str]:
    """States from which `to` is legal -- the WHERE clause of `advance`."""
    return {s.value for s in JobStatus if to in allowed_transitions(s)}


def jobs_available() -> bool:
    """Whether job submission can be accepted at all."""
    return get_engine() is not None
