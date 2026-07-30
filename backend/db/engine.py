"""Engine construction, and the decision of whether there is a database at all.

Postgres is **optional**. Scene search, and every function in ``processing/``,
work without it -- the ``scenes`` table is a cache (PLAN.md 6), and an absent
cache is one that always misses. That is a latency cost, never a wrong answer.

So `BHOOMI_DATABASE_URL` unset is a supported configuration, not a
misconfiguration, and nothing here raises on account of it. It is what the test
suite and a bare `uvicorn` run both use.

The URL is read at call time rather than import time so that tests can set it
with `monkeypatch.setenv` after the module is already imported.
"""

from __future__ import annotations

import logging
import os
import threading

from sqlalchemy import Engine, create_engine

logger = logging.getLogger(__name__)

ENV_VAR = "BHOOMI_DATABASE_URL"
TIMEOUT_VAR = "BHOOMI_DB_CONNECT_TIMEOUT"

# Seconds to wait for a connection before giving up on the cache for this
# request. Without a limit this defaults to the OS TCP timeout -- measured at
# **130 s** against an unreachable host, which turns "degrade to no cache" into
# "every search hangs for two minutes". The graceful degradation in
# `scenes.py` is only graceful if the failure arrives quickly.
#
# 5 s is well above a healthy local or same-region connect (single-digit ms)
# and well below anything a user would wait through.
DEFAULT_CONNECT_TIMEOUT = 5

# Engines are pooled per URL and shared process-wide: creating one per request
# would mean a new connection pool per request, which is worse than no pool.
_engines: dict[str, Engine] = {}
_lock = threading.Lock()


def database_url() -> str | None:
    """The configured URL, or None when Bhoomi should run without a database."""
    return os.getenv(ENV_VAR) or None


def connect_timeout() -> int:
    """Connect timeout in seconds. Falls back to the default if unparseable."""
    raw = os.getenv(TIMEOUT_VAR)
    if not raw:
        return DEFAULT_CONNECT_TIMEOUT
    try:
        return max(1, int(raw))
    except ValueError:
        logger.warning("%s=%r is not an integer; using %ds",
                       TIMEOUT_VAR, raw, DEFAULT_CONNECT_TIMEOUT)
        return DEFAULT_CONNECT_TIMEOUT


def normalize_url(url: str) -> str:
    """Force the psycopg3 driver.

    `postgresql://` makes SQLAlchemy look for psycopg2, which Bhoomi does not
    install -- the failure is an ImportError at first connect, a long way from
    the environment variable that caused it. Compose files, hosting dashboards
    and `DATABASE_URL` conventions all emit the bare form, so accept it and fix
    it here rather than making every caller remember the +psycopg suffix.
    """
    for prefix in ("postgresql://", "postgres://"):
        if url.startswith(prefix):
            return "postgresql+psycopg://" + url[len(prefix):]
    return url


def get_engine() -> Engine | None:
    """The shared engine for the configured URL, or None when there is none."""
    url = database_url()
    if url is None:
        return None
    with _lock:
        engine = _engines.get(url)
        if engine is None:
            # pool_pre_ping because the pool outlives the database across a
            # `docker compose restart postgres`; a stale socket should cost one
            # retry rather than one failed request.
            engine = create_engine(
                normalize_url(url),
                pool_pre_ping=True,
                future=True,
                connect_args={"connect_timeout": connect_timeout()},
            )
            _engines[url] = engine
        return engine


def reset_engines() -> None:
    """Dispose and forget every cached engine.

    Tests that point at a throwaway database need the pool closed, otherwise
    `DROP DATABASE` blocks on the connections still held open by this process.
    """
    with _lock:
        for engine in _engines.values():
            engine.dispose()
        _engines.clear()
