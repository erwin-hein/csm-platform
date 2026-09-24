"""Server-rendered pages (Jinja2 + htmx). Thin: parse the request, call a service
function, commit, render. No business rules live here."""

import uuid
from collections import defaultdict

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.access import (
    can_edit_engagement,
    get_editable_engagement,
    get_visible_client,
    get_visible_deliverable,
    get_visible_engagement,
    membership_role,
    require_ops_or_admin,
)
from app.db import get_db
from app.errors import NotFound, ValidationError
from app.models import (
    ENGAGEMENT_STATUSES,
    MEMBERSHIP_ROLES,
    PIPELINE_STATUSES,
    Deliverable,
    Engagement,
    EngagementMembership,
    Event,
    User,
)
from app.services import capacity as capacity_service
from app.services import clients as client_service
from app.services import deliverables as deliverable_service
from app.services import engagements as engagement_service
from app.services import memberships as membership_service
from app.services import users as user_service
from app.web.auth import current_user
from app.web.templating import is_htmx, render

router = APIRouter()


def _uuid(value: str | None) -> uuid.UUID | None:
    if not value:
        return None
    try:
        return uuid.UUID(value)
    except ValueError:
        raise ValidationError("Invalid id")


def _back(url: str) -> RedirectResponse:
    return RedirectResponse(url, status_code=303)


def _kind_counts(db: Session, engagement: Engagement) -> list[tuple[str, int]]:
    counts: dict[str, int] = defaultdict(int)
    for d in engagement.deliverables:
        counts[d.kind] += 1
    return [(k.display_name, counts[k.kind]) for k in deliverable_service.kinds_for_type(db, engagement.type_key)]


def _engagement_summary(db: Session, engagement: Engagement) -> dict:
    return {
        "engagement": engagement,
        "progress": deliverable_service.engagement_progress(db, engagement),
        "kind_counts": _kind_counts(db, engagement),
        "members": membership_service.list_members(db, engagement.id),
    }


# ---------------------------------------------------------------- portfolio


CLOSED_STATUSES = ("complete", "cancelled")


@router.get("/")
def portfolio(request: Request, show_closed: bool = False, db: Session = Depends(get_db),
              user: User = Depends(current_user)):
    """Completed/cancelled engagements are hidden unless ?show_closed=true."""
    engagements = engagement_service.list_visible_engagements(db, user)
    by_client: dict[uuid.UUID, list[dict]] = defaultdict(list)
    hidden: dict[uuid.UUID, int] = defaultdict(int)
    for e in engagements:
        if e.status in CLOSED_STATUSES and not show_closed:
            hidden[e.client_id] += 1
            continue
        by_client[e.client_id].append(_engagement_summary(db, e))
    clients = client_service.list_visible_clients(db, user)
    return render(request, "portfolio.html", nav="portfolio", clients=clients, by_client=by_client,
                  hidden=hidden, hidden_total=sum(hidden.values()), show_closed=show_closed,
                  types=engagement_service.list_engagement_types(db))


# ---------------------------------------------------------------- board (per engagement type)


@router.get("/board")
def board_default():
    return _back("/board/quickstart")


@router.get("/board/{type_key}")
def board(type_key: str, request: Request, show_closed: bool = False, db: Session = Depends(get_db),
          user: User = Depends(current_user)):
    try:
        etype = engagement_service.get_engagement_type(db, type_key)
    except ValidationError:
        raise NotFound("No such engagement type")
    engagements = engagement_service.list_visible_engagements(db, user, type_key=type_key,
                                                              include_closed=show_closed)
    columns = {s["key"]: [] for s in etype.stage_vocab}
    for e in engagements:
        columns.setdefault(e.stage, []).append(_engagement_summary(db, e))
    return render(request, "board.html", nav=f"board:{type_key}", etype=etype, columns=columns,
                  types=engagement_service.list_engagement_types(db), show_closed=show_closed,
                  kinds=deliverable_service.kinds_for_type(db, type_key))


# ---------------------------------------------------------------- clients


@router.get("/clients/new")
def client_new(request: Request, user: User = Depends(current_user)):
    require_ops_or_admin(user)
    return render(request, "client_new.html", nav="portfolio")


@router.post("/clients")
def client_create(request: Request, name: str = Form(""), domains: str = Form(""), db: Session = Depends(get_db),
                  user: User = Depends(current_user)):
    client = client_service.create_client(db, user, name=name, domains=domains)
    db.commit()
    return _back(f"/clients/{client.id}")


