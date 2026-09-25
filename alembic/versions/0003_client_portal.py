"""Client portal slice: kind-level client visibility, viewer_scope on memberships,
the client-facing comment thread and client review verdicts (CLAUDE.md §3 Portal).

Revision ID: 0003
Revises: 0002
"""

from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
    ALTER TABLE deliverable_kinds ADD COLUMN client_visible BOOLEAN NOT NULL DEFAULT false;
    UPDATE deliverable_kinds SET client_visible = true
      WHERE engagement_type_key = 'migration' AND kind IN ('dashboard', 'tile');

    ALTER TABLE engagement_memberships ADD COLUMN viewer_scope TEXT NOT NULL DEFAULT 'full'
      CONSTRAINT ck_engagement_memberships_viewer_scope CHECK (viewer_scope IN ('full', 'assigned_only'));

    CREATE TABLE deliverable_comments (
      id UUID PRIMARY KEY, deliverable_id UUID NOT NULL REFERENCES deliverables(id),
      author_user_id UUID NOT NULL REFERENCES users(id),
      body TEXT NOT NULL, created_at TIMESTAMPTZ DEFAULT now()
    );
    CREATE INDEX ix_deliverable_comments_deliverable ON deliverable_comments (deliverable_id);

    CREATE TABLE deliverable_client_reviews (
      id UUID PRIMARY KEY, deliverable_id UUID NOT NULL REFERENCES deliverables(id),
      reviewer_user_id UUID NOT NULL REFERENCES users(id),
      verdict TEXT NOT NULL
        CONSTRAINT ck_deliverable_client_reviews_verdict CHECK (verdict IN ('accepted', 'blocked', 'rejected')),
      created_at TIMESTAMPTZ DEFAULT now()
    );
    CREATE INDEX ix_deliverable_client_reviews_deliverable ON deliverable_client_reviews (deliverable_id);
    """)


def downgrade() -> None:
    op.execute("""
    DROP TABLE deliverable_client_reviews;
    DROP TABLE deliverable_comments;
    ALTER TABLE engagement_memberships DROP COLUMN viewer_scope;
    ALTER TABLE deliverable_kinds DROP COLUMN client_visible;
    """)
