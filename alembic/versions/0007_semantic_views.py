"""Semantic-layer views (CLAUDE.md §3 Semantic layer).

The Malloy model reads stored tables plus these views. Each view is the SQL form of a value
the app derives in Python, so derived states are queryable instead of trapped in the service
layer. Python stays authoritative: tests/test_semantic_views_match_python.py checks every
view against the Python derivation on the full demo data, so the two can't drift apart.

- semantic.deliverable_facts: per deliverable, dep_state and its display signal, needs-attention,
  overdue, blocker and child counts, and whether it counts as one unit of work in progress.
- semantic.work_units: the units progress is measured in: listed leaf deliverables plus children
  tracked by count on bulk-count parents (a 99-tile dashboard is 99 units).
- semantic.engagement_stages: per engagement × stage, the derived state (done/active/upcoming/empty).
- semantic.opportunity_facts: per opportunity, the amount (sum of line items), effective probability
  and forecast category, weighted amount, and the won-without-engagement flag.

child_counts can hold JSON null as well as SQL NULL, so it's only expanded when it's an object.

A later migration that changes a column these views read must drop and recreate the affected
views (Postgres refuses to alter a column a view depends on).

Revision ID: 0007
Revises: 0006
"""

from alembic import op

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None

STATUSES = "('not_started','in_progress','internal_validation','external_validation','done')"

