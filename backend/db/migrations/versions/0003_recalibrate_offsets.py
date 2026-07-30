"""Discard offset decisions made by the mis-sampled detector.

Revision ID: 0003
Revises: 0002
Created: 2026-07-31

`scenes.boa_offset_present` is a cache of a measurement (PLAN.md 5.3), and
until 2026-07-31 that measurement sampled the tile at decimation 32 -- where
averaging has erased most of the dark tail the test depends on, putting four of
eight offset-absent scenes below the threshold. Every value recorded before the
fix was produced by that detector and cannot be distinguished, row by row, from
one that happened to be right.

So all of them go. A NULL costs one re-measurement (~7 s, once per scene ever);
a wrong one silently shifts every index computed from that scene -- the failure
5.3 exists to prevent, and the one that produced a median NDVI of +1.703.

There is no downgrade for this. Restoring values known to be unreliable is not
a state worth being able to return to; `downgrade` is a no-op rather than a
lie about reversibility.
"""

from __future__ import annotations

from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("UPDATE scenes SET boa_offset_present = NULL")


def downgrade() -> None:
    # Deliberately empty: the discarded values were wrong, not merely old.
    pass
