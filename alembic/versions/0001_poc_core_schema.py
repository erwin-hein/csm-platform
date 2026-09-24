"""PoC core schema: identity & access, client, engagement + type system,
deliverables engine, events spine. Seeds the two core engagement types and
their deliverable kinds (CLAUDE.md §3) as reference data.

Revision ID: 0001
Revises:
"""

from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
    CREATE TYPE user_type AS ENUM ('internal', 'client_external');
    CREATE TYPE internal_role AS ENUM ('analyst', 'contractor', 'ops', 'admin');
    CREATE TYPE membership_role AS ENUM ('owner', 'collaborator', 'viewer');

    CREATE TABLE users (
      id UUID PRIMARY KEY,
      email TEXT UNIQUE NOT NULL,
      display_name TEXT,
      user_type user_type NOT NULL,
      role internal_role,
      status TEXT DEFAULT 'active',
      created_at TIMESTAMPTZ DEFAULT now()
    );

    CREATE TABLE clients (
      id UUID PRIMARY KEY,
      name TEXT NOT NULL,
      slug TEXT UNIQUE NOT NULL,
      domains TEXT[] NOT NULL DEFAULT '{}',
      created_at TIMESTAMPTZ DEFAULT now()
    );

    CREATE TABLE client_aliases (
      id UUID PRIMARY KEY, client_id UUID REFERENCES clients(id),
      alias TEXT NOT NULL, alias_type TEXT NOT NULL
    );

    CREATE TABLE client_contacts (
      id UUID PRIMARY KEY, client_id UUID REFERENCES clients(id),
      name TEXT, email TEXT, slack_user_id TEXT, title TEXT,
      source TEXT,
      status TEXT DEFAULT 'active'
    );

    CREATE TABLE engagement_types (
      key TEXT PRIMARY KEY,
      display_name TEXT,
      storage_mode TEXT NOT NULL,
      stage_vocab JSONB NOT NULL
    );

    CREATE TABLE engagements (
      id UUID PRIMARY KEY,
      client_id UUID REFERENCES clients(id),
      type_key TEXT REFERENCES engagement_types(key),
      name TEXT NOT NULL,
      stage TEXT NOT NULL,
      status TEXT DEFAULT 'active'
        CONSTRAINT ck_engagements_status CHECK (status IN ('active','paused','complete','cancelled')),
      owner_user_id UUID REFERENCES users(id),
      slack_channel_id TEXT,
      internal_slack_channel_id TEXT,
      harvest_project_id TEXT,
      expected_scope JSONB,
      health TEXT,
      details JSONB DEFAULT '{}',
      started_at TIMESTAMPTZ, ended_at TIMESTAMPTZ,
      created_at TIMESTAMPTZ DEFAULT now(),
      CONSTRAINT uq_engagements_id_type UNIQUE (id, type_key)
    );

    CREATE TABLE engagement_memberships (
      engagement_id UUID REFERENCES engagements(id),
      user_id UUID REFERENCES users(id),
      role membership_role NOT NULL,
      PRIMARY KEY (engagement_id, user_id)
    );
    CREATE UNIQUE INDEX uq_engagement_memberships_one_owner
      ON engagement_memberships (engagement_id) WHERE role = 'owner';
    CREATE INDEX ix_engagement_memberships_user ON engagement_memberships (user_id);

    CREATE TABLE deliverable_kinds (
      engagement_type_key TEXT REFERENCES engagement_types(key),
      kind TEXT NOT NULL,
      display_name TEXT,
      parent_kind TEXT,
      PRIMARY KEY (engagement_type_key, kind),
      CONSTRAINT fk_deliverable_kinds_parent_kind
        FOREIGN KEY (engagement_type_key, parent_kind) REFERENCES deliverable_kinds(engagement_type_key, kind)
    );

    CREATE TABLE deliverables (
      id UUID PRIMARY KEY,
      engagement_id UUID REFERENCES engagements(id),
      engagement_type_key TEXT,
      parent_id UUID REFERENCES deliverables(id),
      kind TEXT NOT NULL,
      name TEXT NOT NULL,
      pipeline_status TEXT DEFAULT 'not_started'
        CONSTRAINT ck_deliverables_pipeline_status CHECK (pipeline_status IN
          ('not_started','in_progress','internal_validation','external_validation','done')),
      blocked BOOLEAN DEFAULT false, blocked_reason TEXT,
      not_applicable BOOLEAN DEFAULT false,
      internal_assignee_user_id UUID REFERENCES users(id),
      client_owner_user_id UUID REFERENCES users(id),
      priority TEXT, target_date DATE,
      hours_estimated NUMERIC,
      created_at TIMESTAMPTZ DEFAULT now(),
      CONSTRAINT fk_deliverables_kind
        FOREIGN KEY (engagement_type_key, kind) REFERENCES deliverable_kinds(engagement_type_key, kind),
      CONSTRAINT fk_deliverables_engagement_type
        FOREIGN KEY (engagement_id, engagement_type_key) REFERENCES engagements(id, type_key),
      CONSTRAINT ck_deliverables_no_self_parent CHECK (parent_id IS NULL OR parent_id <> id)
    );
    CREATE INDEX ix_deliverables_engagement ON deliverables (engagement_id);

    CREATE TABLE deliverable_blockers (
      blocked_id UUID REFERENCES deliverables(id),
      blocker_id UUID REFERENCES deliverables(id),
      PRIMARY KEY (blocked_id, blocker_id),
      CONSTRAINT ck_deliverable_blockers_no_self_loop CHECK (blocked_id <> blocker_id)
    );

    CREATE TABLE deliverable_activity (
      id UUID PRIMARY KEY, deliverable_id UUID REFERENCES deliverables(id),
      actor_user_id UUID REFERENCES users(id), kind TEXT, body TEXT, created_at TIMESTAMPTZ DEFAULT now()
    );
    CREATE INDEX ix_deliverable_activity_deliverable ON deliverable_activity (deliverable_id);

    CREATE TABLE events (
      id BIGSERIAL PRIMARY KEY,
      occurred_at TIMESTAMPTZ DEFAULT now(),
      entity_type TEXT, entity_id UUID,
      event_type TEXT,
      actor_user_id UUID REFERENCES users(id),
      payload JSONB
    );
    CREATE INDEX ix_events_entity ON events (entity_type, entity_id);

    -- Append-only is enforced by the database, not just by convention.
    CREATE FUNCTION events_append_only() RETURNS trigger AS $$
    BEGIN
      RAISE EXCEPTION 'events is append-only: % is not allowed', TG_OP;
    END;
    $$ LANGUAGE plpgsql;
    CREATE TRIGGER events_no_update_delete
      BEFORE UPDATE OR DELETE ON events
      FOR EACH ROW EXECUTE FUNCTION events_append_only();
    CREATE TRIGGER events_no_truncate
      BEFORE TRUNCATE ON events
      FOR EACH STATEMENT EXECUTE FUNCTION events_append_only();
    """)

    # Reference data — verbatim from CLAUDE.md §3. Sent as raw driver SQL so the
    # JSON's "gate":true isn't parsed as a SQLAlchemy :bind parameter.
    op.get_bind().exec_driver_sql("""
    INSERT INTO engagement_types VALUES ('quickstart', 'QuickStart', 'jsonb', '[
      {"key":"kickoff","label":"Kickoff"}, {"key":"dev_training","label":"Dev Training"},
      {"key":"codev","label":"Co-Dev"}, {"key":"creator_training","label":"Creator Training"},
      {"key":"wrapup","label":"Wrap-Up"}, {"key":"handed_off","label":"Handed Off"},
      {"key":"post_qs","label":"Post-QS"}, {"key":"dormant","label":"Dormant"}
    ]');

    INSERT INTO engagement_types VALUES ('migration', 'Migration', 'jsonb', '[
      {"key":"scoping","label":"Scoping"}, {"key":"access_setup","label":"Access & Setup"},
      {"key":"semantic_parity","label":"Semantic-layer Parity","gate":true}, {"key":"dashboard_build","label":"Dashboard Build"},
      {"key":"client_validation","label":"Client Validation"}, {"key":"golive_wrapup","label":"Go-live / Wrap-up"}
    ]');

    INSERT INTO deliverable_kinds (engagement_type_key, kind, display_name, parent_kind) VALUES
      ('migration', 'phase', 'Phase', NULL),
      ('migration', 'dashboard', 'Dashboard', NULL),
      ('migration', 'milestone', 'Milestone', 'phase'),
      ('migration', 'tile', 'Tile', 'dashboard'),
      ('quickstart', 'module', 'Curriculum Module', NULL);
    """)


def downgrade() -> None:
    op.execute("""
    DROP TABLE events;
    DROP FUNCTION events_append_only();
    DROP TABLE deliverable_activity;
    DROP TABLE deliverable_blockers;
    DROP TABLE deliverables;
    DROP TABLE deliverable_kinds;
    DROP TABLE engagement_memberships;
    DROP TABLE engagements;
    DROP TABLE engagement_types;
    DROP TABLE client_contacts;
    DROP TABLE client_aliases;
    DROP TABLE clients;
    DROP TABLE users;
    DROP TYPE membership_role;
    DROP TYPE internal_role;
    DROP TYPE user_type;
    """)
