"""The generalized deliverables tracking engine — CLAUDE.md §2 row 3 / §3.

One tree/pipeline structure for every engagement type. What differs per type is
data, not code: `deliverable_kinds` says which kinds a type allows and which kind
may parent which (e.g. migration tile -> dashboard; quickstart modules are flat).
"""

import uuid
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, InvalidOperation

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.access import (
    can_see_engagement,
    client_membership,
    client_visible_kinds,
    get_visible_deliverable,
    get_editable_deliverable,
    get_editable_engagement,
)
from app.errors import ValidationError
from app.events import emit, mutation
from app.services.stages import derive_stage_states
from app.services import client_portal
from app.models import (
    PIPELINE_STATUSES,
    Deliverable,
    DeliverableActivity,
    DeliverableBlocker,
    DeliverableKind,
    Engagement,
    User,
)

PIPELINE_LABELS = {
    "not_started": "Not started",
    "in_progress": "In progress",
    "internal_validation": "Internal validation",
    "external_validation": "External validation",
    "done": "Done",
}
PRIORITIES = ("low", "medium", "high")

# dep_state vocabulary (CLAUDE.md §3 deliverable_blockers) — derived, never stored.
DEP_BLOCKED = "blocked"                  # manually flagged, and nothing suggests it's resolved
DEP_WAITING = "waiting"                  # not flagged, but waiting on an unfinished blocker
DEP_MAYBE_UNBLOCKED = "maybe_unblocked"  # flagged, but every blocker it waits on is finished — flag likely stale
DEP_CLEAR = "clear"


def _resolved(d: Deliverable) -> bool:
    return d.pipeline_status == "done" or d.not_applicable


def derive_dep_state(deliverable: Deliverable, blockers: list[Deliverable]) -> str:
    open_blockers = [b for b in blockers if not _resolved(b)]
    if deliverable.blocked:
        if blockers and not open_blockers:
            return DEP_MAYBE_UNBLOCKED
        return DEP_BLOCKED
    if open_blockers:
        return DEP_WAITING
    return DEP_CLEAR


@dataclass(frozen=True)
class DepSignal:
    """How loudly to show a dep_state (CLAUDE.md §3). Sequencing isn't urgency: an item
    that waits on unfinished work but hasn't started is just ordered after it."""

    level: str   # 'alert' (red) | 'check' (yellow) | 'warn' (amber) | 'info' (grey, quiet)
    label: str
    detail: str

    @property
    def needs_attention(self) -> bool:
        return self.level != "info"


def derive_dep_signal(deliverable: Deliverable, dep_state: str, blockers: list[Deliverable]) -> DepSignal | None:
    open_names = ", ".join(b.name for b in blockers if not _resolved(b))
    if dep_state == DEP_BLOCKED:
        return DepSignal("alert", "Blocked", deliverable.blocked_reason or "Flagged blocked")
    if dep_state == DEP_MAYBE_UNBLOCKED:
        return DepSignal("check", "Check block flag",
                         f"Still flagged blocked, but everything it waits on is finished ({deliverable.blocked_reason})")
    if dep_state == DEP_WAITING:
        if deliverable.pipeline_status == "not_started":
            return DepSignal("info", f"After {open_names}", f"Scheduled after {open_names}")
        return DepSignal("warn", "Waiting on dependency", f"Work has started, but it still waits on {open_names}")
    return None


# ---------------------------------------------------------------- reads


def kinds_for_type(db: Session, type_key: str) -> list[DeliverableKind]:
    kinds = list(db.scalars(select(DeliverableKind).where(DeliverableKind.engagement_type_key == type_key)))
    # roots first, each followed by its child kinds
    roots = [k for k in kinds if k.parent_kind is None]
    ordered = []
    for root in sorted(roots, key=lambda k: k.kind):
        ordered.append(root)
        ordered.extend(k for k in kinds if k.parent_kind == root.kind)
    return ordered