@router.get("/clients/{client_id}")
def client_detail(client_id: uuid.UUID, request: Request, db: Session = Depends(get_db),
                  user: User = Depends(current_user)):
    client = get_visible_client(db, user, client_id)
    engagements = [e for e in engagement_service.list_visible_engagements(db, user) if e.client_id == client.id]
    return render(request, "client_detail.html", nav="portfolio", client=client,
                  engagements=[_engagement_summary(db, e) for e in engagements],
                  alias_types=client_service.ALIAS_TYPES)


@router.post("/clients/{client_id}/aliases")
def client_add_alias(client_id: uuid.UUID, alias: str = Form(""), alias_type: str = Form("name"),
                     db: Session = Depends(get_db), user: User = Depends(current_user)):
    client_service.add_alias(db, user, client_id, alias=alias, alias_type=alias_type)
    db.commit()
    return _back(f"/clients/{client_id}")


@router.post("/clients/{client_id}/contacts")
def client_add_contact(client_id: uuid.UUID, name: str = Form(""), email: str = Form(""), title: str = Form(""),
                       db: Session = Depends(get_db), user: User = Depends(current_user)):
    client_service.add_contact(db, user, client_id, name=name, email=email, title=title)
    db.commit()
    return _back(f"/clients/{client_id}")


# ---------------------------------------------------------------- engagements


@router.get("/engagements/new")
def engagement_new(request: Request, client_id: str = "", db: Session = Depends(get_db),
                   user: User = Depends(current_user)):
    require_ops_or_admin(user)
    types = engagement_service.list_engagement_types(db)
    return render(request, "engagement_new.html", nav="portfolio",
                  clients=client_service.list_visible_clients(db, user), selected_client_id=client_id,
                  types=types, kinds={t.key: deliverable_service.kinds_for_type(db, t.key) for t in types})


@router.post("/engagements")
def engagement_create(client_id: str = Form(""), type_key: str = Form(""), name: str = Form(""),
                      db: Session = Depends(get_db), user: User = Depends(current_user)):
    cid = _uuid(client_id)
    if cid is None:
        raise ValidationError("Pick a client")
    engagement = engagement_service.create_engagement(db, user, client_id=cid, type_key=type_key, name=name)
    db.commit()
    return _back(f"/engagements/{engagement.id}")


def _engagement_context(db: Session, user: User, engagement: Engagement) -> dict:
    return {
        "engagement": engagement,
        "tree": deliverable_service.engagement_tree(db, engagement),
        "can_edit": can_edit_engagement(db, user, engagement.id),
        "my_role": membership_role(db, user, engagement.id),
        "statuses": ENGAGEMENT_STATUSES,
        "pipeline_statuses": PIPELINE_STATUSES,
    }


def _team_context(db: Session, user: User, engagement: Engagement) -> dict:
    members = membership_service.list_members(db, engagement.id)
    member_ids = {m.user_id for m in members}
    return {
        "engagement": engagement,
        "members": members,
        "candidates": [u for u in user_service.list_internal_users(db) if u.id not in member_ids],
        "roles": MEMBERSHIP_ROLES,
        "can_manage": user.bypasses_membership,
    }


@router.get("/engagements/{engagement_id}")
def engagement_detail(engagement_id: uuid.UUID, request: Request, db: Session = Depends(get_db),
                      user: User = Depends(current_user)):
    engagement = get_visible_engagement(db, user, engagement_id)
    return render(request, "engagement_detail.html", nav=f"board:{engagement.type_key}",
                  **_engagement_context(db, user, engagement), team=_team_context(db, user, engagement))


@router.post("/engagements/{engagement_id}/stage")
def engagement_stage(engagement_id: uuid.UUID, request: Request, stage: str = Form(""),
                     db: Session = Depends(get_db), user: User = Depends(current_user)):
    engagement = engagement_service.change_stage(db, user, engagement_id, stage=stage)
    db.commit()
    if is_htmx(request):
        return render(request, "partials/engagement_header.html", **_engagement_context(db, user, engagement))
    return _back(f"/engagements/{engagement_id}")


@router.post("/engagements/{engagement_id}/status")
def engagement_status(engagement_id: uuid.UUID, request: Request, status: str = Form(""),
                      db: Session = Depends(get_db), user: User = Depends(current_user)):
    engagement = engagement_service.change_status(db, user, engagement_id, status=status)
    db.commit()
    if is_htmx(request):
        return render(request, "partials/engagement_header.html", **_engagement_context(db, user, engagement))
    return _back(f"/engagements/{engagement_id}")


