"""Engagement team assignment — the only way visibility is granted (CLAUDE.md §2 row 6)."""

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.access import require_can_manage_memberships
from app.errors import NotFound, ValidationError
from app.events import emit, mutation
from app.models import MEMBERSHIP_ROLES, Engagement, EngagementMembership, User


def list_members(db: Session, engagement_id: uuid.UUID) -> list[EngagementMembership]:
    """The internal team. Client users with portal access are listed separately
    (services.client_portal.list_client_members)."""
    order = {r: i for i, r in enumerate(MEMBERSHIP_ROLES)}
    rows = db.scalars(select(EngagementMembership).join(User, User.id == EngagementMembership.user_id).where(
        EngagementMembership.engagement_id == engagement_id, User.user_type == "internal"))
    return sorted(rows, key=lambda m: (order[m.role], m.user.label))


def get_engagement_owner(db: Session, engagement_id: uuid.UUID) -> User | None:
    """The engagement's owner = its single 'owner' membership. The one place anything
    that needs "the engagement's owner" (e.g. the rules engine's draft_nudge opt-in,
    CLAUDE.md §3) should resolve it from."""
    return db.scalar(select(User).join(EngagementMembership, EngagementMembership.user_id == User.id).where(
        EngagementMembership.engagement_id == engagement_id, EngagementMembership.role == "owner"))


def _team_engagement(db: Session, actor: User, engagement_id: uuid.UUID) -> Engagement:
    """Teams are managed with engagements:manage or team:manage (the Team screens list every
    open engagement, so team:manage reaches engagements its holder isn't on)."""
    require_can_manage_memberships(actor)
    engagement = db.get(Engagement, engagement_id)
    if engagement is None:
        raise NotFound("Engagement not found")
    return engagement


@mutation("membership_granted")
def assign_member(db: Session, actor: User, engagement_id: uuid.UUID, *, user_id: uuid.UUID,
                  role: str) -> EngagementMembership:
    """Grant (or change) a user's role on an engagement. One owner per engagement:
    making someone owner demotes the previous owner to collaborator."""
    engagement = _team_engagement(db, actor, engagement_id)
    if role not in MEMBERSHIP_ROLES:
        raise ValidationError(f"Role must be one of {', '.join(MEMBERSHIP_ROLES)}")
    user = db.get(User, user_id)
    if user is None or user.status != "active":
        raise NotFound("User not found")
    if user.user_type != "internal":
        # client_external grants belong to the portal, which is out of PoC scope.
        raise ValidationError("Only internal users can be assigned here")
    if not user.can("engagements"):
        raise ValidationError(f"{user.label} has no access to engagements; an admin can grant it under Access")

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

    db.flush()
    db.expire(engagement, ["memberships"])
    emit(db, entity_type="engagement", entity_id=engagement.id, event_type="membership_granted", actor=actor,
         payload={"user_id": user.id, "email": user.email, "role": role, "previous_role": previous_role,
                  "demoted_owner_user_id": demoted})
    return membership


@mutation("membership_revoked")
def remove_member(db: Session, actor: User, engagement_id: uuid.UUID, *, user_id: uuid.UUID) -> None:
    engagement = _team_engagement(db, actor, engagement_id)
    membership = db.get(EngagementMembership, (engagement.id, user_id))
    if membership is None:
        raise NotFound("That user isn't on this engagement")
    role = membership.role
    db.delete(membership)
    db.flush()
    db.expire(engagement, ["memberships"])
    emit(db, entity_type="engagement", entity_id=engagement.id, event_type="membership_revoked", actor=actor,
         payload={"user_id": user_id, "role": role})
