"""Cached STAC scene metadata (PLAN.md 6).

Revision ID: 0001
Revises:
Created: 2026-07-30

The SQL is copied from PLAN.md 6 rather than paraphrased, so the two can be
diffed. `jobs` and `outputs` are in that section too but are not created here:
nothing writes them until the January queue lands, and an unexercised table is
a schema free to be wrong.
"""

from __future__ import annotations

from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # PostGIS ships in the postgis/postgis image but is not enabled per-database.
    # Requires superuser, which the compose role has and a managed-Postgres role
    # may not -- on a provider that pre-enables it, this is a no-op.
    op.execute("CREATE EXTENSION IF NOT EXISTS postgis")

    op.execute("""
        CREATE TABLE scenes (
            id                  BIGSERIAL PRIMARY KEY,
            external_id         TEXT NOT NULL,
            catalogue           TEXT NOT NULL,
            collection          TEXT NOT NULL,
            satellite           TEXT,
            sensor              TEXT,
            acquired_at         TIMESTAMPTZ NOT NULL,
            cloud_cover         REAL,
            processing_baseline TEXT,
            boa_offset_present  BOOLEAN,
            geometry            GEOMETRY(Polygon, 4326) NOT NULL,
            assets              JSONB NOT NULL,
            properties          JSONB,
            cached_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
            UNIQUE (catalogue, external_id)
        )
    """)
    op.execute("CREATE INDEX scenes_geom_idx ON scenes USING GIST (geometry)")
    op.execute("CREATE INDEX scenes_acquired_idx ON scenes (acquired_at DESC)")


def downgrade() -> None:
    # The indexes go with the table. The extension does not: it may predate
    # Bhoomi on a shared database, and dropping it would take out every other
    # geometry column with it.
    op.execute("DROP TABLE IF EXISTS scenes")
