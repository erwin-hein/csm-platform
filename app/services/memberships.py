"""Engagement team assignment — the only way visibility is granted (CLAUDE.md §2 row 6)."""

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.access import get_visible_engagement, require_ops_or_admin
from app.errors import NotFound, ValidationError
from app.events import emit, mutation
from app.models import MEMBERSHIP_ROLES, EngagementMembership, User


def list_members(db: Session, engagement_id: uuid.UUID) -> list[EngagementMembership]:
    order = {r: i for i, r in enumerate(MEMBERSHIP_ROLES)}
    rows = db.scalars(select(EngagementMembership).where(EngagementMembership.engagement_id == engagement_id))
    return sorted(rows, key=lambda m: (order[m.role], m.user.label))


@mutation("membership_granted")
def assign_member(db: Session, actor: User, engagement_id: uuid.UUID, *, user_id: uuid.UUID,
                  role: str) -> EngagementMembership:
    """Grant (or change) a user's role on an engagement. PoC rule: one owner per
    engagement, mirrored onto engagements.owner_user_id; making someone owner
    demotes the previous owner to collaborator."""
    require_ops_or_admin(actor)
    engagement = get_visible_engagement(db, actor, engagement_id)
    if role not in MEMBERSHIP_ROLES:
        raise ValidationError(f"Role must be one of {', '.join(MEMBERSHIP_ROLES)}")
    user = db.get(User, user_id)
    if user is None or user.status != "active":
        raise NotFound("User not found")
    if user.user_type != "internal":
        # client_external grants belong to the portal, which is out of PoC scope.
        raise ValidationError("Only internal users can be assigned here")

    demoted = None
    if role == "owner":
        current_owner = db.scalar(select(EngagementMembership).where(
            EngagementMembership.engagement_id == engagement.id, EngagementMembership.role == "owner"))
        if current_owner is not None and current_owner.user_id != user.id:
            current_owner.role = "collaborator"
            demoted = current_owner.user_id
            db.flush()

    membership = db.get(EngagementMembership, (engagement.id, user.id))
    previous_role = membership.role if membership else None
    if previous_role == role:
        raise ValidationError(f"{user.label} is already {role} on this engagement")
    if membership is None:
        membership = EngagementMembership(engagement_id=engagement.id, user_id=user.id, role=role)
        db.add(membership)
    else:
        membership.role = role

    if role == "owner":
        engagement.owner_user_id = user.id
    elif engagement.owner_user_id == user.id:
        engagement.owner_user_id = None

    emit(db, entity_type="engagement", entity_id=engagement.id, event_type="membership_granted", actor=actor,
         payload={"user_id": user.id, "email": user.email, "role": role, "previous_role": previous_role,
                  "demoted_owner_user_id": demoted})
    return membership


@mutation("membership_revoked")
def remove_member(db: Session, actor: User, engagement_id: uuid.UUID, *, user_id: uuid.UUID) -> None:
    require_ops_or_admin(actor)
    engagement = get_visible_engagement(db, actor, engagement_id)
    membership = db.get(EngagementMembership, (engagement.id, user_id))
    if membership is None:
        raise NotFound("That user isn't on this engagement")
    role = membership.role
    db.delete(membership)
    if engagement.owner_user_id == user_id:
        engagement.owner_user_id = None
    emit(db, entity_type="engagement", entity_id=engagement.id, event_type="membership_revoked", actor=actor,
         payload={"user_id": user_id, "role": role})
