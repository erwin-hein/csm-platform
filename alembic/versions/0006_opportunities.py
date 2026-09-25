"""CRM slice: opportunities, products, line items (CLAUDE.md §3 Opportunities).

- opportunity_stages: the configurable pipeline. Each stage carries a default probability
  and forecast category; an opportunity can override both while it's open.
- opportunities: belong to a client (prospects are ordinary clients rows) and have one owner.
- products / opportunity_line_items: every opportunity has at least one line item (enforced
  in the service layer). An opportunity's amount is the sum of its line items, never stored.
- engagements.opportunity_id: the optional bridge from a won opportunity to the engagement(s)
  delivering it. Bridged, never merged.

USD only for now: amounts carry no currency column.

Revision ID: 0006
Revises: 0005
"""

from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
    CREATE TABLE opportunity_stages (
      key TEXT PRIMARY KEY,
      label TEXT NOT NULL,
      sort_order INT NOT NULL,
      default_probability INT NOT NULL CHECK (default_probability BETWEEN 0 AND 100),
      forecast_category TEXT NOT NULL
        CHECK (forecast_category IN ('pipeline', 'best_case', 'commit', 'closed', 'omitted')),
      is_closed BOOLEAN NOT NULL DEFAULT false,
      is_won BOOLEAN NOT NULL DEFAULT false,
      CHECK (NOT is_won OR is_closed)
    );
    INSERT INTO opportunity_stages VALUES
      ('prospecting',   'Prospecting',   1,  10, 'pipeline',  false, false),
      ('qualification', 'Qualification', 2,  20, 'pipeline',  false, false),
      ('discovery',     'Discovery',     3,  40, 'pipeline',  false, false),
      ('proposal',      'Proposal',      4,  60, 'best_case', false, false),
      ('negotiation',   'Negotiation',   5,  80, 'commit',    false, false),
      ('closed_won',    'Closed Won',    6, 100, 'closed',    true,  true),
      ('closed_lost',   'Closed Lost',   7,   0, 'omitted',   true,  false);

    CREATE TABLE products (
      id UUID PRIMARY KEY,
      name TEXT UNIQUE NOT NULL,
      pricing_model TEXT NOT NULL CHECK (pricing_model IN ('fixed_bid', 'time_and_materials', 'retainer')),
      default_unit_price NUMERIC(12, 2) CHECK (default_unit_price >= 0),
      engagement_type_key TEXT REFERENCES engagement_types(key),   -- what it's delivered as, if anything
      active BOOLEAN NOT NULL DEFAULT true,
      created_at TIMESTAMPTZ DEFAULT now()
    );

    CREATE TABLE opportunities (
      id UUID PRIMARY KEY,
      client_id UUID NOT NULL REFERENCES clients(id),
      name TEXT NOT NULL,
      owner_user_id UUID REFERENCES users(id),
      stage_key TEXT NOT NULL REFERENCES opportunity_stages(key),
      probability INT CHECK (probability BETWEEN 0 AND 100),          -- override; NULL = the stage's default
      forecast_category TEXT                                           -- override; NULL = the stage's default
        CHECK (forecast_category IN ('pipeline', 'best_case', 'commit', 'omitted')),
      close_date DATE NOT NULL,          -- expected while open; the actual date once closed
      next_step TEXT,
      source TEXT,
      lost_reason TEXT,
      closed_at TIMESTAMPTZ,
      created_at TIMESTAMPTZ DEFAULT now()
    );
    CREATE INDEX ix_opportunities_client ON opportunities (client_id);

    CREATE TABLE opportunity_line_items (
      id UUID PRIMARY KEY,
      opportunity_id UUID NOT NULL REFERENCES opportunities(id),
      product_id UUID NOT NULL REFERENCES products(id),
      quantity NUMERIC(12, 2) NOT NULL CHECK (quantity > 0),
      unit_price NUMERIC(12, 2) NOT NULL CHECK (unit_price >= 0),
      description TEXT,
      created_at TIMESTAMPTZ DEFAULT now()
    );
    CREATE INDEX ix_opportunity_line_items_opportunity ON opportunity_line_items (opportunity_id);

    ALTER TABLE engagements ADD COLUMN opportunity_id UUID REFERENCES opportunities(id);
    """)


def downgrade() -> None:
    op.execute("""
    ALTER TABLE engagements DROP COLUMN opportunity_id;
    DROP TABLE opportunity_line_items;
    DROP TABLE opportunities;
    DROP TABLE products;
    DROP TABLE opportunity_stages;
    """)
