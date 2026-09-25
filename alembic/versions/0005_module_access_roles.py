"""Module-based access (CLAUDE.md §3 Identity & access, §6 2026-09-25).

Access has two independent axes. Module access decides which areas of the app a user
can enter; record access (engagement_memberships, opportunity ownership) decides which
records inside a module they see. Module access is granted through named access roles:
each role grants a level ('use' | 'manage') per module, a user holds any number of roles,
and their effective level per module is the highest any of their roles grants.

users.role (the analyst/contractor/ops/admin enum) is replaced by:
- users.is_admin: bypasses everything and is the only thing that can edit access;
- users.is_contractor: an employment fact, read by the client-invite rule;
- roles: ops → Operations (manage everywhere), analyst/contractor → Delivery.

Revision ID: 0005
Revises: 0004
"""

from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
    CREATE TABLE modules (
      key TEXT PRIMARY KEY,
      label TEXT NOT NULL,
      sort_order INT NOT NULL DEFAULT 0
    );
    INSERT INTO modules VALUES
      ('engagements', 'Engagements', 1), ('opportunities', 'Opportunities', 2), ('team', 'Team', 3);

    CREATE TABLE access_roles (
      id UUID PRIMARY KEY,
      name TEXT UNIQUE NOT NULL,
      description TEXT,
      is_default BOOLEAN NOT NULL DEFAULT false,   -- granted to every new internal user on first login
      created_at TIMESTAMPTZ DEFAULT now()
    );

    CREATE TABLE access_role_grants (
      role_id UUID NOT NULL REFERENCES access_roles(id) ON DELETE CASCADE,
      module_key TEXT NOT NULL REFERENCES modules(key),
      level TEXT NOT NULL CONSTRAINT ck_access_role_grants_level CHECK (level IN ('use', 'manage')),
      PRIMARY KEY (role_id, module_key)
    );

    CREATE TABLE user_access_roles (
      user_id UUID NOT NULL REFERENCES users(id),
      role_id UUID NOT NULL REFERENCES access_roles(id) ON DELETE CASCADE,
      PRIMARY KEY (user_id, role_id)
    );

    INSERT INTO access_roles (id, name, description, is_default) VALUES
      ('00000000-0000-4000-8000-000000000001', 'Operations',
       'Sees and manages everything: engagements, pipeline, team allocation.', false),
      ('00000000-0000-4000-8000-000000000002', 'Delivery',
       'Works the engagements they are on the team of.', true),
      ('00000000-0000-4000-8000-000000000003', 'Sales',
       'Sees the whole pipeline; edits their own opportunities.', false),
      ('00000000-0000-4000-8000-000000000004', 'Sales lead',
       'Edits and reassigns any opportunity; maintains the product catalog.', false),
      ('00000000-0000-4000-8000-000000000005', 'Resourcing',
       'Allocates people to engagements from the Team screens.', false);
    INSERT INTO access_role_grants VALUES
      ('00000000-0000-4000-8000-000000000001', 'engagements', 'manage'),
      ('00000000-0000-4000-8000-000000000001', 'opportunities', 'manage'),
      ('00000000-0000-4000-8000-000000000001', 'team', 'manage'),
      ('00000000-0000-4000-8000-000000000002', 'engagements', 'use'),
      ('00000000-0000-4000-8000-000000000003', 'opportunities', 'use'),
      ('00000000-0000-4000-8000-000000000004', 'opportunities', 'manage'),
      ('00000000-0000-4000-8000-000000000005', 'team', 'manage');

    ALTER TABLE users ADD COLUMN is_admin BOOLEAN NOT NULL DEFAULT false;
    ALTER TABLE users ADD COLUMN is_contractor BOOLEAN NOT NULL DEFAULT false;
    UPDATE users SET is_admin = true WHERE role = 'admin';
    UPDATE users SET is_contractor = true WHERE role = 'contractor';
    INSERT INTO user_access_roles
      SELECT id, '00000000-0000-4000-8000-000000000001' FROM users WHERE role = 'ops';
    INSERT INTO user_access_roles
      SELECT id, '00000000-0000-4000-8000-000000000002' FROM users WHERE role IN ('analyst', 'contractor');
    ALTER TABLE users DROP COLUMN role;
    DROP TYPE internal_role;
    """)


def downgrade() -> None:
    op.execute("""
    CREATE TYPE internal_role AS ENUM ('analyst', 'contractor', 'ops', 'admin');
    ALTER TABLE users ADD COLUMN role internal_role;
    UPDATE users SET role = 'analyst' WHERE user_type = 'internal';
    UPDATE users SET role = 'contractor' WHERE is_contractor;
    UPDATE users u SET role = 'ops' FROM user_access_roles r
      WHERE r.user_id = u.id AND r.role_id = '00000000-0000-4000-8000-000000000001';
    UPDATE users SET role = 'admin' WHERE is_admin;
    ALTER TABLE users DROP COLUMN is_contractor;
    ALTER TABLE users DROP COLUMN is_admin;
    DROP TABLE user_access_roles;
    DROP TABLE access_role_grants;
    DROP TABLE access_roles;
    DROP TABLE modules;
    """)
