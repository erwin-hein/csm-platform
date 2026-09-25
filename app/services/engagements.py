import uuid
from datetime import date, datetime, time, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.access import (
    get_editable_engagement,
    get_visible_client,
    get_visible_opportunity,
    require_module,
    visible_engagements_stmt,
)
from app.errors import ValidationError
from app.events import emit, mutation
from app.models import ENGAGEMENT_STATUSES, Engagement, EngagementType, Opportunity, User


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
                      stage: str | None = None, started_on: date | str | None = None,
                      opportunity_id: uuid.UUID | None = None) -> Engagement:
    """started_on lets an engagement that's already under way be entered with its real start date.
    opportunity_id links it to the won opportunity it delivers (CLAUDE.md §3 Opportunities)."""
    require_module(actor, "engagements", "manage")
    client = get_visible_client(db, actor, client_id)
    opp = _linkable_opportunity(db, actor, opportunity_id, client.id) if opportunity_id else None
    etype = get_engagement_type(db, type_key)
    name = name.strip()
    if not name:
        raise ValidationError("Engagement name is required")
    if etype.stages_derived:
        # Stages follow the engagement's stage-kind deliverables (e.g. phases); nothing to set.
        if stage:
            raise ValidationError(f"{etype.display_name} stages follow its {etype.stage_kind}s and aren't set by hand")
        stage = None
    else:
        stage = stage or etype.stage_keys[0]
        if stage not in etype.stage_keys:
            raise ValidationError(f"'{stage}' is not a {etype.display_name} stage")
    if started_on in (None, ""):
        started_at = datetime.now(timezone.utc)
    else:
        try:
            day = started_on if isinstance(started_on, date) else date.fromisoformat(started_on)
        except ValueError:
            raise ValidationError(f"'{started_on}' is not a valid date")
        if day > date.today():
            raise ValidationError("An engagement can't start in the future")
        started_at = datetime.combine(day, time(9), tzinfo=timezone.utc)
    engagement = Engagement(client_id=client.id, type_key=etype.key, name=name, stage=stage,
                            status="active", started_at=started_at, opportunity_id=opp.id if opp else None)
    db.add(engagement)
    db.flush()
    if opp:
        db.expire(opp, ["engagements"])
    emit(db, entity_type="engagement", entity_id=engagement.id, event_type="engagement_created", actor=actor,
         payload={"client_id": client.id, "type_key": etype.key, "name": name, "stage": stage,
                  "started_at": started_at, "opportunity_id": opp.id if opp else None})
    return engagement


def _linkable_opportunity(db: Session, actor: User, opportunity_id: uuid.UUID, client_id: uuid.UUID) -> Opportunity:
    opp = get_visible_opportunity(db, actor, opportunity_id)
    if opp.client_id != client_id:
        raise ValidationError("The opportunity belongs to a different client")
    if not opp.is_won:
        raise ValidationError("Only a Closed Won opportunity can be linked to an engagement")
    return opp


@mutation("engagement_opportunity_linked")
def link_opportunity(db: Session, actor: User, engagement_id: uuid.UUID, *,
                     opportunity_id: uuid.UUID | None) -> Engagement:
    """Link an existing engagement to the won opportunity it delivers, or unlink it (None)."""
    require_module(actor, "engagements", "manage")
    engagement = get_editable_engagement(db, actor, engagement_id)
    previous = engagement.opportunity_id
    opp = _linkable_opportunity(db, actor, opportunity_id, engagement.client_id) if opportunity_id else None
    if previous == (opp.id if opp else None):
        raise ValidationError("Nothing to change")
    engagement.opportunity_id = opp.id if opp else None
    db.flush()
    for oid in {previous, engagement.opportunity_id} - {None}:
        o = db.get(Opportunity, oid)
        if o is not None:
            db.expire(o, ["engagements"])
    emit(db, entity_type="engagement", entity_id=engagement.id, event_type="engagement_opportunity_linked",
         actor=actor, payload={"opportunity_id": engagement.opportunity_id, "previous_opportunity_id": previous})
    return engagement


@mutation("stage_changed")
def change_stage(db: Session, actor: User, engagement_id: uuid.UUID, *, stage: str) -> Engagement:
    engagement = get_editable_engagement(db, actor, engagement_id)
    if engagement.type.stages_derived:
        raise ValidationError(f"{engagement.type.display_name} stages follow its {engagement.type.stage_kind}s; "
                              f"update the {engagement.type.stage_kind} instead")
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
