import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.access import (
    get_editable_engagement,
    get_visible_client,
    require_ops_or_admin,
    visible_engagements_stmt,
)
from app.errors import ValidationError
from app.events import emit, mutation
from app.models import ENGAGEMENT_STATUSES, Engagement, EngagementType, User


def list_engagement_types(db: Session) -> list[EngagementType]:
    return list(db.scalars(select(EngagementType).order_by(EngagementType.display_name)))


def get_engagement_type(db: Session, key: str) -> EngagementType:
    etype = db.get(EngagementType, key)
    if etype is None:
        raise ValidationError(f"Unknown engagement type '{key}'")
    return etype


def list_visible_engagements(db: Session, user: User, *, type_key: str | None = None,
                             include_closed: bool = True) -> list[Engagement]:
    stmt = visible_engagements_stmt(user)
    if type_key:
        stmt = stmt.where(Engagement.type_key == type_key)
    if not include_closed:
        stmt = stmt.where(Engagement.status.in_(("active", "paused")))
    return list(db.scalars(stmt.order_by(Engagement.created_at)).unique())


@mutation("engagement_created")
def create_engagement(db: Session, actor: User, *, client_id: uuid.UUID, type_key: str, name: str,
                      stage: str | None = None) -> Engagement:
    require_ops_or_admin(actor)
    client = get_visible_client(db, actor, client_id)
    etype = get_engagement_type(db, type_key)
    name = name.strip()
    if not name:
        raise ValidationError("Engagement name is required")
    stage = stage or etype.stage_keys[0]
    if stage not in etype.stage_keys:
        raise ValidationError(f"'{stage}' is not a {etype.display_name} stage")
    engagement = Engagement(client_id=client.id, type_key=etype.key, name=name, stage=stage,
                            status="active", started_at=datetime.now(timezone.utc))
    db.add(engagement)
    db.flush()
    emit(db, entity_type="engagement", entity_id=engagement.id, event_type="engagement_created", actor=actor,
         payload={"client_id": client.id, "type_key": etype.key, "name": name, "stage": stage})
    return engagement


@mutation("stage_changed")
def change_stage(db: Session, actor: User, engagement_id: uuid.UUID, *, stage: str) -> Engagement:
    engagement = get_editable_engagement(db, actor, engagement_id)
    # Validated app-side against the type's stage_vocab (CLAUDE.md §3 engagements.stage).
    if stage not in engagement.type.stage_keys:
        raise ValidationError(f"'{stage}' is not a {engagement.type.display_name} stage")
    previous = engagement.stage
    if previous == stage:
        raise ValidationError("Engagement is already in that stage")
    engagement.stage = stage
    emit(db, entity_type="engagement", entity_id=engagement.id, event_type="stage_changed", actor=actor,
         payload={"from": previous, "to": stage})
    return engagement


@mutation("engagement_status_changed")
def change_status(db: Session, actor: User, engagement_id: uuid.UUID, *, status: str) -> Engagement:
    engagement = get_editable_engagement(db, actor, engagement_id)
    if status not in ENGAGEMENT_STATUSES:
        raise ValidationError(f"Status must be one of {', '.join(ENGAGEMENT_STATUSES)}")
    previous = engagement.status
    if previous == status:
        raise ValidationError(f"Engagement is already {status}")
    engagement.status = status
    if status in ("complete", "cancelled"):
        engagement.ended_at = datetime.now(timezone.utc)
    else:
        engagement.ended_at = None
    emit(db, entity_type="engagement", entity_id=engagement.id, event_type="engagement_status_changed",
         actor=actor, payload={"from": previous, "to": status})
    return engagement
