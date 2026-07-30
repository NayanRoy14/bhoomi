"""Persistence.

Optional for `scenes`, which is a cache (see `engine.database_url`), and
required for `jobs`, which is the record of the work itself. `backend/db/jobs.py`
explains why the second has no null implementation.
"""

from backend.db.engine import database_url, get_engine, reset_engines
from backend.db.jobs import (
    Job,
    JobStatus,
    JobStore,
    JobsUnavailable,
    Output,
    TooManyActiveJobs,
    jobs_available,
)
from backend.db.scenes import (
    DEFAULT_CATALOGUE,
    NullSceneStore,
    PostgresOffsetCache,
    PostgresSceneStore,
    SceneStore,
    default_scene_store,
)

__all__ = [
    "DEFAULT_CATALOGUE",
    "Job",
    "JobStatus",
    "JobStore",
    "JobsUnavailable",
    "NullSceneStore",
    "Output",
    "PostgresOffsetCache",
    "PostgresSceneStore",
    "SceneStore",
    "TooManyActiveJobs",
    "database_url",
    "default_scene_store",
    "get_engine",
    "jobs_available",
    "reset_engines",
]
