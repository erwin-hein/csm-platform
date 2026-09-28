"""The semantic views are SQL twins of values the app derives in Python (CLAUDE.md §3 Semantic
layer). Python stays authoritative; this pins the views to it on the full demo dataset, so the
Malloy model can never show a number the app disagrees with."""

from collections import defaultdict

import pytest
from sqlalchemy import select, text

from app import seed
from app.models import Deliverable, Engagement, Opportunity, User
from app.services import deliverables as D
from app.services import stages as S


@pytest.fixture
def demo(db):
    """The full demo dataset, plus the progress edge cases it doesn't happen to contain."""
    seed.seed(db)
    admin = db.scalar(select(User).where(User.is_admin.is_(True)))
    mig = db.scalar(select(Engagement).where(Engagement.type_key == "migration").order_by(Engagement.created_at))
    counted_only = D.create_deliverable(db, admin, mig.id, kind="dashboard", name="Counted only", child_count=12)
    D.set_child_counts(db, admin, counted_only.id, counts={"done": 4, "in_progress": 2, "not_started": 6})
    all_na = D.create_deliverable(db, admin, mig.id, kind="dashboard", name="Only an N/A tile")
    tile = D.create_deliverable(db, admin, mig.id, kind="tile", name="Dropped tile", parent_id=all_na.id)
    D.update_deliverable(db, admin, tile.id, not_applicable=True)
    na_phase = D.create_deliverable(db, admin, mig.id, kind="phase", name="Skipped phase", stage_key="golive_wrapup")
    D.update_deliverable(db, admin, na_phase.id, not_applicable=True)
    db.flush()
    return db


def _rows(db, sql):
    return [dict(r._mapping) for r in db.execute(text(sql))]


def test_dep_state_signal_and_attention(demo):
    db = demo
    items = list(db.scalars(select(Deliverable)))
    blockers = D.blocker_map(db, [d.id for d in items])
    facts = {r["deliverable_id"]: r for r in _rows(db, "SELECT * FROM semantic.deliverable_facts")}
    assert len(facts) == len(items) > 50
    for d in items:
        state = D.derive_dep_state(d, blockers[d.id])
        signal = D.derive_dep_signal(d, state, blockers[d.id])
        f = facts[d.id]
        assert f["dep_state"] == state, d.name
        assert f["dep_signal"] == (signal.level if signal else None), d.name
        assert f["needs_attention"] == bool(signal and signal.needs_attention), d.name


def test_progress_units_match_engagement_progress(demo):
    db = demo
    units = defaultdict(lambda: {"done": 0, "total": 0, "by_status": defaultdict(int)})
    for r in _rows(db, "SELECT engagement_id, pipeline_status, units FROM semantic.work_units"):
        u = units[r["engagement_id"]]
        u["total"] += r["units"]
        u["by_status"][r["pipeline_status"]] += r["units"]
        u["done"] += r["units"] if r["pipeline_status"] == "done" else 0
    engagements = list(db.scalars(select(Engagement)))
    assert engagements
    for e in engagements:
        p = D.engagement_progress(db, e)
        u = units[e.id]
        assert (u["done"], u["total"]) == (p.done, p.applicable), e.name
        assert {k: v for k, v in u["by_status"].items() if v} == {k: v for k, v in p.by_status.items() if v}, e.name


def test_stage_states(demo):
    db = demo
    rows = _rows(db, "SELECT engagement_id, stage_key, state FROM semantic.engagement_stages ORDER BY position")
    by_engagement = defaultdict(list)
    for r in rows:
        by_engagement[r["engagement_id"]].append((r["stage_key"], r["state"]))
    for e in db.scalars(select(Engagement)):
        assert by_engagement[e.id] == [(s.key, s.state) for s in S.derive_stage_states(db, e)], e.name


def test_opportunity_facts(demo):
    db = demo
    facts = {r["opportunity_id"]: r for r in _rows(db, "SELECT * FROM semantic.opportunity_facts")}
    opps = list(db.scalars(select(Opportunity)))
    assert len(facts) == len(opps) > 5
    for o in opps:
        f = facts[o.id]
        assert f["amount"] == o.amount, o.name
        assert f["effective_probability"] == o.effective_probability, o.name
        assert f["effective_forecast_category"] == o.effective_forecast_category, o.name
        assert f["weighted_amount"] == o.weighted_amount, o.name
        assert f["is_overdue"] == o.is_overdue, o.name
        assert f["won_without_engagement"] == (o.is_won and not o.engagements), o.name