def blocker_map(db: Session, deliverable_ids: list[uuid.UUID]) -> dict[uuid.UUID, list[Deliverable]]:
    """blocked_id -> [blocker deliverables], for a batch of deliverables."""
    out: dict[uuid.UUID, list[Deliverable]] = {i: [] for i in deliverable_ids}
    if not deliverable_ids:
        return out
    rows = db.execute(
        select(DeliverableBlocker.blocked_id, Deliverable)
        .join(Deliverable, Deliverable.id == DeliverableBlocker.blocker_id)
        .where(DeliverableBlocker.blocked_id.in_(deliverable_ids))
    )
    for blocked_id, blocker in rows:
        out[blocked_id].append(blocker)
    return out


def blocking_list(db: Session, deliverable_id: uuid.UUID) -> list[Deliverable]:
    """Deliverables that wait on this one."""
    return list(db.scalars(
        select(Deliverable).join(DeliverableBlocker, DeliverableBlocker.blocked_id == Deliverable.id)
        .where(DeliverableBlocker.blocker_id == deliverable_id)
    ))


@dataclass
class Progress:
    done: int = 0
    applicable: int = 0
    by_status: dict[str, int] = field(default_factory=lambda: {s: 0 for s in PIPELINE_STATUSES})
    attention: int = 0  # items whose dependency signal needs attention (not quiet sequencing)

    @property
    def pct(self) -> int:
        return round(100 * self.done / self.applicable) if self.applicable else 0

    def add_counts(self, counts: dict | None) -> None:
        """Children tracked by count rather than listed individually (bulk_child_counts)."""
        for status, n in (counts or {}).items():
            if status in self.by_status and n:
                self.applicable += n
                self.by_status[status] += n
                if status == "done":
                    self.done += n

    def add(self, d: Deliverable, signal: "DepSignal | None") -> None:
        if d.not_applicable:
            return
        self.applicable += 1
        self.by_status[d.pipeline_status] += 1
        if d.pipeline_status == "done":
            self.done += 1
        if signal and signal.needs_attention:
            self.attention += 1


@dataclass
class Node:
    deliverable: Deliverable
    dep_state: str
    blockers: list[Deliverable]
    signal: DepSignal | None = None
    note_count: int = 0
    client_visible: bool = False
    verdict: object | None = None      # latest DeliverableClientReview, if any
    client_comment_count: int = 0
    children: list["Node"] = field(default_factory=list)
    progress: Progress = field(default_factory=Progress)  # over children, for parent nodes


@dataclass
class Section:
    kind: DeliverableKind
    child_kind: DeliverableKind | None
    nodes: list[Node]
    progress: Progress


@dataclass
class EngagementTree:
    sections: list[Section]
    progress: Progress  # over leaf-level work across the whole engagement


