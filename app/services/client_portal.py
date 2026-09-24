"""Client-facing collaboration (CLAUDE.md §3 Client-facing portal): the client comment
thread, UAT review verdicts, and inviting client contacts onto an engagement.

Two populations use these functions. Internal users reach a deliverable through the usual
internal visibility rules; client_external users only through the client_* rules in
app.access (client-visible kinds, scoped by viewer_scope). Nothing here ever reads or
writes deliverable_activity, the internal-only channel.
"""

import uuid
from dataclasses import dataclass, field

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.access import (
    can_edit_engagement,
    client_can_review,
    client_can_see,
    client_membership,
    client_visible_kinds,
    get_client_deliverable,
    get_client_engagement,
    get_visible_deliverable,
    get_visible_engagement,
    is_internal,
)
from app.errors import Forbidden, NotFound, ValidationError
from app.events import emit, mutation
from app.models import (
    REVIEW_VERDICTS,
    UAT_STATUS,
    VIEWER_SCOPES,
    ClientContact,
    Deliverable,
    DeliverableClientReview,
    DeliverableComment,
    DeliverableKind,
    Engagement,
    EngagementMembership,
    User,
)

VERDICT_LABELS = {"accepted": "Accepted", "blocked": "Blocked", "rejected": "Rejected"}
# How pipeline_status reads to a client.
CLIENT_STATUS_LABELS = {
    "not_started": "Not started",
    "in_progress": "In progress",
    "internal_validation": "In internal QA",
    "external_validation": "Ready for your review (UAT)",
    "done": "Done",
}


# ---------------------------------------------------------------- shared lookups


def _deliverable_for_thread(db: Session, actor: User, deliverable_id: uuid.UUID) -> Deliverable:
    """Resolve a deliverable for the client thread, for either population."""
    if is_internal(actor):
        d = get_visible_deliverable(db, actor, deliverable_id)
        if d.kind not in client_visible_kinds(db, d.engagement_type_key):
            raise NotFound("This deliverable has no client thread")
        return d
    return get_client_deliverable(db, actor, deliverable_id)[0]


def get_latest_verdicts(db: Session, deliverable_ids: list[uuid.UUID]) -> dict[uuid.UUID, DeliverableClientReview]:
    if not deliverable_ids:
        return {}
    latest = (select(DeliverableClientReview.deliverable_id, func.max(DeliverableClientReview.created_at).label("at"))
              .where(DeliverableClientReview.deliverable_id.in_(deliverable_ids))
              .group_by(DeliverableClientReview.deliverable_id).subquery())
    rows = db.scalars(select(DeliverableClientReview).join(
        latest, (latest.c.deliverable_id == DeliverableClientReview.deliverable_id)
        & (latest.c.at == DeliverableClientReview.created_at)))
    return {r.deliverable_id: r for r in rows}


def get_comment_counts(db: Session, deliverable_ids: list[uuid.UUID]) -> dict[uuid.UUID, int]:
    if not deliverable_ids:
        return {}
    return dict(db.execute(select(DeliverableComment.deliverable_id, func.count())
                           .where(DeliverableComment.deliverable_id.in_(deliverable_ids))
                           .group_by(DeliverableComment.deliverable_id)).all())


def list_comments(db: Session, actor: User, deliverable_id: uuid.UUID) -> list[DeliverableComment]:
    d = _deliverable_for_thread(db, actor, deliverable_id)
    return list(db.scalars(select(DeliverableComment).where(DeliverableComment.deliverable_id == d.id)
                           .order_by(DeliverableComment.created_at)))


def list_reviews(db: Session, deliverable_id: uuid.UUID) -> list[DeliverableClientReview]:
    return list(db.scalars(select(DeliverableClientReview).where(DeliverableClientReview.deliverable_id == deliverable_id)
                           .order_by(DeliverableClientReview.created_at.desc())))


def list_client_members(db: Session, engagement_id: uuid.UUID) -> list[EngagementMembership]:
    return list(db.scalars(select(EngagementMembership).join(User, User.id == EngagementMembership.user_id).where(
        EngagementMembership.engagement_id == engagement_id, User.user_type == "client_external")
        .order_by(User.display_name)))


def list_invitable_contacts(db: Session, engagement: Engagement) -> list[ClientContact]:
    """The client's contacts with an email who don't have access to this engagement yet."""
    member_emails = {m.user.email for m in list_client_members(db, engagement.id)}
    return [c for c in db.scalars(select(ClientContact).where(
        ClientContact.client_id == engagement.client_id, ClientContact.status == "active").order_by(ClientContact.name))
        if c.email and c.email not in member_emails]