# ---------------------------------------------------------------- team assignment


def _team_response(request: Request, db: Session, user: User, engagement_id: uuid.UUID, compact: bool):
    engagement = get_visible_engagement(db, user, engagement_id)
    return render(request, "partials/team_panel.html", team=_team_context(db, user, engagement), compact=compact)


def _capacity_context(db: Session, user: User) -> dict:
    people = capacity_service.list_capacity(db, user)
    return {
        "people": people,
        "max_hours": max([p.open_hours for p in people] + [1]),
        "open_engagements": capacity_service.list_open_engagements(db),
        "roles": MEMBERSHIP_ROLES,
    }


def _person_response(request: Request, db: Session, user: User, person_id: uuid.UUID):
    ctx = _capacity_context(db, user)
    person = next((p for p in ctx["people"] if p.user.id == person_id), None)
    if person is None:
        raise NotFound("User not found")
    return render(request, "partials/person_card.html", p=person, **ctx)


@router.get("/team")
def team_capacity(request: Request, db: Session = Depends(get_db), user: User = Depends(current_user)):
    """Team capacity: assignments grouped per person (ops/admin)."""
    return render(request, "team_capacity.html", nav="team", **_capacity_context(db, user))


@router.post("/team/people/{person_id}/allocate")
def team_allocate(person_id: uuid.UUID, request: Request, engagement_id: str = Form(""), role: str = Form(""),
                  db: Session = Depends(get_db), user: User = Depends(current_user)):
    eid = _uuid(engagement_id)
    if eid is None:
        raise ValidationError("Pick an engagement")
    membership_service.assign_member(db, user, eid, user_id=person_id, role=role)
    db.commit()
    if is_htmx(request):
        return _person_response(request, db, user, person_id)
    return _back("/team")


