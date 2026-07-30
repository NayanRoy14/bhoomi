"""Shared fixtures for the tests that need real infrastructure.

Postgres and Redis are both opt-in. Without them the suite still runs, and the
tests that need them skip -- which keeps `python -m pytest` on a clean clone
meaningful, at the cost of it not covering the SQL. The commands are in the
README.

The alternative, mocking Postgres, would test the mock: the parts worth
checking here are whether ST_GeomFromGeoJSON accepts our geometry, whether an
UPDATE with a status guard actually blocks an illegal transition, and whether
an advisory lock serialises two submissions. A fake would have to invent all
three.
"""

from __future__ import annotations

import os

import pytest

TEST_DB_URL = os.getenv("BHOOMI_TEST_DATABASE_URL")
TEST_REDIS_URL = os.getenv("BHOOMI_TEST_REDIS_URL")

needs_db = pytest.mark.skipif(not TEST_DB_URL,
                              reason="set BHOOMI_TEST_DATABASE_URL to run")
needs_redis = pytest.mark.skipif(not TEST_REDIS_URL,
                                 reason="set BHOOMI_TEST_REDIS_URL to run")

#: Dropped in dependency order before migrating. Explicit rather than a schema
#: drop so a stray table created by hand survives to be noticed.
_DROP = """
    DROP TABLE IF EXISTS outputs CASCADE;
    DROP TABLE IF EXISTS jobs CASCADE;
    DROP TABLE IF EXISTS scenes CASCADE;
    DROP TABLE IF EXISTS alembic_version CASCADE;
    DROP TYPE IF EXISTS job_status;
"""


@pytest.fixture(scope="session")
def db():
    """A freshly migrated database, built once for the session.

    Rebuilt from nothing rather than reused, so a half-migrated leftover cannot
    mask a broken migration -- the migrations are themselves under test.
    """
    from alembic import command
    from alembic.config import Config
    from sqlalchemy import create_engine, text

    from backend.db.engine import normalize_url

    url = normalize_url(TEST_DB_URL)
    engine = create_engine(url, future=True)

    with engine.begin() as conn:
        for statement in filter(None, (s.strip() for s in _DROP.split(";"))):
            conn.execute(text(statement))

    cfg = Config("alembic.ini")
    cfg.set_main_option("sqlalchemy.url", url)
    command.upgrade(cfg, "head")

    yield engine
    engine.dispose()


@pytest.fixture
def clean_db(db):
    """Empty tables before each test that touches them."""
    from sqlalchemy import text

    with db.begin() as conn:
        conn.execute(text("TRUNCATE TABLE jobs CASCADE"))
        conn.execute(text("TRUNCATE TABLE scenes CASCADE"))
    return db


@pytest.fixture
def redis_conn():
    """A flushed Redis database. Uses whichever db index the URL names."""
    from redis import Redis

    conn = Redis.from_url(TEST_REDIS_URL)
    conn.flushdb()
    yield conn
    conn.flushdb()
    conn.close()
