"""Opportunities, pipeline, forecast and products pages (CLAUDE.md §3 Opportunities).
Thin, like pages.py: parse, call a service function, commit, render."""

import uuid

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from app.access import can_edit_opportunity, get_visible_opportunity, require_module
from app.db import get_db
from app.errors import ValidationError
from app.models import OVERRIDABLE_FORECAST_CATEGORIES, PRICING_MODELS, User
from app.services import clients as client_service
from app.services import engagements as engagement_service
from app.services import forecast as forecast_service
from app.services import opportunities as opp_service
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


# ---------------------------------------------------------------- pipeline


def _pipeline_context(db: Session, user: User, owner: str, show_closed: bool) -> dict:
    owner_id = user.id if owner == "mine" else _uuid(owner) if owner not in ("", "all") else None
    opps = opp_service.list_visible_opportunities(db, user, include_closed=show_closed, owner_id=owner_id)
    stages = opp_service.list_stages(db)
    return {
        "columns": forecast_service.derive_pipeline(stages, opps, show_closed=show_closed),
        "stages": stages, "owner": owner or "all", "show_closed": show_closed,
        "sales_users": opp_service.list_sales_users(db),
        "open_total": sum((o.amount for o in opps if not o.is_closed), 0),
        "open_weighted": sum((o.weighted_amount for o in opps if not o.is_closed), 0),
        "won_unlinked": opp_service.list_won_without_engagement(db, user),
        "can_edit": {o.id: can_edit_opportunity(user, o) for o in opps},
    }


@router.get("/pipeline")
def pipeline(request: Request, owner: str = "", show_closed: bool = False, db: Session = Depends(get_db),
             user: User = Depends(current_user)):
    require_module(user, "opportunities")
    return render(request, "opportunities/pipeline.html", nav="pipeline",
                  **_pipeline_context(db, user, owner, show_closed))


@router.post("/opportunities/{opportunity_id}/stage")
def opportunity_stage(opportunity_id: uuid.UUID, request: Request, stage_key: str = Form(""),
                      lost_reason: str = Form(""), panel: str = Form(""), owner: str = Form(""),
                      show_closed: bool = Form(False), db: Session = Depends(get_db),
                      user: User = Depends(current_user)):
    opp_service.change_opportunity_stage(db, user, opportunity_id, stage_key=stage_key, lost_reason=lost_reason)
    db.commit()
    if is_htmx(request) and panel == "pipeline":
        return render(request, "opportunities/_pipeline_board.html", **_pipeline_context(db, user, owner, show_closed))
    return _back(f"/opportunities/{opportunity_id}")


# ---------------------------------------------------------------- opportunities


@router.get("/opportunities/new")
def opportunity_new(request: Request, client_id: str = "", db: Session = Depends(get_db),
                    user: User = Depends(current_user)):
    require_module(user, "opportunities")
    return render(request, "opportunities/new.html", nav="pipeline",
                  clients=client_service.list_visible_clients(db, user), selected_client_id=client_id,
                  stages=[s for s in opp_service.list_stages(db) if not s.is_closed],
                  products=opp_service.list_products(db), sales_users=opp_service.list_sales_users(db))


@router.post("/opportunities")
def opportunity_create(client_id: str = Form(""), name: str = Form(""), stage_key: str = Form(""),
                       close_date: str = Form(""), product_id: str = Form(""), quantity: str = Form("1"),
                       unit_price: str = Form(""), owner_user_id: str = Form(""), next_step: str = Form(""),
                       source: str = Form(""), db: Session = Depends(get_db), user: User = Depends(current_user)):
    cid = _uuid(client_id)
    if cid is None:
        raise ValidationError("Pick a client")
    opp = opp_service.create_opportunity(db, user, client_id=cid, name=name, stage_key=stage_key,
                                         close_date=close_date, product_id=_uuid(product_id), quantity=quantity,
                                         unit_price=unit_price, owner_user_id=_uuid(owner_user_id),
                                         next_step=next_step, source=source)
    db.commit()
    return _back(f"/opportunities/{opp.id}")


@router.get("/opportunities/{opportunity_id}")
def opportunity_detail(opportunity_id: uuid.UUID, request: Request, db: Session = Depends(get_db),
                       user: User = Depends(current_user)):
    opp = get_visible_opportunity(db, user, opportunity_id)
    stages = opp_service.list_stages(db)
    linkable = []
    if user.bypasses_membership and opp.is_won:
        linkable = [e for e in engagement_service.list_visible_engagements(db, user)
                    if e.client_id == opp.client_id and e.opportunity_id is None]
    return render(request, "opportunities/detail.html", nav="pipeline", o=opp, stages=stages,
                  can_edit=can_edit_opportunity(user, opp), can_reassign=user.can("opportunities", "manage"),
                  sales_users=opp_service.list_sales_users(db), products=opp_service.list_products(db),
                  categories=OVERRIDABLE_FORECAST_CATEGORIES, linkable=linkable,
                  movement=forecast_service.list_movement(db, [opp], days=3650,
                                                          stages={s.key: s for s in stages}))


