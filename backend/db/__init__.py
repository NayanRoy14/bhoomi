"""Persistence. Optional -- see `engine.database_url`.

Only the `scenes` table exists so far. `jobs` and `outputs` (PLAN.md 6) arrive
in January with the queue that writes them, on the same principle the compose
file follows: a table nothing reads is a schema that can drift without any test
noticing.
"""

from backend.db.engine import database_url, get_engine, reset_engines
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
    "NullSceneStore",
    "PostgresOffsetCache",
    "PostgresSceneStore",
    "SceneStore",
    "database_url",
    "default_scene_store",
    "get_engine",
    "reset_engines",
]