@router.get("/team/engagements")
def team_overview(request: Request, db: Session = Depends(get_db), user: User = Depends(current_user)):
    require_ops_or_admin(user)
    engagements = engagement_service.list_visible_engagements(db, user)
    rows = [{"engagement": e, "team": _team_context(db, user, e)} for e in engagements]
    rows.sort(key=lambda r: (bool(r["team"]["members"]), r["engagement"].status != "active",
                             r["engagement"].client.name, r["engagement"].name))
    load: dict[uuid.UUID, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for m in db.scalars(select(EngagementMembership)):
        load[m.user_id][m.role] += 1
    return render(request, "team.html", nav="team", rows=rows, users=user_service.list_internal_users(db),
                  load=load, roles=MEMBERSHIP_ROLES)


@router.post("/engagements/{engagement_id}/members")
def member_assign(engagement_id: uuid.UUID, request: Request, user_id: str = Form(""), role: str = Form(""),
                  compact: bool = Form(False), panel: str = Form(""), db: Session = Depends(get_db),
                  user: User = Depends(current_user)):
    uid = _uuid(user_id)
    if uid is None:
        raise ValidationError("Pick someone to assign")
    membership_service.assign_member(db, user, engagement_id, user_id=uid, role=role)
    db.commit()
    if is_htmx(request) and panel == "person":
        return _person_response(request, db, user, uid)
    if is_htmx(request):
        return _team_response(request, db, user, engagement_id, compact)
    return _back(f"/engagements/{engagement_id}")


@router.post("/engagements/{engagement_id}/members/{member_user_id}/remove")
def member_remove(engagement_id: uuid.UUID, member_user_id: uuid.UUID, request: Request,
                  compact: bool = Form(False), panel: str = Form(""), db: Session = Depends(get_db),
                  user: User = Depends(current_user)):
    membership_service.remove_member(db, user, engagement_id, user_id=member_user_id)
    db.commit()
    if is_htmx(request) and panel == "person":
        return _person_response(request, db, user, member_user_id)
    if is_htmx(request):
        return _team_response(request, db, user, engagement_id, compact)
    return _back(f"/engagements/{engagement_id}")


# ---------------------------------------------------------------- deliverables


@router.get("/engagements/{engagement_id}/deliverables/new")
def deliverable_new(engagement_id: uuid.UUID, request: Request, kind: str = "", parent_id: str = "",
                    db: Session = Depends(get_db), user: User = Depends(current_user)):
    engagement = get_editable_engagement(db, user, engagement_id)
    kinds = deliverable_service.kinds_for_type(db, engagement.type_key)
    return render(request, "deliverable_new.html", nav=f"board:{engagement.type_key}", engagement=engagement,
                  kinds=kinds, selected_kind=kind or kinds[0].kind, selected_parent=parent_id,
                  parents=deliverable_service.parent_candidates(db, engagement),
                  team=membership_service.list_members(db, engagement.id),
                  existing=list(db.scalars(select(Deliverable).where(Deliverable.engagement_id == engagement.id)
                                           .order_by(Deliverable.kind, Deliverable.name))),
                  priorities=deliverable_service.PRIORITIES)


@router.post("/engagements/{engagement_id}/deliverables")
async def deliverable_create(engagement_id: uuid.UUID, request: Request, db: Session = Depends(get_db),
                             user: User = Depends(current_user)):
    form = await request.form()
    deliverable = deliverable_service.create_deliverable(
        db, user, engagement_id, kind=form.get("kind", ""), name=form.get("name", ""),
        parent_id=_uuid(form.get("parent_id")), internal_assignee_user_id=_uuid(form.get("assignee_id")),
        priority=form.get("priority") or None, target_date=form.get("target_date") or None,
        hours_estimated=form.get("hours_estimated") or None,
    )
    for blocker_id in form.getlist("blocker_ids"):
        deliverable_service.add_blocker(db, user, deliverable.id, blocker_id=_uuid(blocker_id))
    db.commit()
    return _back(f"/deliverables/{deliverable.id}")


@router.post("/deliverables/{deliverable_id}/pipeline")
def deliverable_pipeline(deliverable_id: uuid.UUID, request: Request, pipeline_status: str = Form(""),
                         panel: str = Form(""), db: Session = Depends(get_db), user: User = Depends(current_user)):
    """Inline status change from the engagement view; re-renders the whole deliverables
    panel so parent rollups and dep_states stay consistent."""
    d = deliverable_service.update_deliverable(db, user, deliverable_id, pipeline_status=pipeline_status)
    db.commit()
    if is_htmx(request) and panel:
        engagement = get_visible_engagement(db, user, d.engagement_id)
        return render(request, "partials/deliverables_panel.html", **_engagement_context(db, user, engagement))
    return _back(f"/deliverables/{deliverable_id}")


@router.get("/engagements/{engagement_id}/deliverables-panel")
def deliverables_panel(engagement_id: uuid.UUID, request: Request, db: Session = Depends(get_db),
                       user: User = Depends(current_user)):
    engagement = get_visible_engagement(db, user, engagement_id)
    return render(request, "partials/deliverables_panel.html", **_engagement_context(db, user, engagement))


def _chat_response(request: Request, db: Session, user: User, deliverable_id: uuid.UUID, **headers):
    d = get_visible_deliverable(db, user, deliverable_id)
    activity = list(reversed(deliverable_service.list_activity(db, d.id)))  # oldest first, chat-style
    resp = render(request, "partials/chat.html", d=d, activity=activity)
    resp.headers.update(headers)
    return resp


@router.get("/deliverables/{deliverable_id}/chat")
def deliverable_chat(deliverable_id: uuid.UUID, request: Request, db: Session = Depends(get_db),
                     user: User = Depends(current_user)):
    return _chat_response(request, db, user, deliverable_id)


@router.post("/deliverables/{deliverable_id}/chat")
def deliverable_chat_post(deliverable_id: uuid.UUID, request: Request, body: str = Form(""),
                          db: Session = Depends(get_db), user: User = Depends(current_user)):
    deliverable_service.add_note(db, user, deliverable_id, body=body)
    db.commit()
    # notesChanged makes the engagement page refresh its comment counts.
    return _chat_response(request, db, user, deliverable_id, **{"HX-Trigger": "notesChanged"})


@router.get("/deliverables/{deliverable_id}")
def deliverable_detail(deliverable_id: uuid.UUID, request: Request, db: Session = Depends(get_db),
                       user: User = Depends(current_user)):
    d = get_visible_deliverable(db, user, deliverable_id)
    engagement = d.engagement
    blockers = deliverable_service.blocker_map(db, [d.id])[d.id]
    child_blockers = deliverable_service.blocker_map(db, [c.id for c in d.children])
    dep_state = deliverable_service.derive_dep_state(d, blockers)

    def child_signal(c):
        return deliverable_service.derive_dep_signal(c, deliverable_service.derive_dep_state(c, child_blockers[c.id]),
                                                     child_blockers[c.id])

    return render(
        request, "deliverable_detail.html", nav=f"board:{engagement.type_key}", d=d, engagement=engagement,
        kind=next(k for k in deliverable_service.kinds_for_type(db, engagement.type_key) if k.kind == d.kind),
        dep_state=dep_state, signal=deliverable_service.derive_dep_signal(d, dep_state, blockers), blockers=blockers,
        blocking=deliverable_service.blocking_list(db, d.id),
        children=[(c, child_signal(c)) for c in d.children],
        child_kind=next((k for k in deliverable_service.kinds_for_type(db, engagement.type_key)
                         if k.parent_kind == d.kind), None),
        candidates=deliverable_service.blocker_candidates(db, d),
        activity=deliverable_service.list_activity(db, d.id),
        can_edit=can_edit_engagement(db, user, engagement.id),
        team=membership_service.list_members(db, engagement.id),
        pipeline_statuses=PIPELINE_STATUSES, priorities=deliverable_service.PRIORITIES,
    )


@router.post("/deliverables/{deliverable_id}/edit")
async def deliverable_edit(deliverable_id: uuid.UUID, request: Request, db: Session = Depends(get_db),
                           user: User = Depends(current_user)):
    form = await request.form()
    deliverable_service.update_deliverable(
        db, user, deliverable_id, name=form.get("name", ""),
        internal_assignee_user_id=_uuid(form.get("assignee_id")), priority=form.get("priority") or None,
        target_date=form.get("target_date") or None, hours_estimated=form.get("hours_estimated") or None,
    )
    db.commit()
    return _back(f"/deliverables/{deliverable_id}")


@router.post("/deliverables/{deliverable_id}/block")
def deliverable_block(deliverable_id: uuid.UUID, blocked: bool = Form(False), blocked_reason: str = Form(""),
                      db: Session = Depends(get_db), user: User = Depends(current_user)):
    deliverable_service.update_deliverable(db, user, deliverable_id, blocked=blocked, blocked_reason=blocked_reason)
    db.commit()
    return _back(f"/deliverables/{deliverable_id}")


@router.post("/deliverables/{deliverable_id}/applicable")
def deliverable_applicable(deliverable_id: uuid.UUID, not_applicable: bool = Form(False),
                           db: Session = Depends(get_db), user: User = Depends(current_user)):
    deliverable_service.update_deliverable(db, user, deliverable_id, not_applicable=not_applicable)
    db.commit()
    return _back(f"/deliverables/{deliverable_id}")


@router.post("/deliverables/{deliverable_id}/notes")
def deliverable_note(deliverable_id: uuid.UUID, body: str = Form(""), db: Session = Depends(get_db),
                     user: User = Depends(current_user)):
    deliverable_service.add_note(db, user, deliverable_id, body=body)
    db.commit()
    return _back(f"/deliverables/{deliverable_id}")


@router.post("/deliverables/{deliverable_id}/blockers")
def deliverable_add_blocker(deliverable_id: uuid.UUID, blocker_id: str = Form(""), db: Session = Depends(get_db),
                            user: User = Depends(current_user)):
    bid = _uuid(blocker_id)
    if bid is None:
        raise ValidationError("Pick the deliverable it's waiting on")
    deliverable_service.add_blocker(db, user, deliverable_id, blocker_id=bid)
    db.commit()
    return _back(f"/deliverables/{deliverable_id}")


@router.post("/deliverables/{deliverable_id}/blockers/{blocker_id}/remove")
def deliverable_remove_blocker(deliverable_id: uuid.UUID, blocker_id: uuid.UUID, db: Session = Depends(get_db),
                               user: User = Depends(current_user)):
    deliverable_service.remove_blocker(db, user, deliverable_id, blocker_id=blocker_id)
    db.commit()
    return _back(f"/deliverables/{deliverable_id}")


# ---------------------------------------------------------------- event log (ops/admin)


@router.get("/events")
def event_log(request: Request, engagement: str = "", db: Session = Depends(get_db),
              user: User = Depends(current_user)):
    require_ops_or_admin(user)
    stmt = select(Event).order_by(Event.id.desc()).limit(300)
    scoped = None
    if engagement:
        scoped = get_visible_engagement(db, user, _uuid(engagement))
        deliverable_ids = select(Deliverable.id).where(Deliverable.engagement_id == scoped.id)
        stmt = stmt.where(or_(Event.entity_id == scoped.id, Event.entity_id.in_(deliverable_ids)))
    return render(request, "events.html", nav="events", events=list(db.scalars(stmt)), scoped=scoped)
