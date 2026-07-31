"""Cache the measured DN floor instead of the offset verdict.

Revision ID: 0005
Revises: 0004
Created: 2026-07-31

`scenes.boa_offset_present` cached a *conclusion*. That turned out to be the
wrong thing to store, twice over.

**It cannot survive a recalibration.** When the detector's sampling density was
corrected, no cached row could be repaired individually -- a boolean does not
carry the number that produced it -- so migration 0003 nulled the entire column
and every scene had to be re-read over the network. That has now happened once
and would happen again on the next threshold change.

**And the detector it served was measured wrong.** Across 48 scenes (8 regions,
6 years, baselines 02.11-05.12, 2026-07-31) the shipped rule -- dark fraction
below 1% means the offset is present -- misclassified **17 of 47** offset-absent
scenes. Every Thar Desert and Delhi scene reads below the threshold because
those tiles contain almost no dark ground, not because they carry the offset.
The statistic was measuring terrain.

`boa_floor_dn` stores the 0.1st percentile of the valid DN distribution: a raw
observation that does not move when a threshold does. The verdict is derived on
read, so a future recalibration costs nothing.

DOUBLE PRECISION rather than INTEGER because it is a percentile, not a pixel
value. Nullable with no default -- an unmeasured scene must read as "unknown",
never as a floor of 0, which would prove every scene offset-absent.

The old column is dropped rather than kept in step: leaving both invites code
that reads the stale one. `downgrade` restores the column but not its contents,
which are not recoverable from the floor alone without re-deciding them -- and
re-deciding them under the old, broken rule is not something this migration
should do.
"""

from __future__ import annotations

from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE scenes ADD COLUMN boa_floor_dn DOUBLE PRECISION")
    op.execute("ALTER TABLE scenes DROP COLUMN boa_offset_present")


def downgrade() -> None:
    op.execute("ALTER TABLE scenes ADD COLUMN boa_offset_present BOOLEAN")
    op.execute("ALTER TABLE scenes DROP COLUMN boa_floor_dn")