def engagement_tree(db: Session, engagement: Engagement) -> EngagementTree:
    """Build the per-type view of an engagement's deliverables, driven entirely by
    the type's deliverable_kinds rows."""
    items = list(db.scalars(
        select(Deliverable).where(Deliverable.engagement_id == engagement.id).order_by(Deliverable.created_at)
    ))
    blockers = blocker_map(db, [d.id for d in items])
    ids = [d.id for d in items]
    notes = get_note_counts(db, ids)
    client_kinds = client_visible_kinds(db, engagement.type_key)
    verdicts, client_counts = client_portal.get_latest_verdicts(db, ids), client_portal.get_comment_counts(db, ids)
    nodes = {}
    for d in items:
        state = derive_dep_state(d, blockers[d.id])
        nodes[d.id] = Node(d, state, blockers[d.id], derive_dep_signal(d, state, blockers[d.id]), notes.get(d.id, 0),
                           d.kind in client_kinds, verdicts.get(d.id), client_counts.get(d.id, 0))
    for node in nodes.values():
        parent_id = node.deliverable.parent_id
        if parent_id in nodes:
            nodes[parent_id].children.append(node)
            nodes[parent_id].progress.add(node.deliverable, node.signal)
    for node in nodes.values():
        node.progress.add_counts(node.deliverable.child_counts)

    kinds = kinds_for_type(db, engagement.type_key)
    overall = Progress()
    sections = []
    for kind in (k for k in kinds if k.parent_kind is None):
        child_kind = next((k for k in kinds if k.parent_kind == kind.kind), None)
        roots = [n for n in nodes.values() if n.deliverable.kind == kind.kind and n.deliverable.parent_id is None]
        section_progress = Progress()
        for root in roots:
            counts = root.deliverable.child_counts if child_kind else None
            leaves = root.children if child_kind else [root]
            if not leaves and not any((counts or {}).values()):
                leaves = [root]
            for leaf in leaves:
                section_progress.add(leaf.deliverable, leaf.signal)
                overall.add(leaf.deliverable, leaf.signal)
            section_progress.add_counts(counts)
            overall.add_counts(counts)
        sections.append(Section(kind, child_kind, roots, section_progress))
    # Sections appear in the order the engagement's work was first laid out; empty ones last.
    sections.sort(key=lambda s: (not s.nodes, s.nodes[0].deliverable.created_at if s.nodes else None, s.kind.kind))
    return EngagementTree(sections, overall)


def engagement_progress(db: Session, engagement: Engagement) -> Progress:
    return engagement_tree(db, engagement).progress


def get_note_counts(db: Session, deliverable_ids: list[uuid.UUID]) -> dict[uuid.UUID, int]:
    if not deliverable_ids:
        return {}
    rows = db.execute(
        select(DeliverableActivity.deliverable_id, func.count())
        .where(DeliverableActivity.deliverable_id.in_(deliverable_ids), DeliverableActivity.kind == "note")
        .group_by(DeliverableActivity.deliverable_id)
    )
    return dict(rows.all())


def list_activity(db: Session, deliverable_id: uuid.UUID) -> list[DeliverableActivity]:
    return list(db.scalars(
        select(DeliverableActivity).where(DeliverableActivity.deliverable_id == deliverable_id)
        .order_by(DeliverableActivity.created_at.desc())
    ))


def blocker_candidates(db: Session, deliverable: Deliverable) -> list[Deliverable]:
    existing = {b.id for b in blocker_map(db, [deliverable.id])[deliverable.id]}
    return [
        d for d in db.scalars(
            select(Deliverable).where(Deliverable.engagement_id == deliverable.engagement_id)
            .order_by(Deliverable.kind, Deliverable.name)
        )
        if d.id != deliverable.id and d.id not in existing
    ]


def parent_candidates(db: Session, engagement: Engagement) -> dict[str, list[Deliverable]]:
    """child kind -> deliverables that may parent it."""
    out: dict[str, list[Deliverable]] = {}
    for kind in kinds_for_type(db, engagement.type_key):
        if kind.parent_kind:
            out[kind.kind] = list(db.scalars(
                select(Deliverable).where(
                    Deliverable.engagement_id == engagement.id,
                    Deliverable.kind == kind.parent_kind,
                    Deliverable.parent_id.is_(None),
                ).order_by(Deliverable.created_at)
            ))
    return out


# ---------------------------------------------------------------- writes


def _log(db: Session, deliverable: Deliverable, actor: User, kind: str, body: str) -> None:
    db.add(DeliverableActivity(deliverable_id=deliverable.id, actor_user_id=actor.id, kind=kind, body=body))


def _validate_assignee(db: Session, engagement_id: uuid.UUID, user_id: uuid.UUID | None) -> None:
    if user_id is None:
        return
    user = db.get(User, user_id)
    if user is None or user.user_type != "internal":
        raise ValidationError("Assignee must be an internal user")
    if not can_see_engagement(db, user, engagement_id):
        raise ValidationError(f"{user.label} isn't on this engagement's team — assign them first")


