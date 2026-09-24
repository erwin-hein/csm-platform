"""Visibility and permission rules — CLAUDE.md §2 row 6.

Visibility lives on engagement_memberships, never at the Client level. ops/admin
bypass membership entirely. A Client is visible to a non-bypass user only through
at least one Engagement they're a member of.

Permission model for the PoC (see CLAUDE.md §5 for which parts are provisional):
- read an engagement + its deliverables: any membership role, or ops/admin
- edit deliverables / move stage:        owner or collaborator, or ops/admin
- create clients & engagements, manage memberships: ops/admin only
"""

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.errors import Forbidden, NotFound
from app.models import Client, Deliverable, Engagement, EngagementMembership, User

EDIT_ROLES = ("owner", "collaborator")


def visible_engagements_stmt(user: User):
    stmt = select(Engagement)
    if not user.bypasses_membership:
        stmt = stmt.join(
            EngagementMembership,
            (EngagementMembership.engagement_id == Engagement.id) & (EngagementMembership.user_id == user.id),
        )
    return stmt


def visible_clients_stmt(user: User):
    stmt = select(Client)
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
    return user.bypasses_membership or membership_role(db, user, engagement_id) is not None


def can_edit_engagement(db: Session, user: User, engagement_id: uuid.UUID) -> bool:
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
