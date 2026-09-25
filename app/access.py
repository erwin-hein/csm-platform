"""Visibility and permission rules — CLAUDE.md §2 row 6.

Visibility lives on engagement_memberships, never at the Client level. ops/admin
bypass membership entirely. A Client is visible to a non-bypass user only through
at least one Engagement they're a member of.

Permission model (CLAUDE.md §3 Identity & access):
- read an engagement + its deliverables: any membership role, or ops/admin
- comment (internal notes):              anyone who can read
- edit deliverables / move stage:        owner or collaborator, or ops/admin
- create clients & engagements, manage memberships: ops/admin only

Everything above is for *internal* users only: a client_external user never passes an
internal check, whatever memberships they hold. Their access goes exclusively through the
client_* functions at the bottom (CLAUDE.md §3 Client-facing portal).
"""

import uuid

from sqlalchemy import false, select
from sqlalchemy.orm import Session

from app.errors import Forbidden, NotFound
from app.models import Client, Deliverable, DeliverableKind, Engagement, EngagementMembership, User

EDIT_ROLES = ("owner", "collaborator")


def is_internal(user: User) -> bool:
    return user.user_type == "internal"


def visible_engagements_stmt(user: User):
    stmt = select(Engagement)
    if not is_internal(user):
        return stmt.where(false())
    if not user.bypasses_membership:
        stmt = stmt.join(
            EngagementMembership,
            (EngagementMembership.engagement_id == Engagement.id) & (EngagementMembership.user_id == user.id),
        )
    return stmt


def visible_clients_stmt(user: User):
    stmt = select(Client)
    if not is_internal(user):
        return stmt.where(false())
    if not user.bypasses_membership:
        member_client_ids = (
            select(Engagement.client_id)
            .join(EngagementMembership, EngagementMembership.engagement_id == Engagement.id)
            .where(EngagementMembership.user_id == user.id)
        )
        stmt = stmt.where(Client.id.in_(member_client_ids))
    return stmt


def membership_role(db: Session, user: User, engagement_id: uuid.UUID) -> str | None:
    return db.scalar(
        select(EngagementMembership.role).where(
            EngagementMembership.engagement_id == engagement_id, EngagementMembership.user_id == user.id
        )
    )


def can_see_engagement(db: Session, user: User, engagement_id: uuid.UUID) -> bool:
    if not is_internal(user):
        return False
    return user.bypasses_membership or membership_role(db, user, engagement_id) is not None


def can_edit_engagement(db: Session, user: User, engagement_id: uuid.UUID) -> bool:
    if not is_internal(user):
        return False
    return user.bypasses_membership or membership_role(db, user, engagement_id) in EDIT_ROLES


def get_visible_engagement(db: Session, user: User, engagement_id: uuid.UUID) -> Engagement:
    engagement = db.get(Engagement, engagement_id)
    if engagement is None or not can_see_engagement(db, user, engagement.id):
        raise NotFound("Engagement not found")
    return engagement


def get_editable_engagement(db: Session, user: User, engagement_id: uuid.UUID) -> Engagement:
    engagement = get_visible_engagement(db, user, engagement_id)
    if not can_edit_engagement(db, user, engagement.id):
        raise Forbidden("You have view-only access to this engagement")
    return engagement


def get_visible_client(db: Session, user: User, client_id: uuid.UUID) -> Client:
    client = db.scalar(visible_clients_stmt(user).where(Client.id == client_id))
    if client is None:
        raise NotFound("Client not found")
    return client


def get_visible_deliverable(db: Session, user: User, deliverable_id: uuid.UUID) -> Deliverable:
    deliverable = db.get(Deliverable, deliverable_id)
    if deliverable is None or not can_see_engagement(db, user, deliverable.engagement_id):
        raise NotFound("Deliverable not found")
    return deliverable


def get_editable_deliverable(db: Session, user: User, deliverable_id: uuid.UUID) -> Deliverable:
    deliverable = get_visible_deliverable(db, user, deliverable_id)
    if not can_edit_engagement(db, user, deliverable.engagement_id):
        raise Forbidden("You have view-only access to this engagement")
    return deliverable


def require_ops_or_admin(user: User) -> None:
    if not user.bypasses_membership:
        raise Forbidden("Only ops or admin can do this")


# ---------------------------------------------------------------- client_external users (portal)
#
# visible(deliverable, client user) =
#     its kind is client_visible
#     AND (viewer_scope = 'full'
#          OR deliverable.client_owner_user_id = user
#          OR one of its children has client_owner_user_id = user)   -- the parent, shown for context
#
# The same predicate gates reading/posting the client thread. Submitting a verdict additionally
# needs scope 'full' or being the deliverable's client owner, and the deliverable being in UAT.


def client_visible_kinds(db: Session, type_key: str) -> set[str]:
    return set(db.scalars(select(DeliverableKind.kind).where(
        DeliverableKind.engagement_type_key == type_key, DeliverableKind.client_visible.is_(True))))


def client_membership(db: Session, user: User, engagement_id: uuid.UUID) -> EngagementMembership | None:
    if user.user_type != "client_external":
        return None
    return db.get(EngagementMembership, (engagement_id, user.id))


def client_can_see(db: Session, user: User, d: Deliverable, membership: EngagementMembership | None = None,
                   visible_kinds: set[str] | None = None) -> bool:
    membership = membership or client_membership(db, user, d.engagement_id)
    if membership is None:
        return False
    kinds = visible_kinds if visible_kinds is not None else client_visible_kinds(db, d.engagement_type_key)
    if d.kind not in kinds:
        return False
    if membership.viewer_scope == "full" or d.client_owner_user_id == user.id:
        return True
    return any(c.client_owner_user_id == user.id and c.kind in kinds for c in d.children)


def client_can_review(user: User, membership: EngagementMembership, d: Deliverable) -> bool:
    return membership.viewer_scope == "full" or d.client_owner_user_id == user.id


def get_client_engagement(db: Session, user: User, engagement_id: uuid.UUID) -> tuple[Engagement, EngagementMembership]:
    membership = client_membership(db, user, engagement_id)
    engagement = db.get(Engagement, engagement_id) if membership else None
    if engagement is None or not client_visible_kinds(db, engagement.type_key):
        raise NotFound("Engagement not found")
    return engagement, membership


def get_client_deliverable(db: Session, user: User, deliverable_id: uuid.UUID) -> tuple[Deliverable, EngagementMembership]:
    d = db.get(Deliverable, deliverable_id)
    membership = client_membership(db, user, d.engagement_id) if d else None
    if d is None or membership is None or not client_can_see(db, user, d, membership):
        raise NotFound("Deliverable not found")
    return d, membership