def _validate_client_owner(db: Session, d: Deliverable, user_id: uuid.UUID | None) -> None:
    """The client-side owner (QA / sign-off) must be a client user with access to this
    engagement, and the deliverable's kind must be one clients can see."""
    if user_id is None:
        return
    if d.kind not in client_visible_kinds(db, d.engagement_type_key):
        raise ValidationError("Clients can't see this kind of deliverable, so it can't have a client owner")
    user = db.get(User, user_id)
    if user is None or user.user_type != "client_external" or client_membership(db, user, d.engagement_id) is None:
        raise ValidationError("The client owner must be a client user with access to this engagement")


def _validate_stage_key(engagement: Engagement, kind: str, stage_key: str | None) -> str | None:
    """Deliverables of the type's stage kind must say which stage they stand for; no
    other kind may carry one."""
    etype = engagement.type
    if etype.stage_kind != kind:
        if stage_key:
            raise ValidationError(f"Only {etype.stage_kind or 'stage'} deliverables are linked to a stage")
        return None
    if not stage_key:
        raise ValidationError(f"Pick which stage this {kind} stands for")
    if stage_key not in etype.stage_keys:
        raise ValidationError(f"'{stage_key}' is not a {etype.display_name} stage")
    return stage_key


def _stage_snapshot(db: Session, engagement: Engagement, kind: str, parent_kind: str | None = None) -> dict | None:
    """Stage states before a change that can move them (a stage-kind deliverable or its child)."""
    if engagement.type.stage_kind is None or engagement.type.stage_kind not in (kind, parent_kind):
        return None
    return {s.key: s.state for s in derive_stage_states(db, engagement)}


def _emit_stage_changes(db: Session, actor: User, engagement: Engagement, before: dict | None) -> None:
    """Derived stages still land on the spine: when a deliverable change moves a stage's
    state, record it (the rules engine's stage_age reads these)."""
    if before is None:
        return
    db.flush()
    after = {s.key: s.state for s in derive_stage_states(db, engagement)}
    changes = [{"stage": k, "from": before.get(k), "to": v} for k, v in after.items() if before.get(k) != v]
    if changes:
        emit(db, entity_type="engagement", entity_id=engagement.id, event_type="stage_state_changed", actor=actor,
             payload={"changes": changes})


def _parse_count(value: int | str) -> int:
    try:
        n = int(value)
    except (TypeError, ValueError):
        raise ValidationError(f"'{value}' is not a whole number")
    if n < 0:
        raise ValidationError("Counts can't be negative")
    return n


def _parse_date(value: str | date | None) -> date | None:
    if value in (None, ""):
        return None
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise ValidationError(f"'{value}' is not a valid date")


def _parse_hours(value: str | Decimal | None) -> Decimal | None:
    if value in (None, ""):
        return None
    try:
        hours = Decimal(str(value))
    except InvalidOperation:
        raise ValidationError(f"'{value}' is not a number of hours")
    if hours < 0:
        raise ValidationError("Hours can't be negative")
    return hours


