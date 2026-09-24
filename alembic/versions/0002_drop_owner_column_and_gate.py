"""Drop engagements.owner_user_id (the owner membership is the owner) and remove the
"gate" stage flag from the migration type's vocabulary (CLAUDE.md §6, 2026-09-24).

Revision ID: 0002
Revises: 0001
"""

from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE engagements DROP COLUMN owner_user_id")
    # Strip the "gate" key from every stage object, for any type that carries it.
    op.execute("""
    UPDATE engagement_types
    SET stage_vocab = (SELECT jsonb_agg(stage - 'gate' ORDER BY ord)
                       FROM jsonb_array_elements(stage_vocab) WITH ORDINALITY AS t(stage, ord))
    WHERE stage_vocab @? '$[*].gate'
    """)


def downgrade() -> None:
    op.execute("ALTER TABLE engagements ADD COLUMN owner_user_id UUID REFERENCES users(id)")
    op.execute("""
    UPDATE engagements e SET owner_user_id = m.user_id
    FROM engagement_memberships m WHERE m.engagement_id = e.id AND m.role = 'owner'
    """)
    op.execute("""
    UPDATE engagement_types
    SET stage_vocab = (SELECT jsonb_agg(CASE WHEN stage->>'key' = 'semantic_parity'
                                             THEN stage || jsonb_build_object('gate', true) ELSE stage END
                                        ORDER BY ord)
                       FROM jsonb_array_elements(stage_vocab) WITH ORDINALITY AS t(stage, ord))
    WHERE key = 'migration'
    """)
