"""Engagement boards: one big card per engagement, with grouping and sorting options
instead of a fixed stage-column layout (CLAUDE.md §6, 2026-09-25).

People juggling several engagements, migrations especially, don't think in stage columns.
So the board is a card grid the viewer can group (none / stage / client / owner) and sort
(closest deadline / alphabetical / longest running / stage). Types whose stages are hand-set
(quickstart) default to grouping by stage; types with derived, concurrent stages default
to no grouping.
"""

from dataclasses import dataclass, field
from datetime import date, datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import UAT_STATUS, Deliverable, Engagement, EngagementType, User
from app.services.deliverables import Progress, engagement_progress
from app.services.stages import ACTIVE, StageState, derive_board_keys

GROUPINGS = {"none": "No grouping", "stage": "Stage", "client": "Client", "owner": "Owner"}
SORTS = {"deadline": "Closest deadline", "alpha": "Alphabetical", "lifetime": "Longest running", "stage": "Stage"}


@dataclass
class BoardCard:
    engagement: Engagement
    progress: Progress
    stage_states: list[StageState]
    owner: User | None
    team: list[User]
    next_deadline: date | None = None
    next_deadline_item: str | None = None
    overdue: int = 0
    in_uat: int = 0
    kind_counts: list[tuple[str, int]] = field(default_factory=list)

    @property
    def active_stages(self) -> list[StageState]:
        return [s for s in self.stage_states if s.state == ACTIVE]

    @property
    def lifetime_days(self) -> int | None:
        start = self.engagement.started_at or self.engagement.created_at
        return (datetime.now(timezone.utc) - start).days if start else None

    @property
    def first_active_index(self) -> int:
        keys = derive_board_keys(self.stage_states)
        order = [s.key for s in self.stage_states]
        return order.index(keys[0]) if keys and keys[0] in order else len(order)


@dataclass
class BoardGroup:
    key: str
    label: str
    cards: list[BoardCard]


def derive_default_grouping(etype: EngagementType) -> str:
    return "none" if etype.stages_derived else "stage"


def derive_card(db: Session, engagement: Engagement, *, today: date | None = None,
                kind_counts: list[tuple[str, int]] | None = None) -> BoardCard:
    today = today or date.today()
    open_items = list(db.scalars(select(Deliverable).where(
        Deliverable.engagement_id == engagement.id, Deliverable.pipeline_status != "done",
        Deliverable.not_applicable.is_(False))))
    dated = sorted((d for d in open_items if d.target_date), key=lambda d: d.target_date)
    upcoming = [d for d in dated if d.target_date >= today]
    members = sorted((m for m in engagement.memberships if m.user.user_type == "internal"),
                     key=lambda m: (m.role != "owner", m.user.label))
    return BoardCard(
        engagement=engagement,
        progress=engagement_progress(db, engagement),
        stage_states=engagement.stage_states,
        owner=engagement.owner,
        team=[m.user for m in members],
        next_deadline=upcoming[0].target_date if upcoming else None,
        next_deadline_item=upcoming[0].name if upcoming else None,
        overdue=sum(1 for d in dated if d.target_date < today),
        in_uat=sum(1 for d in open_items if d.pipeline_status == UAT_STATUS),
        kind_counts=kind_counts or [],
    )


def _sort_key(sort: str):
    if sort == "alpha":
        return lambda c: (c.engagement.client.name.lower(), c.engagement.name.lower())
    if sort == "lifetime":
        far_future = datetime.max.replace(tzinfo=timezone.utc)
        return lambda c: (c.engagement.started_at or c.engagement.created_at or far_future, c.engagement.name)
    if sort == "stage":
        return lambda c: (c.first_active_index, c.engagement.name.lower())
    # closest deadline: overdue work first, then the nearest upcoming date; nothing dated goes last
    return lambda c: (c.overdue == 0, c.next_deadline or date.max, c.engagement.name.lower())


def derive_board(etype: EngagementType, cards: list[BoardCard], *, group: str, sort: str) -> list[BoardGroup]:
    group = group if group in GROUPINGS else derive_default_grouping(etype)
    sort = sort if sort in SORTS else "deadline"
    ordered = sorted(cards, key=_sort_key(sort))
    if group == "none":
        return [BoardGroup("all", "", ordered)]
    if group == "stage":
        # Stages can run concurrently, so a card appears under every stage it's active in.
        groups = {s["key"]: BoardGroup(s["key"], s["label"], []) for s in etype.stage_vocab}
        for c in ordered:
            for key in derive_board_keys(c.stage_states):
                groups[key].cards.append(c)
        return [g for g in groups.values() if g.cards]
    buckets: dict[str, BoardGroup] = {}
    for c in ordered:
        if group == "client":
            key, label = str(c.engagement.client_id), c.engagement.client.name
        else:
            key = str(c.owner.id) if c.owner else "none"
            label = c.owner.label if c.owner else "No owner"
        buckets.setdefault(key, BoardGroup(key, label, [])).cards.append(c)
    return sorted(buckets.values(), key=lambda g: (g.key == "none", g.label.lower()))