@mutation("deliverable_created")
def create_deliverable(db: Session, actor: User, engagement_id: uuid.UUID, *, kind: str, name: str,
                       parent_id: uuid.UUID | None = None, internal_assignee_user_id: uuid.UUID | None = None,
                       priority: str | None = None, target_date: str | date | None = None,
                       hours_estimated: str | Decimal | None = None, stage_key: str | None = None,
                       child_count: int | str | None = None) -> Deliverable:
    engagement = get_editable_engagement(db, actor, engagement_id)
    name = name.strip()
    if not name:
        raise ValidationError("Deliverable name is required")

    kind_row = db.get(DeliverableKind, (engagement.type_key, kind))
    if kind_row is None:
        allowed = ", ".join(k.kind for k in kinds_for_type(db, engagement.type_key))
        raise ValidationError(f"'{kind}' isn't a {engagement.type.display_name} deliverable kind (allowed: {allowed})")

    parent = None
    if kind_row.parent_kind:
        if parent_id is None:
            raise ValidationError(f"A {kind_row.display_name} must sit under a {kind_row.parent_kind}")
        parent = db.get(Deliverable, parent_id)
        if parent is None or parent.engagement_id != engagement.id:
            raise ValidationError("Parent must be a deliverable on the same engagement")
        if parent.kind != kind_row.parent_kind:
            raise ValidationError(f"A {kind_row.display_name} must sit under a {kind_row.parent_kind}, not a {parent.kind}")
        if parent.parent_id is not None:
            raise ValidationError("Deliverable trees are at most two levels deep")
    elif parent_id is not None:
        raise ValidationError(f"A {kind_row.display_name} is a top-level deliverable and can't have a parent")

    if priority and priority not in PRIORITIES:
        raise ValidationError(f"Priority must be one of {', '.join(PRIORITIES)}")
    _validate_assignee(db, engagement.id, internal_assignee_user_id)

    stage_key = _validate_stage_key(engagement, kind, stage_key or None)
    initial_counts = None
    if child_count not in (None, ""):
        if not kind_row.bulk_child_counts:
            raise ValidationError(f"A {kind_row.display_name} doesn't track children by count")
        initial_counts = {"not_started": _parse_count(child_count)}

    before = _stage_snapshot(db, engagement, kind, parent.kind if parent else None)
    deliverable = Deliverable(
        engagement_id=engagement.id, engagement_type_key=engagement.type_key, parent_id=parent.id if parent else None,
        kind=kind, name=name, internal_assignee_user_id=internal_assignee_user_id, priority=priority or None,
        target_date=_parse_date(target_date), hours_estimated=_parse_hours(hours_estimated), stage_key=stage_key,
        child_counts=initial_counts,
    )
    db.add(deliverable)
    db.flush()
    _log(db, deliverable, actor, "created", f"Created {kind_row.display_name}")

    # Listing a child individually takes it out of the parent's counted bucket, so the
    # parent's total stays the same (a 99-tile dashboard stays 99 tiles).
    taken_from_count = False
    if parent is not None and (parent.child_counts or {}).get("not_started", 0) > 0:
        parent.child_counts = {**parent.child_counts, "not_started": parent.child_counts["not_started"] - 1}
        taken_from_count = True
    emit(db, entity_type="deliverable", entity_id=deliverable.id, event_type="deliverable_created", actor=actor,
         payload={"engagement_id": engagement.id, "kind": kind, "name": name, "parent_id": deliverable.parent_id,
                  "stage_key": stage_key, "child_counts": initial_counts, "taken_from_parent_count": taken_from_count})
    _emit_stage_changes(db, actor, engagement, before)
    return deliverable


_UNSET = object()