def can_review_now(user: User, membership: EngagementMembership, d: Deliverable) -> bool:
    return client_can_review(user, membership, d) and d.pipeline_status == UAT_STATUS


# ---------------------------------------------------------------- the portal's view of an engagement


@dataclass
class ClientItem:
    deliverable: Deliverable
    verdict: DeliverableClientReview | None
    comment_count: int
    can_review: bool
    owned: bool
    children: list["ClientItem"] = field(default_factory=list)


@dataclass
class ClientSection:
    kind: DeliverableKind
    items: list[ClientItem]
    done: int
    total: int


def list_client_engagements(db: Session, actor: User) -> list[tuple[Engagement, EngagementMembership]]:
    if actor.user_type != "client_external":
        return []
    rows = db.execute(select(Engagement, EngagementMembership).join(
        EngagementMembership, EngagementMembership.engagement_id == Engagement.id)
        .where(EngagementMembership.user_id == actor.id).order_by(Engagement.name)).all()
    return [(e, m) for e, m in rows if client_visible_kinds(db, e.type_key)]


def derive_client_view(db: Session, engagement: Engagement, *, viewer: User | None = None,
                       membership: EngagementMembership | None = None) -> list[ClientSection]:
    """What a client sees on this engagement. With no viewer, it's the project-lead view
    (every client-visible deliverable): what internal users preview in the Client view tab."""
    kinds = {k.kind: k for k in db.scalars(select(DeliverableKind).where(
        DeliverableKind.engagement_type_key == engagement.type_key, DeliverableKind.client_visible.is_(True)))}
    items = list(db.scalars(select(Deliverable).where(
        Deliverable.engagement_id == engagement.id, Deliverable.kind.in_(kinds), Deliverable.not_applicable.is_(False))
        .order_by(Deliverable.created_at)))
    if viewer is not None:
        items = [d for d in items if client_can_see(db, viewer, d, membership, set(kinds))]
    ids = [d.id for d in items]
    verdicts, counts = get_latest_verdicts(db, ids), get_comment_counts(db, ids)
    nodes = {d.id: ClientItem(
        d, verdicts.get(d.id), counts.get(d.id, 0),
        can_review=bool(viewer and membership and can_review_now(viewer, membership, d)),
        owned=bool(viewer and d.client_owner_user_id == viewer.id)) for d in items}
    for n in nodes.values():
        if n.deliverable.parent_id in nodes:
            nodes[n.deliverable.parent_id].children.append(n)
    sections = []
    for kind in sorted((k for k in kinds.values() if k.parent_kind is None or k.parent_kind not in kinds),
                       key=lambda k: k.kind):
        roots = [n for n in nodes.values() if n.deliverable.kind == kind.kind
                 and n.deliverable.parent_id not in nodes]
        flat = [x for r in roots for x in ([r] + r.children)]
        sections.append(ClientSection(kind, roots, sum(x.deliverable.pipeline_status == "done" for x in flat), len(flat)))
    return [s for s in sections if s.items]


# ---------------------------------------------------------------- writes


@mutation("client_comment_added")
def add_client_comment(db: Session, actor: User, deliverable_id: uuid.UUID, *, body: str) -> DeliverableComment:
    """Post to the client-facing thread. Anyone who can see the deliverable can post:
    internal team members, and client users within their scope."""
    d = _deliverable_for_thread(db, actor, deliverable_id)
    body = body.strip()
    if not body:
        raise ValidationError("Comment can't be empty")
    row = DeliverableComment(deliverable_id=d.id, author_user_id=actor.id, body=body)
    db.add(row)
    db.flush()
    emit(db, entity_type="deliverable", entity_id=d.id, event_type="client_comment_added", actor=actor,
         payload={"engagement_id": d.engagement_id, "comment_id": row.id, "author_type": actor.user_type})
    return row


