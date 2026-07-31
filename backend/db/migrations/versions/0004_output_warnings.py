"""Carry processing warnings on the output row.

Revision ID: 0004
Revises: 0003
Created: 2026-07-31

PLAN.md 6 does not define this column; it is an addition, and the reason is a
gap that only showed up once the API had real users in front of it.

`processing/` already produces warnings that change how a result should be
read -- "this scene has no SCL band, cloud and shadow are NOT masked", and the
processing-baseline mismatch of 5.3. Until now they reached the worker log and
the GeoTIFF's own tags, and stopped there. Someone using the web interface, who
never opens the file in GDAL, got an unmasked raster with nothing to say so.

For a project whose stated value is that it tells you what it checked and what
it found, a silent unmasked result is the wrong failure. So the warnings travel
with the row and out through 7.5.

TEXT[] rather than JSONB, matching `jobs.scene_ids`: it is a list of strings and
never queried by structure. Nullable with a default, so the column arrives
without rewriting existing rows.
"""

from __future__ import annotations

from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE outputs ADD COLUMN warnings TEXT[] NOT NULL DEFAULT '{}'")


def downgrade() -> None:
    op.execute("ALTER TABLE outputs DROP COLUMN warnings")
