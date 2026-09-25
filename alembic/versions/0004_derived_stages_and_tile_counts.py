"""Derived, concurrent stages and bulk child counts (CLAUDE.md §6, 2026-09-25).

- engagement_types.stage_kind: the deliverable kind that stands for the type's stages
  (migration → phase). For such types a stage's state is derived from its phase
  deliverables and several stages can be active at once; engagements.stage is unused
  (NULL). Types without one (quickstart) keep a hand-set engagements.stage.
- deliverables.stage_key: which stage a stage-kind deliverable stands for.
- deliverable_kinds.bulk_child_counts / deliverables.child_counts: a parent (e.g. a
  dashboard) can track most of its children as per-status counts, listing only the ones
  worth singling out individually.

Revision ID: 0004
Revises: 0003
"""

from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
    ALTER TABLE engagement_types ADD COLUMN stage_kind TEXT;
    ALTER TABLE engagement_types ADD CONSTRAINT fk_engagement_types_stage_kind
      FOREIGN KEY (key, stage_kind) REFERENCES deliverable_kinds(engagement_type_key, kind);
    UPDATE engagement_types SET stage_kind = 'phase' WHERE key = 'migration';

    ALTER TABLE deliverables ADD COLUMN stage_key TEXT;
    -- Existing phases are linked to the stage whose label they share.
    UPDATE deliverables d SET stage_key = s.stage->>'key'
    FROM engagement_types t, jsonb_array_elements(t.stage_vocab) AS s(stage)
    WHERE t.key = d.engagement_type_key AND t.stage_kind = d.kind
      AND lower(s.stage->>'label') = lower(d.name);

    ALTER TABLE engagements ALTER COLUMN stage DROP NOT NULL;
    UPDATE engagements e SET stage = NULL FROM engagement_types t
      WHERE t.key = e.type_key AND t.stage_kind IS NOT NULL;

    ALTER TABLE deliverable_kinds ADD COLUMN bulk_child_counts BOOLEAN NOT NULL DEFAULT false;
    UPDATE deliverable_kinds SET bulk_child_counts = true
      WHERE engagement_type_key = 'migration' AND kind = 'dashboard';
    ALTER TABLE deliverables ADD COLUMN child_counts JSONB;
    """)


def downgrade() -> None:
    op.execute("""
    ALTER TABLE deliverables DROP COLUMN child_counts;
    ALTER TABLE deliverable_kinds DROP COLUMN bulk_child_counts;
    UPDATE engagements e SET stage = t.stage_vocab->0->>'key' FROM engagement_types t
      WHERE t.key = e.type_key AND e.stage IS NULL;
    ALTER TABLE engagements ALTER COLUMN stage SET NOT NULL;
    ALTER TABLE deliverables DROP COLUMN stage_key;
    ALTER TABLE engagement_types DROP CONSTRAINT fk_engagement_types_stage_kind;
    ALTER TABLE engagement_types DROP COLUMN stage_kind;
    """)
