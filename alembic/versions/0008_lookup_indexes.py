"""Indexes on foreign keys the app looks up by but that had none.

Found while measuring board performance at scale (CLAUDE.md §5, 2026-09-28): the engagement tree
walks children by parent_id, dep_state looks blockers up from both ends of an edge, and the
won-without-engagement flag looks engagements up by opportunity. At 500x the demo data these
roughly halve board render time. They don't touch the semantic views, so nothing is recreated.

Revision ID: 0008
Revises: 0007
"""

from alembic import op

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
    CREATE INDEX ix_deliverables_parent ON deliverables (parent_id);
    CREATE INDEX ix_deliverable_blockers_blocker ON deliverable_blockers (blocker_id);
    CREATE INDEX ix_engagements_opportunity ON engagements (opportunity_id);
    """)


def downgrade() -> None:
    op.execute("""
    DROP INDEX ix_engagements_opportunity;
    DROP INDEX ix_deliverable_blockers_blocker;
    DROP INDEX ix_deliverables_parent;
    """)
