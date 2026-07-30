"""Jobs and outputs (PLAN.md 6).

Revision ID: 0002
Revises: 0001
Created: 2026-07-31

Arrives with the queue that writes them, on the principle 0001 stated: a table
nothing exercises is a schema free to be wrong. `outputs` is created here
rather than later because the job state machine's terminal transition writes
it, even though the January fake process produces none.
"""

from __future__ import annotations

from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TYPE job_status AS ENUM (
            'queued','searching','reading','processing',
            'writing_cog','completed','failed','cancelled','timed_out'
        )
    """)

    # gen_random_uuid() is built into PostgreSQL 13+, so no pgcrypto extension.
    op.execute("""
        CREATE TABLE jobs (
            id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            process       TEXT NOT NULL,
            status        job_status NOT NULL DEFAULT 'queued',
            progress      SMALLINT NOT NULL DEFAULT 0 CHECK (progress BETWEEN 0 AND 100),
            aoi           GEOMETRY(Polygon, 4326) NOT NULL,
            aoi_area_km2  REAL NOT NULL,
            scene_ids     TEXT[] NOT NULL,
            parameters    JSONB NOT NULL DEFAULT '{}',
            error_message TEXT,
            error_detail  TEXT,
            client_ip     INET,
            created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
            started_at    TIMESTAMPTZ,
            completed_at  TIMESTAMPTZ
        )
    """)
    op.execute("CREATE INDEX jobs_status_idx ON jobs (status)")
    op.execute("CREATE INDEX jobs_created_idx ON jobs (created_at DESC)")

    op.execute("""
        CREATE TABLE outputs (
            id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            job_id         UUID NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
            output_type    TEXT NOT NULL,
            cog_uri        TEXT NOT NULL,
            bounds         GEOMETRY(Polygon, 4326) NOT NULL,
            crs            TEXT NOT NULL,
            resolution_m   REAL NOT NULL,
            size_bytes     BIGINT,
            valid_fraction REAL,
            stats          JSONB,
            expires_at     TIMESTAMPTZ,
            created_at     TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)
    # Every read of this table is "the outputs of job X" (7.5).
    op.execute("CREATE INDEX outputs_job_idx ON outputs (job_id)")


def downgrade() -> None:
    # outputs first: it holds the foreign key into jobs.
    op.execute("DROP TABLE IF EXISTS outputs")
    op.execute("DROP TABLE IF EXISTS jobs")
    op.execute("DROP TYPE IF EXISTS job_status")