@router.post("/opportunities/{opportunity_id}/edit")
async def opportunity_edit(opportunity_id: uuid.UUID, request: Request, db: Session = Depends(get_db),
                           user: User = Depends(current_user)):
    form = await request.form()
    fields = {k: form.get(k, "") for k in ("name", "close_date", "next_step", "source") if k in form}
    if "probability" in form:
        fields["probability"] = form.get("probability") or None
    if "forecast_category" in form:
        fields["forecast_category"] = form.get("forecast_category") or None
    if "owner_user_id" in form:
        fields["owner_user_id"] = _uuid(form.get("owner_user_id"))
    opp_service.update_opportunity(db, user, opportunity_id, **fields)
    db.commit()
    return _back(f"/opportunities/{opportunity_id}")


@router.post("/opportunities/{opportunity_id}/line-items")
def line_item_add(opportunity_id: uuid.UUID, product_id: str = Form(""), quantity: str = Form("1"),
                  unit_price: str = Form(""), description: str = Form(""), db: Session = Depends(get_db),
                  user: User = Depends(current_user)):
    opp_service.add_line_item(db, user, opportunity_id, product_id=_uuid(product_id), quantity=quantity,
                              unit_price=unit_price, description=description)
    db.commit()
    return _back(f"/opportunities/{opportunity_id}#products")


@router.post("/line-items/{line_item_id}/edit")
def line_item_edit(line_item_id: uuid.UUID, quantity: str = Form(""), unit_price: str = Form(""),
                   description: str = Form(""), db: Session = Depends(get_db), user: User = Depends(current_user)):
    item = opp_service.update_line_item(db, user, line_item_id, quantity=quantity, unit_price=unit_price,
                                        description=description)
    db.commit()
    return _back(f"/opportunities/{item.opportunity_id}#products")


@router.post("/line-items/{line_item_id}/remove")
def line_item_remove(line_item_id: uuid.UUID, opportunity_id: str = Form(""), db: Session = Depends(get_db),
                     user: User = Depends(current_user)):
    opp_service.remove_line_item(db, user, line_item_id)
    db.commit()
    return _back(f"/opportunities/{opportunity_id}#products")


@router.post("/opportunities/{opportunity_id}/link-engagement")
def opportunity_link(opportunity_id: uuid.UUID, engagement_id: str = Form(""), unlink: str = Form(""),
                     db: Session = Depends(get_db), user: User = Depends(current_user)):
    eid = _uuid(engagement_id)
    if eid is None:
        raise ValidationError("Pick an engagement")
    engagement_service.link_opportunity(db, user, eid, opportunity_id=None if unlink else opportunity_id)
    db.commit()
    return _back(f"/opportunities/{opportunity_id}")


# ---------------------------------------------------------------- forecast


@router.get("/forecast")
def forecast(request: Request, period: str = "quarter", offset: int = 0, db: Session = Depends(get_db),
             user: User = Depends(current_user)):
    require_module(user, "opportunities")
    kind = period if period in forecast_service.PERIOD_KINDS else "quarter"
    offset = max(-12, min(12, offset))
    opps = opp_service.list_visible_opportunities(db, user)
    p = forecast_service.derive_period(kind, offset=offset)
    stages = {s.key: s for s in opp_service.list_stages(db)}
    fc = forecast_service.derive_forecast(opps, p)
    return render(request, "opportunities/forecast.html", nav="forecast", fc=fc, kind=kind, offset=offset,
                  period_kinds=forecast_service.PERIOD_KINDS,
                  outlook=forecast_service.derive_outlook(opps, kind, today=p.start),
                  movement=forecast_service.list_movement(db, opps, days=14, stages=stages),
                  won_unlinked=opp_service.list_won_without_engagement(db, user))


# ---------------------------------------------------------------- products


@router.get("/products")
def products(request: Request, db: Session = Depends(get_db), user: User = Depends(current_user)):
    require_module(user, "opportunities")
    return render(request, "opportunities/products.html", nav="pipeline",
                  products=opp_service.list_products(db, include_inactive=True), pricing_models=PRICING_MODELS,
                  types=engagement_service.list_engagement_types(db),
                  can_manage=user.can("opportunities", "manage"))


@router.post("/products")
def product_create(name: str = Form(""), pricing_model: str = Form(""), default_unit_price: str = Form(""),
                   engagement_type_key: str = Form(""), db: Session = Depends(get_db),
                   user: User = Depends(current_user)):
    opp_service.create_product(db, user, name=name, pricing_model=pricing_model,
                               default_unit_price=default_unit_price, engagement_type_key=engagement_type_key)
    db.commit()
    return _back("/products")


@router.post("/products/{product_id}/edit")
def product_edit(product_id: uuid.UUID, name: str = Form(""), pricing_model: str = Form(""),
                 default_unit_price: str = Form(""), engagement_type_key: str = Form(""),
                 active: bool = Form(False), db: Session = Depends(get_db), user: User = Depends(current_user)):
    opp_service.update_product(db, user, product_id, name=name, pricing_model=pricing_model,
                               default_unit_price=default_unit_price, engagement_type_key=engagement_type_key,
                               active=active)
    db.commit()
    return _back("/products")