@mutation("deliverable_updated")
def update_deliverable(db: Session, actor: User, deliverable_id: uuid.UUID, *, name=_UNSET, pipeline_status=_UNSET,
                       blocked=_UNSET, blocked_reason=_UNSET, not_applicable=_UNSET,
                       internal_assignee_user_id=_UNSET, client_owner_user_id=_UNSET, priority=_UNSET,
                       target_date=_UNSET, hours_estimated=_UNSET, stage_key=_UNSET) -> Deliverable:
    d = get_editable_deliverable(db, actor, deliverable_id)
    before = _stage_snapshot(db, d.engagement, d.kind, d.parent.kind if d.parent else None)
    changes: dict[str, dict] = {}

    def set_field(attr: str, value) -> None:
        old = getattr(d, attr)
        if old != value:
            changes[attr] = {"from": old, "to": value}
            setattr(d, attr, value)

    if name is not _UNSET:
        if not name.strip():
            raise ValidationError("Deliverable name is required")
        set_field("name", name.strip())
    if pipeline_status is not _UNSET:
        if pipeline_status not in PIPELINE_STATUSES:
            raise ValidationError(f"Status must be one of {', '.join(PIPELINE_STATUSES)}")
        set_field("pipeline_status", pipeline_status)
    if blocked is not _UNSET:
        reason = (blocked_reason or "").strip() if blocked_reason is not _UNSET else (d.blocked_reason or "")
        if blocked and not reason:
            raise ValidationError("Say why it's blocked")
        set_field("blocked", bool(blocked))
        set_field("blocked_reason", reason if blocked else None)
    elif blocked_reason is not _UNSET and d.blocked:
        set_field("blocked_reason", (blocked_reason or "").strip() or d.blocked_reason)
    if not_applicable is not _UNSET:
        set_field("not_applicable", bool(not_applicable))
    if internal_assignee_user_id is not _UNSET:
        _validate_assignee(db, d.engagement_id, internal_assignee_user_id)
        set_field("internal_assignee_user_id", internal_assignee_user_id)
    if client_owner_user_id is not _UNSET:
        _validate_client_owner(db, d, client_owner_user_id)
        set_field("client_owner_user_id", client_owner_user_id)
    if priority is not _UNSET:
        if priority and priority not in PRIORITIES:
            raise ValidationError(f"Priority must be one of {', '.join(PRIORITIES)}")
        set_field("priority", priority or None)
    if target_date is not _UNSET:
        set_field("target_date", _parse_date(target_date))
    if hours_estimated is not _UNSET:
        set_field("hours_estimated", _parse_hours(hours_estimated))
    if stage_key is not _UNSET:
        set_field("stage_key", _validate_stage_key(d.engagement, d.kind, stage_key or None))

    if not changes:
        raise ValidationError("Nothing changed")

    if "pipeline_status" in changes:
        c = changes["pipeline_status"]
        _log(db, d, actor, "status_changed", f"{PIPELINE_LABELS[c['from']]} → {PIPELINE_LABELS[c['to']]}")
    if "blocked" in changes:
        _log(db, d, actor, "blocked" if d.blocked else "unblocked",
             f"Flagged blocked: {d.blocked_reason}" if d.blocked else "Block cleared")
    if "not_applicable" in changes:
        _log(db, d, actor, "not_applicable", "Marked not applicable" if d.not_applicable else "Marked applicable again")
    other = [k for k in changes if k not in ("pipeline_status", "blocked", "blocked_reason", "not_applicable")]
    if other:
        _log(db, d, actor, "edited", "Updated " + ", ".join(k.replace("_user_id", "").replace("_", " ") for k in other))

    emit(db, entity_type="deliverable", entity_id=d.id, event_type="deliverable_updated", actor=actor,
         payload={"engagement_id": d.engagement_id, "changes": changes})
    if changes.get("pipeline_status", {}).get("to") == "done":
        emit(db, entity_type="deliverable", entity_id=d.id, event_type="deliverable_completed", actor=actor,
             payload={"engagement_id": d.engagement_id})
    _emit_stage_changes(db, actor, d.engagement, before)
    return d


@mutation("deliverable_child_counts_set")
def set_child_counts(db: Session, actor: User, deliverable_id: uuid.UUID, *, counts: dict) -> Deliverable:
    """Per-status counts for the children of a bulk-count parent (e.g. a dashboard's tiles)
    that aren't listed individually. Listed children keep their own status; progress adds
    both together."""
    d = get_editable_deliverable(db, actor, deliverable_id)
    kind_row = db.get(DeliverableKind, (d.engagement_type_key, d.kind))
    if not kind_row.bulk_child_counts:
        raise ValidationError(f"A {kind_row.display_name} doesn't track children by count")
    unknown = set(counts) - set(PIPELINE_STATUSES)
    if unknown:
        raise ValidationError(f"Unknown status: {', '.join(sorted(unknown))}")
    new = {s: _parse_count(counts.get(s) or 0) for s in PIPELINE_STATUSES}
    new = {s: n for s, n in new.items() if n}
    previous = d.child_counts or {}
    if new == {k: v for k, v in previous.items() if v}:
        raise ValidationError("Nothing changed")
    d.child_counts = new or None
    summary = ", ".join(f"{n} {PIPELINE_LABELS[s].lower()}" for s, n in new.items()) or "none"
    _log(db, d, actor, "counts", f"Counts of unlisted items: {summary}")
    emit(db, entity_type="deliverable", entity_id=d.id, event_type="deliverable_child_counts_set", actor=actor,
         payload={"engagement_id": d.engagement_id, "from": previous, "to": new})
    return d


