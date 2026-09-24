"""The demo seed covers every state the walkthrough needs, so nothing demos empty."""

from collections import Counter

from sqlalchemy import select

from app import seed
from app.models import Deliverable, Engagement, EngagementMembership, Event
from app.services.deliverables import blocker_map, derive_dep_state


def test_seed_shape(db):
    seed.seed(db)
    db.flush()
    engagements = list(db.scalars(select(Engagement)))
    types = Counter(e.type_key for e in engagements)
    assert types["quickstart"] >= 2 and types["migration"] >= 2

    unassigned = [e for e in engagements if not db.scalar(
        select(EngagementMembership).where(EngagementMembership.engagement_id == e.id))]
    assert len(unassigned) >= 1

    ds = list(db.scalars(select(Deliverable)))
    assert {d.pipeline_status for d in ds} == {"not_started", "in_progress", "internal_validation",
                                               "external_validation", "done"}
    assert {d.kind for d in ds} == {"module", "phase", "milestone", "dashboard", "tile"}
    blockers = blocker_map(db, [d.id for d in ds])
    states = {derive_dep_state(d, blockers[d.id]) for d in ds}
    assert states == {"clear", "waiting", "blocked", "maybe_unblocked"}
    assert any(d.blocked and d.blocked_reason and blockers[d.id] for d in ds)
    # seeded through services, so the spine is populated too
    assert db.scalar(select(Event).where(Event.event_type == "deliverable_created"))
