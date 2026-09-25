"""Stage state for an engagement — one mechanism for every type (CLAUDE.md §3).

Every engagement has a *set* of active stages. A linear type is simply one whose work
runs one stage at a time. Where the type declares a stage kind (migration → phase), each
stage's state is derived from the deliverables of that kind that stand for it, so several
stages can be active at once and nothing is moved by hand. Otherwise (quickstart) the
engagement's hand-set `stage` decides: stages before it are done, it is active, the
rest are upcoming.
"""

from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Deliverable, Engagement

DONE, ACTIVE, UPCOMING, EMPTY = "done", "active", "upcoming", "empty"


@dataclass
class StageState:
    key: str
    label: str
    state: str                       # done | active | upcoming | empty (derived types with nothing linked)
    items: list[Deliverable] = field(default_factory=list)


def _finished(d: Deliverable) -> bool:
    return d.pipeline_status == "done" or d.not_applicable


def _started(d: Deliverable, child_statuses: list[str]) -> bool:
    return d.pipeline_status != "not_started" or any(s != "not_started" for s in child_statuses)


def derive_stage_states(db: Session, engagement: Engagement) -> list[StageState]:
    vocab = engagement.type.stage_vocab
    if not engagement.type.stages_derived:
        keys = [s["key"] for s in vocab]
        cur = keys.index(engagement.stage) if engagement.stage in keys else -1
        return [StageState(s["key"], s["label"], DONE if i < cur else ACTIVE if i == cur else UPCOMING)
                for i, s in enumerate(vocab)]
    items = list(db.scalars(select(Deliverable).where(
        Deliverable.engagement_id == engagement.id, Deliverable.kind == engagement.type.stage_kind,
        Deliverable.stage_key.is_not(None)).order_by(Deliverable.created_at))) if db else []
    # Queried rather than read off d.children, so it's right mid-transaction too.
    child_statuses: dict = {d.id: [] for d in items}
    if items:
        for parent_id, status in db.execute(select(Deliverable.parent_id, Deliverable.pipeline_status).where(
                Deliverable.parent_id.in_(child_statuses), Deliverable.not_applicable.is_(False))):
            child_statuses[parent_id].append(status)
    states = []
    for s in vocab:
        linked = [d for d in items if d.stage_key == s["key"] and not d.not_applicable]
        if not linked:
            state = EMPTY
        elif all(_finished(d) for d in linked):
            state = DONE
        elif any(_started(d, child_statuses[d.id]) for d in linked):
            state = ACTIVE
        else:
            state = UPCOMING
        states.append(StageState(s["key"], s["label"], state, linked))
    return states


def derive_board_keys(states: list[StageState]) -> list[str]:
    """Which board columns an engagement's card appears in: every active stage; with
    nothing active, the first stage that isn't done yet (or the last one, if all are)."""
    active = [s.key for s in states if s.state == ACTIVE]
    if active:
        return active
    pending = [s.key for s in states if s.state in (UPCOMING, EMPTY)]
    return pending[:1] or [states[-1].key]


def derive_stage_label(states: list[StageState]) -> str:
    active = [s.label for s in states if s.state == ACTIVE]
    if active:
        return " + ".join(active)
    if all(s.state == DONE for s in states if s.state != EMPTY) and any(s.state == DONE for s in states):
        return "All stages done"
    nxt = next((s.label for s in states if s.state in (UPCOMING, EMPTY)), None)
    return f"Not started · next: {nxt}" if nxt else "Not started"


def derive_active_keys(states: list[StageState]) -> set[str]:
    return {s.key for s in states if s.state == ACTIVE}