@mutation("deliverable_note_added")
def add_note(db: Session, actor: User, deliverable_id: uuid.UUID, *, body: str) -> DeliverableActivity:
    """Internal commentary. Anyone who can see the deliverable can comment, viewers included;
    deliverable_activity is never client-visible (CLAUDE.md §3 Portal)."""
    d = get_visible_deliverable(db, actor, deliverable_id)
    body = body.strip()
    if not body:
        raise ValidationError("Note can't be empty")
    row = DeliverableActivity(deliverable_id=d.id, actor_user_id=actor.id, kind="note", body=body)
    db.add(row)
    db.flush()
    emit(db, entity_type="deliverable", entity_id=d.id, event_type="deliverable_note_added", actor=actor,
         payload={"engagement_id": d.engagement_id, "activity_id": row.id})
    return row


def _would_cycle(db: Session, blocked_id: uuid.UUID, blocker_id: uuid.UUID) -> bool:
    """Adding (blocked waits on blocker) cycles iff blocker already transitively waits on blocked."""
    seen, stack = set(), [blocker_id]
    while stack:
        current = stack.pop()
        if current == blocked_id:
            return True
        if current in seen:
            continue
        seen.add(current)
        stack.extend(db.scalars(select(DeliverableBlocker.blocker_id).where(DeliverableBlocker.blocked_id == current)))
    return False


@mutation("deliverable_blocker_added")
def add_blocker(db: Session, actor: User, deliverable_id: uuid.UUID, *, blocker_id: uuid.UUID) -> DeliverableBlocker:
    d = get_editable_deliverable(db, actor, deliverable_id)
    blocker = db.get(Deliverable, blocker_id)
    if blocker is None or blocker.engagement_id != d.engagement_id:
        raise ValidationError("A blocker must be a deliverable on the same engagement")
    if blocker.id == d.id:
        raise ValidationError("A deliverable can't block itself")
    if db.get(DeliverableBlocker, (d.id, blocker.id)):
        raise ValidationError(f"Already waiting on '{blocker.name}'")
    if _would_cycle(db, d.id, blocker.id):
        raise ValidationError(f"'{blocker.name}' already depends on '{d.name}' — that would be a cycle")
    edge = DeliverableBlocker(blocked_id=d.id, blocker_id=blocker.id)
    db.add(edge)
    _log(db, d, actor, "blocker_added", f"Now waiting on {blocker.name}")
    emit(db, entity_type="deliverable", entity_id=d.id, event_type="deliverable_blocker_added", actor=actor,
         payload={"engagement_id": d.engagement_id, "blocker_id": blocker.id})
    return edge


@mutation("deliverable_blocker_removed")
def remove_blocker(db: Session, actor: User, deliverable_id: uuid.UUID, *, blocker_id: uuid.UUID) -> None:
    d = get_editable_deliverable(db, actor, deliverable_id)
    edge = db.get(DeliverableBlocker, (d.id, blocker_id))
    if edge is None:
        raise ValidationError("That dependency doesn't exist")
    blocker = db.get(Deliverable, blocker_id)
    db.delete(edge)
    _log(db, d, actor, "blocker_removed", f"No longer waiting on {blocker.name}")
    emit(db, entity_type="deliverable", entity_id=d.id, event_type="deliverable_blocker_removed", actor=actor,
         payload={"engagement_id": d.engagement_id, "blocker_id": blocker_id})