VIEWS = f"""
CREATE SCHEMA semantic;
COMMENT ON SCHEMA semantic IS 'Read-only views for the Malloy semantic layer; SQL twins of the app''s derived values.';

CREATE VIEW semantic.deliverable_facts AS
WITH blockers AS (
  SELECT b.blocked_id,
         count(*) AS blocker_count,
         count(*) FILTER (WHERE NOT (x.pipeline_status = 'done' OR x.not_applicable)) AS open_blocker_count
  FROM deliverable_blockers b JOIN deliverables x ON x.id = b.blocker_id
  GROUP BY b.blocked_id
), children AS (
  SELECT parent_id, count(*) AS listed_children FROM deliverables WHERE parent_id IS NOT NULL GROUP BY parent_id
), counted AS (
  SELECT d.id,
         sum(c.value::int) AS counted_children,
         sum(c.value::int) FILTER (WHERE c.key = 'done') AS counted_done
  FROM deliverables d CROSS JOIN LATERAL jsonb_each_text(
    CASE WHEN jsonb_typeof(d.child_counts) = 'object' THEN d.child_counts ELSE '{{}}'::jsonb END) AS c(key, value)
  WHERE c.key IN {STATUSES}
  GROUP BY d.id
), base AS (
  SELECT d.*, e.client_id, k.display_name AS kind_label, k.client_visible,
         EXISTS (SELECT 1 FROM deliverable_kinds ck
                 WHERE ck.engagement_type_key = d.engagement_type_key AND ck.parent_kind = d.kind) AS has_child_kind,
         COALESCE(bl.blocker_count, 0) AS blocker_count, COALESCE(bl.open_blocker_count, 0) AS open_blocker_count,
         COALESCE(ch.listed_children, 0) AS listed_children,
         COALESCE(ct.counted_children, 0) AS counted_children, COALESCE(ct.counted_done, 0) AS counted_done
  FROM deliverables d
  JOIN engagements e ON e.id = d.engagement_id
  JOIN deliverable_kinds k ON k.engagement_type_key = d.engagement_type_key AND k.kind = d.kind
  LEFT JOIN blockers bl ON bl.blocked_id = d.id
  LEFT JOIN children ch ON ch.parent_id = d.id
  LEFT JOIN counted ct ON ct.id = d.id
)
SELECT id AS deliverable_id, engagement_id, client_id, engagement_type_key, parent_id, kind, kind_label, name,
       pipeline_status, blocked, blocked_reason, not_applicable, internal_assignee_user_id, client_owner_user_id,
       priority, target_date, hours_estimated, stage_key, client_visible, created_at,
       (pipeline_status = 'done' OR not_applicable) AS is_finished,
       blocker_count, open_blocker_count,
       CASE WHEN blocked THEN CASE WHEN blocker_count > 0 AND open_blocker_count = 0
                                   THEN 'maybe_unblocked' ELSE 'blocked' END
            WHEN open_blocker_count > 0 THEN 'waiting'
            ELSE 'clear' END AS dep_state,
       CASE WHEN blocked AND NOT (blocker_count > 0 AND open_blocker_count = 0) THEN 'alert'
            WHEN blocked THEN 'check'
            WHEN open_blocker_count > 0 AND pipeline_status <> 'not_started' THEN 'warn'
            WHEN open_blocker_count > 0 THEN 'info' END AS dep_signal,
       (blocked OR (open_blocker_count > 0 AND pipeline_status <> 'not_started')) AS needs_attention,
       (target_date < current_date AND NOT (pipeline_status = 'done' OR not_applicable)) AS is_overdue,
       listed_children, counted_children, counted_done,
       -- One unit of leaf-level work in progress: a child, a root with no child kind, or a
       -- root of a parent kind that has nothing listed or counted under it yet.
       (NOT not_applicable AND (parent_id IS NOT NULL OR NOT has_child_kind
                                OR (listed_children = 0 AND counted_children = 0))) AS is_work_unit
FROM base;

CREATE VIEW semantic.work_units AS
SELECT f.deliverable_id, f.engagement_id, f.client_id, f.kind, f.pipeline_status, 'listed' AS source, 1 AS units
FROM semantic.deliverable_facts f
WHERE f.is_work_unit
UNION ALL
SELECT d.id, d.engagement_id, e.client_id, ck.kind, c.key, 'counted', c.value::int
FROM deliverables d
JOIN engagements e ON e.id = d.engagement_id
JOIN deliverable_kinds ck ON ck.engagement_type_key = d.engagement_type_key AND ck.parent_kind = d.kind
CROSS JOIN LATERAL jsonb_each_text(
  CASE WHEN jsonb_typeof(d.child_counts) = 'object' THEN d.child_counts ELSE '{{}}'::jsonb END) AS c(key, value)
WHERE d.parent_id IS NULL AND c.key IN {STATUSES} AND c.value::int > 0;

CREATE VIEW semantic.engagement_stages AS
WITH vocab AS (
  SELECT e.id AS engagement_id, e.client_id, e.type_key, t.stage_kind, e.stage AS current_stage,
         (s.ord - 1)::int AS position, s.stage->>'key' AS stage_key, s.stage->>'label' AS stage_label
  FROM engagements e
  JOIN engagement_types t ON t.key = e.type_key
  CROSS JOIN LATERAL jsonb_array_elements(t.stage_vocab) WITH ORDINALITY AS s(stage, ord)
), current_position AS (
  SELECT engagement_id, position FROM vocab WHERE stage_key = current_stage
), linked AS (
  SELECT d.engagement_id, d.stage_key, count(*) AS linked_count,
         bool_and(d.pipeline_status = 'done') AS all_finished,
         bool_or(d.pipeline_status <> 'not_started' OR EXISTS (
           SELECT 1 FROM deliverables c
           WHERE c.parent_id = d.id AND NOT c.not_applicable AND c.pipeline_status <> 'not_started')) AS any_started
  FROM deliverables d
  JOIN engagements e ON e.id = d.engagement_id
  JOIN engagement_types t ON t.key = e.type_key
  WHERE d.kind = t.stage_kind AND d.stage_key IS NOT NULL AND NOT d.not_applicable
  GROUP BY d.engagement_id, d.stage_key
)
SELECT v.engagement_id, v.client_id, v.type_key, v.position, v.stage_key, v.stage_label,
       (v.stage_kind IS NOT NULL) AS derived,
       CASE WHEN v.stage_kind IS NULL THEN
              CASE WHEN cp.position IS NULL OR v.position > cp.position THEN 'upcoming'
                   WHEN v.position < cp.position THEN 'done'
                   ELSE 'active' END
            ELSE
              CASE WHEN l.linked_count IS NULL THEN 'empty'
                   WHEN l.all_finished THEN 'done'
                   WHEN l.any_started THEN 'active'
                   ELSE 'upcoming' END
       END AS state,
       COALESCE(l.linked_count, 0) AS linked_count
FROM vocab v
LEFT JOIN current_position cp ON cp.engagement_id = v.engagement_id
LEFT JOIN linked l ON l.engagement_id = v.engagement_id AND l.stage_key = v.stage_key;

CREATE VIEW semantic.opportunity_facts AS
WITH items AS (
  SELECT opportunity_id, sum(quantity * unit_price) AS amount, count(*) AS line_items
  FROM opportunity_line_items GROUP BY opportunity_id
), facts AS (
  SELECT o.*, s.label AS stage_label, s.sort_order AS stage_order, s.is_closed, s.is_won,
         COALESCE(i.amount, 0) AS amount, COALESCE(i.line_items, 0) AS line_items,
         CASE WHEN s.is_closed OR o.probability IS NULL THEN s.default_probability ELSE o.probability END
           AS effective_probability,
         CASE WHEN s.is_closed OR o.forecast_category IS NULL THEN s.forecast_category ELSE o.forecast_category END
           AS effective_forecast_category
  FROM opportunities o
  JOIN opportunity_stages s ON s.key = o.stage_key
  LEFT JOIN items i ON i.opportunity_id = o.id
)
SELECT id AS opportunity_id, client_id, name, owner_user_id, stage_key, stage_label, stage_order,
       is_closed, is_won, (is_closed AND NOT is_won) AS is_lost,
       close_date, closed_at, created_at, source, next_step, lost_reason,
       amount, line_items, effective_probability, effective_forecast_category,
       amount * effective_probability / 100 AS weighted_amount,
       (NOT is_closed AND close_date < current_date) AS is_overdue,
       (is_won AND NOT EXISTS (SELECT 1 FROM engagements e WHERE e.opportunity_id = facts.id)) AS won_without_engagement
FROM facts;
"""


def upgrade() -> None:
    op.execute(VIEWS)


def downgrade() -> None:
    op.execute("DROP SCHEMA semantic CASCADE")
