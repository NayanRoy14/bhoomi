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
TEST_S3_ENDPOINT = os.getenv("BHOOMI_TEST_S3_ENDPOINT")

needs_db = pytest.mark.skipif(not TEST_DB_URL,
                              reason="set BHOOMI_TEST_DATABASE_URL to run")
needs_redis = pytest.mark.skipif(not TEST_REDIS_URL,
                                 reason="set BHOOMI_TEST_REDIS_URL to run")
needs_s3 = pytest.mark.skipif(not TEST_S3_ENDPOINT,
                              reason="set BHOOMI_TEST_S3_ENDPOINT to run")

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
def s3_bucket():
    """An empty bucket on an S3-compatible server.

    MinIO stands in for R2 (PLAN.md D14). That is not a compromise: R2 *is*
    reached through the S3 API, which is the whole reason the decision was safe
    to make before benchmarking. What these tests can prove is that the
    implementation speaks S3 correctly. What they cannot prove is anything
    about R2's own latency or durability -- see PLAN.md 9.4.
    """
    import uuid

    from backend.storage import S3Storage

    name = f"bhoomi-test-{uuid.uuid4().hex[:12]}"
    store = S3Storage(
        bucket=name,
        endpoint=TEST_S3_ENDPOINT,
        access_key=os.getenv("BHOOMI_TEST_S3_ACCESS_KEY_ID", "minioadmin"),
        secret_key=os.getenv("BHOOMI_TEST_S3_SECRET_ACCESS_KEY", "minioadmin"),
        region="us-east-1",
        public_base_url="",
    )
    store.client.create_bucket(Bucket=name)
    yield store

    listed = store.client.list_objects_v2(Bucket=name).get("Contents", [])
    for item in listed:
        store.client.delete_object(Bucket=name, Key=item["Key"])
    store.client.delete_bucket(Bucket=name)


@pytest.fixture
def redis_conn():
    """A flushed Redis database. Uses whichever db index the URL names."""
    from redis import Redis

    conn = Redis.from_url(TEST_REDIS_URL)
    conn.flushdb()
    yield conn
    conn.flushdb()
    conn.close()
