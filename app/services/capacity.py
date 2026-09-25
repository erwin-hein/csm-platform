"""Team capacity: assignments grouped per person, for ops/admin to judge who has room
for more work. Read-only; allocation goes through services.memberships.

"Load" is built only from data the PoC has — team memberships on open engagements and
the open deliverables assigned to each person (with their estimated hours and target
dates). There is no time tracking yet, so this is planned work, not hours logged.
"""

import uuid
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.access import require_module
from app.models import Deliverable, Engagement, EngagementMembership, User

OPEN_ENGAGEMENT_STATUSES = ("active", "paused")
DUE_SOON_DAYS = 14


@dataclass
class Allocation:
    engagement: Engagement
    role: str
    open_items: int = 0
    open_hours: Decimal = Decimal(0)


@dataclass
class PersonLoad:
    user: User
    allocations: list[Allocation] = field(default_factory=list)
    open_items: int = 0
    open_hours: Decimal = Decimal(0)
    unestimated: int = 0
    overdue: int = 0
    due_soon: int = 0

    def count(self, role: str) -> int:
        return sum(1 for a in self.allocations if a.role == role)


def list_capacity(db: Session, actor: User, *, today: date | None = None) -> list[PersonLoad]:
    require_module(actor, "team")
    today = today or date.today()
    # Only people who can work engagements carry engagement load.
    people = {u.id: PersonLoad(u) for u in db.scalars(
        select(User).where(User.user_type == "internal", User.status == "active")) if u.can("engagements")}

    allocations: dict[tuple[uuid.UUID, uuid.UUID], Allocation] = {}
    for m, e in db.execute(
        select(EngagementMembership, Engagement).join(Engagement, Engagement.id == EngagementMembership.engagement_id)
        .where(Engagement.status.in_(OPEN_ENGAGEMENT_STATUSES))
    ):
        if m.user_id in people:
            a = Allocation(e, m.role)
            allocations[(m.user_id, e.id)] = a
            people[m.user_id].allocations.append(a)

    for d in db.scalars(
        select(Deliverable).join(Engagement, Engagement.id == Deliverable.engagement_id).where(
            Engagement.status.in_(OPEN_ENGAGEMENT_STATUSES), Deliverable.internal_assignee_user_id.is_not(None),
            Deliverable.pipeline_status != "done", Deliverable.not_applicable.is_(False))
    ):
        p = people.get(d.internal_assignee_user_id)
        if p is None:
            continue
        hours = d.hours_estimated or Decimal(0)
        p.open_items += 1
        p.open_hours += hours
        p.unestimated += d.hours_estimated is None
        if d.target_date and d.target_date < today:
            p.overdue += 1
        elif d.target_date and d.target_date <= today + timedelta(days=DUE_SOON_DAYS):
            p.due_soon += 1
        a = allocations.get((p.user.id, d.engagement_id))
        if a:
            a.open_items += 1
            a.open_hours += hours

    order = {"owner": 0, "collaborator": 1, "viewer": 2}
    for p in people.values():
        p.allocations.sort(key=lambda a: (order[a.role], a.engagement.client.name, a.engagement.name))
    return sorted(people.values(), key=lambda p: (p.user.bypasses_membership, p.user.label))


def list_all_engagements(db: Session) -> list[Engagement]:
    """Every engagement, for the Team screens (team:use sees who is on what, even without
    engagements:manage)."""
    return list(db.scalars(select(Engagement).order_by(Engagement.created_at)))


def list_open_engagements(db: Session) -> list[Engagement]:
    return list(db.scalars(select(Engagement).where(Engagement.status.in_(OPEN_ENGAGEMENT_STATUSES))
                           .order_by(Engagement.name)))