@mutation("client_review_submitted")
def submit_review(db: Session, actor: User, deliverable_id: uuid.UUID, *, verdict: str,
                  comment: str = "") -> DeliverableClientReview:
    """A client's UAT verdict. Allowed only while the deliverable is in UAT
    (external_validation), and only for a project lead or the deliverable's client owner.
    It never changes pipeline_status; acting on it is the assignee's call."""
    if actor.user_type != "client_external":
        raise Forbidden("Only client users give review verdicts")
    d, membership = get_client_deliverable(db, actor, deliverable_id)
    if verdict not in REVIEW_VERDICTS:
        raise ValidationError(f"Verdict must be one of {', '.join(REVIEW_VERDICTS)}")
    if not client_can_review(actor, membership, d):
        raise Forbidden("Only the project lead or this item's owner can review it")
    if d.pipeline_status != UAT_STATUS:
        raise ValidationError("Verdicts open once this is in UAT")
    row = DeliverableClientReview(deliverable_id=d.id, reviewer_user_id=actor.id, verdict=verdict)
    db.add(row)
    db.flush()
    comment_id = None
    if comment.strip():
        c = DeliverableComment(deliverable_id=d.id, author_user_id=actor.id,
                               body=f"[{VERDICT_LABELS[verdict]}] {comment.strip()}")
        db.add(c)
        db.flush()
        comment_id = c.id
    emit(db, entity_type="deliverable", entity_id=d.id, event_type="client_review_submitted", actor=actor,
         payload={"engagement_id": d.engagement_id, "review_id": row.id, "verdict": verdict, "comment_id": comment_id})
    return row


def _require_can_invite(db: Session, actor: User, engagement_id: uuid.UUID) -> Engagement:
    # Same gate as other engagement-scoped actions: owner/collaborator, or ops/admin.
    engagement = get_visible_engagement(db, actor, engagement_id)
    if not can_edit_engagement(db, actor, engagement.id):
        raise Forbidden("Only the engagement's owner/collaborators or ops/admin can manage client access")
    return engagement


@mutation("client_user_invited")
def invite_client_contact(db: Session, actor: User, engagement_id: uuid.UUID, *, contact_id: uuid.UUID,
                          viewer_scope: str = "full") -> EngagementMembership:
    """Give one of the client's contacts access to this engagement. Creates their
    client_external account on first invite (invite-only; there is no self sign-up)."""
    engagement = _require_can_invite(db, actor, engagement_id)
    if viewer_scope not in VIEWER_SCOPES:
        raise ValidationError(f"Scope must be one of {', '.join(VIEWER_SCOPES)}")
    contact = db.get(ClientContact, contact_id)
    if contact is None or contact.client_id != engagement.client_id or not contact.email:
        raise ValidationError("Pick a contact of this client who has an email address")
    user = db.scalar(select(User).where(User.email == contact.email.lower()))
    created = user is None
    if user is None:
        user = User(email=contact.email.lower(), display_name=contact.name, user_type="client_external", role=None)
        db.add(user)
        db.flush()
    elif user.user_type != "client_external":
        raise ValidationError(f"{contact.email} belongs to an internal user")
    if db.get(EngagementMembership, (engagement.id, user.id)):
        raise ValidationError(f"{user.label} already has access")
    m = EngagementMembership(engagement_id=engagement.id, user_id=user.id, role="viewer", viewer_scope=viewer_scope)
    db.add(m)
    db.flush()
    db.expire(engagement, ["memberships"])
    emit(db, entity_type="engagement", entity_id=engagement.id, event_type="client_user_invited", actor=actor,
         payload={"user_id": user.id, "email": user.email, "viewer_scope": viewer_scope, "account_created": created})
    return m


@mutation("client_scope_changed")
def set_client_scope(db: Session, actor: User, engagement_id: uuid.UUID, *, user_id: uuid.UUID,
                     viewer_scope: str) -> EngagementMembership:
    engagement = _require_can_invite(db, actor, engagement_id)
    if viewer_scope not in VIEWER_SCOPES:
        raise ValidationError(f"Scope must be one of {', '.join(VIEWER_SCOPES)}")
    m = db.get(EngagementMembership, (engagement.id, user_id))
    if m is None or m.user.user_type != "client_external":
        raise NotFound("That client user isn't on this engagement")
    previous, m.viewer_scope = m.viewer_scope, viewer_scope
    emit(db, entity_type="engagement", entity_id=engagement.id, event_type="client_scope_changed", actor=actor,
         payload={"user_id": user_id, "from": previous, "to": viewer_scope})
    return m


@mutation("client_access_revoked")
def revoke_client_access(db: Session, actor: User, engagement_id: uuid.UUID, *, user_id: uuid.UUID) -> None:
    """Revocation just deletes the membership; the client account persists."""
    engagement = _require_can_invite(db, actor, engagement_id)
    m = db.get(EngagementMembership, (engagement.id, user_id))
    if m is None or m.user.user_type != "client_external":
        raise NotFound("That client user isn't on this engagement")
    db.delete(m)
    db.flush()
    db.expire(engagement, ["memberships"])
    emit(db, entity_type="engagement", entity_id=engagement.id, event_type="client_access_revoked", actor=actor,
         payload={"user_id": user_id})

