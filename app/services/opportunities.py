"""Opportunities, products and line items — the CRM slice (CLAUDE.md §3 Opportunities).

- Every opportunity has at least one line item; its amount is the sum of them, never stored.
- Probability and forecast category default from the stage and can be overridden while
  the opportunity is open. Closed stages fix them (won 100/closed, lost 0/omitted).
- Stage rules are deliberately minimal: Closed Lost needs a reason. Closing sets the close
  date to today unless one is given, so a deal lands in the period it actually closed.
- Every change emits an event whose payload carries before/after values, which is what the
  forecast's "recent movement" reads.

Access (CLAUDE.md §3 Identity & access): opportunities:use reads every opportunity and edits
its own; opportunities:manage edits and reassigns any, and maintains the product catalog.
"""

import uuid
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.access import (
    get_editable_opportunity,
    get_visible_client,
    get_visible_opportunity,
    require_module,
    visible_opportunities_stmt,
)
from app.errors import NotFound, ValidationError
from app.events import emit, mutation
from app.models import (
    OVERRIDABLE_FORECAST_CATEGORIES,
    PRICING_MODELS,
    EngagementType,
    Opportunity,
    OpportunityLineItem,
    OpportunityStage,
    Product,
    User,
)


# ---------------------------------------------------------------- parsing helpers


def _decimal(value, label: str, *, positive: bool = False) -> Decimal:
    try:
        d = Decimal(str(value).replace(",", "").replace("$", "").strip())
    except (InvalidOperation, ValueError):
        raise ValidationError(f"{label} must be a number")
    if d < 0 or (positive and d == 0):
        raise ValidationError(f"{label} must be {'greater than zero' if positive else 'zero or more'}")
    return d.quantize(Decimal("0.01"))


def _date(value, label: str = "Close date") -> date:
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value))
    except ValueError:
        raise ValidationError(f"{label} is required (YYYY-MM-DD)")


def _probability(value) -> int | None:
    if value in (None, ""):
        return None
    try:
        p = int(value)
    except (TypeError, ValueError):
        raise ValidationError("Probability must be a whole number")
    if not 0 <= p <= 100:
        raise ValidationError("Probability must be between 0 and 100")
    return p


# ---------------------------------------------------------------- stages & products


def list_stages(db: Session) -> list[OpportunityStage]:
    return list(db.scalars(select(OpportunityStage).order_by(OpportunityStage.sort_order)))


def get_stage(db: Session, key: str) -> OpportunityStage:
    stage = db.get(OpportunityStage, key)
    if stage is None:
        raise ValidationError(f"Unknown stage '{key}'")
    return stage


def list_products(db: Session, *, include_inactive: bool = False) -> list[Product]:
    stmt = select(Product).order_by(Product.name)
    if not include_inactive:
        stmt = stmt.where(Product.active.is_(True))
    return list(db.scalars(stmt))


def _product_fields(db: Session, *, name: str, pricing_model: str, default_unit_price,
                    engagement_type_key: str | None) -> dict:
    name = name.strip()
    if not name:
        raise ValidationError("Product name is required")
    if pricing_model not in PRICING_MODELS:
        raise ValidationError(f"Pricing model must be one of {', '.join(PRICING_MODELS)}")
    if engagement_type_key and db.get(EngagementType, engagement_type_key) is None:
        raise ValidationError(f"Unknown engagement type '{engagement_type_key}'")
    price = None if default_unit_price in (None, "") else _decimal(default_unit_price, "Default price")
    return {"name": name, "pricing_model": pricing_model, "default_unit_price": price,
            "engagement_type_key": engagement_type_key or None}


@mutation("product_created")
def create_product(db: Session, actor: User, *, name: str, pricing_model: str, default_unit_price=None,
                   engagement_type_key: str | None = None) -> Product:
    require_module(actor, "opportunities", "manage")
    fields = _product_fields(db, name=name, pricing_model=pricing_model, default_unit_price=default_unit_price,
                             engagement_type_key=engagement_type_key)
    if db.scalar(select(Product.id).where(Product.name == fields["name"])):
        raise ValidationError(f"A product called '{fields['name']}' already exists")
    product = Product(**fields)
    db.add(product)
    db.flush()
    emit(db, entity_type="product", entity_id=product.id, event_type="product_created", actor=actor, payload=fields)
    return product


@mutation("product_updated")
def update_product(db: Session, actor: User, product_id: uuid.UUID, *, name: str, pricing_model: str,
                   default_unit_price=None, engagement_type_key: str | None = None,
                   active: bool = True) -> Product:
    """Existing line items keep their own quantity and price; editing a product never reprices a deal."""
    require_module(actor, "opportunities", "manage")
    product = db.get(Product, product_id)
    if product is None:
        raise NotFound("Product not found")
    fields = _product_fields(db, name=name, pricing_model=pricing_model, default_unit_price=default_unit_price,
                             engagement_type_key=engagement_type_key)
    if db.scalar(select(Product.id).where(Product.name == fields["name"], Product.id != product.id)):
        raise ValidationError(f"A product called '{fields['name']}' already exists")
    fields["active"] = active
    changes = {k: [getattr(product, k), v] for k, v in fields.items() if getattr(product, k) != v}
    if not changes:
        raise ValidationError("Nothing to change")
    for k, v in fields.items():
        setattr(product, k, v)
    emit(db, entity_type="product", entity_id=product.id, event_type="product_updated", actor=actor,
         payload=changes)
    return product


def _active_product(db: Session, product_id: uuid.UUID | None) -> Product:
    product = db.get(Product, product_id) if product_id else None
    if product is None:
        raise ValidationError("Pick a product")
    if not product.active:
        raise ValidationError(f"{product.name} is no longer offered")
    return product


def _line_item_values(product: Product, quantity, unit_price) -> tuple[Decimal, Decimal]:
    qty = _decimal(quantity if quantity not in (None, "") else 1, "Quantity", positive=True)
    if unit_price in (None, ""):
        if product.default_unit_price is None:
            raise ValidationError(f"{product.name} has no default price; enter one")
        price = product.default_unit_price
    else:
        price = _decimal(unit_price, "Unit price")
    return qty, price


# ---------------------------------------------------------------- opportunities


def list_visible_opportunities(db: Session, user: User, *, include_closed: bool = True,
                               client_id: uuid.UUID | None = None,
                               owner_id: uuid.UUID | None = None) -> list[Opportunity]:
    stmt = visible_opportunities_stmt(user).join(OpportunityStage)
    if not include_closed:
        stmt = stmt.where(OpportunityStage.is_closed.is_(False))
    if client_id:
        stmt = stmt.where(Opportunity.client_id == client_id)
    if owner_id:
        stmt = stmt.where(Opportunity.owner_user_id == owner_id)
    return list(db.scalars(stmt.order_by(Opportunity.close_date, Opportunity.name)).unique())


def list_sales_users(db: Session) -> list[User]:
    """Who can own an opportunity: active internal users with opportunities access."""
    users = db.scalars(select(User).where(User.user_type == "internal", User.status == "active")
                       .order_by(User.display_name))
    return [u for u in users if u.can("opportunities")]


def _owner(db: Session, actor: User, owner_user_id: uuid.UUID | None) -> User:
    if owner_user_id is None or owner_user_id == actor.id:
        return actor
    if not actor.can("opportunities", "manage"):
        raise ValidationError("Only a sales lead can give an opportunity to someone else")
    owner = db.get(User, owner_user_id)
    if owner is None or not owner.can("opportunities"):
        raise ValidationError("The owner needs access to opportunities")
    return owner


def _enter_stage(opp: Opportunity, stage: OpportunityStage, *, lost_reason: str | None,
                 close_date: date | None) -> None:
    lost_reason = (lost_reason or "").strip() or None
    if stage.key == "closed_lost" and not lost_reason:
        raise ValidationError("Say why the opportunity was lost")
    opp.stage_key, opp.stage = stage.key, stage
    opp.lost_reason = lost_reason if stage.is_closed and not stage.is_won else None
    if stage.is_closed:
        opp.closed_at = opp.closed_at or datetime.now(timezone.utc)
        opp.close_date = close_date or date.today()
    else:
        opp.closed_at = None
        if close_date:
            opp.close_date = close_date


def _snapshot(opp: Opportunity) -> dict:
    return {"stage": opp.stage_key, "amount": opp.amount, "probability": opp.effective_probability,
            "forecast_category": opp.effective_forecast_category, "close_date": opp.close_date,
            "owner_user_id": opp.owner_user_id, "client_id": opp.client_id, "name": opp.name}


@mutation("opportunity_created")
def create_opportunity(db: Session, actor: User, *, client_id: uuid.UUID, name: str, stage_key: str,
                       close_date, product_id: uuid.UUID | None, quantity=1, unit_price=None,
                       owner_user_id: uuid.UUID | None = None, next_step: str = "", source: str = "",
                       lost_reason: str | None = None, probability=None,
                       forecast_category: str | None = None) -> Opportunity:
    """Created with its first line item: an opportunity always has at least one product."""
    require_module(actor, "opportunities")
    client = get_visible_client(db, actor, client_id)
    name = name.strip()
    if not name:
        raise ValidationError("Opportunity name is required")
    stage = get_stage(db, stage_key)
    product = _active_product(db, product_id)
    qty, price = _line_item_values(product, quantity, unit_price)
    owner = _owner(db, actor, owner_user_id)
    opp = Opportunity(client_id=client.id, name=name, owner_user_id=owner.id, close_date=_date(close_date),
                      next_step=next_step.strip() or None, source=source.strip() or None,
                      probability=_probability(probability), forecast_category=_category(forecast_category),
                      line_items=[OpportunityLineItem(product_id=product.id, product=product, quantity=qty,
                                                      unit_price=price)])
    _enter_stage(opp, stage, lost_reason=lost_reason, close_date=_date(close_date))
    db.add(opp)
    db.flush()
    emit(db, entity_type="opportunity", entity_id=opp.id, event_type="opportunity_created", actor=actor,
         payload=_snapshot(opp))
    return opp


def _category(value: str | None) -> str | None:
    if value in (None, ""):
        return None
    if value not in OVERRIDABLE_FORECAST_CATEGORIES:
        raise ValidationError(f"Forecast category must be one of {', '.join(OVERRIDABLE_FORECAST_CATEGORIES)}")
    return value


_UNSET = object()


@mutation("opportunity_updated")
def update_opportunity(db: Session, actor: User, opportunity_id: uuid.UUID, *, name=_UNSET, close_date=_UNSET,
                       owner_user_id=_UNSET, probability=_UNSET, forecast_category=_UNSET, next_step=_UNSET,
                       source=_UNSET) -> Opportunity:
    """Only the fields passed change. Probability/forecast overrides only apply while open."""
    opp = get_editable_opportunity(db, actor, opportunity_id)
    new: dict = {}
    if name is not _UNSET:
        if not name.strip():
            raise ValidationError("Opportunity name is required")
        new["name"] = name.strip()
    if close_date is not _UNSET:
        new["close_date"] = _date(close_date)
    if owner_user_id is not _UNSET and owner_user_id != opp.owner_user_id:
        if not actor.can("opportunities", "manage"):
            raise ValidationError("Only a sales lead can reassign an opportunity")
        new["owner_user_id"] = _owner(db, actor, owner_user_id).id
    for field, value, parse in (("probability", probability, _probability),
                                ("forecast_category", forecast_category, _category)):
        if value is not _UNSET:
            parsed = parse(value)
            if opp.is_closed and parsed != getattr(opp, field):
                raise ValidationError(f"A closed opportunity's {field.replace('_', ' ')} is fixed by its stage")
            new[field] = parsed
    for field, value in (("next_step", next_step), ("source", source)):
        if value is not _UNSET:
            new[field] = (value or "").strip() or None
    changes = {k: [getattr(opp, k), v] for k, v in new.items() if getattr(opp, k) != v}
    if not changes:
        raise ValidationError("Nothing to change")
    for k, (_, v) in changes.items():
        setattr(opp, k, v)
    db.flush()
    db.expire(opp, ["owner"])
    emit(db, entity_type="opportunity", entity_id=opp.id, event_type="opportunity_updated", actor=actor,
         payload={"changes": changes, "amount": opp.amount})
    return opp


@mutation("opportunity_stage_changed")
def change_opportunity_stage(db: Session, actor: User, opportunity_id: uuid.UUID, *, stage_key: str,
                             lost_reason: str | None = None, close_date=None) -> Opportunity:
    opp = get_editable_opportunity(db, actor, opportunity_id)
    stage = get_stage(db, stage_key)
    previous = _snapshot(opp)
    if stage.key == opp.stage_key:
        raise ValidationError(f"Already in {stage.label}")
    _enter_stage(opp, stage, lost_reason=lost_reason, close_date=_date(close_date) if close_date else None)
    db.flush()
    emit(db, entity_type="opportunity", entity_id=opp.id, event_type="opportunity_stage_changed", actor=actor,
         payload={"from": previous["stage"], "to": stage.key, "is_won": stage.is_won, "is_closed": stage.is_closed,
                  "lost_reason": opp.lost_reason, "amount": opp.amount, "probability": opp.effective_probability,
                  "forecast_category": opp.effective_forecast_category,
                  "close_date": [previous["close_date"], opp.close_date]})
    return opp


def _refresh_items(db: Session, opp: Opportunity) -> None:
    db.flush()
    db.expire(opp, ["line_items"])


@mutation("opportunity_line_item_added")
def add_line_item(db: Session, actor: User, opportunity_id: uuid.UUID, *, product_id: uuid.UUID | None,
                  quantity=1, unit_price=None, description: str = "") -> OpportunityLineItem:
    opp = get_editable_opportunity(db, actor, opportunity_id)
    before = opp.amount
    product = _active_product(db, product_id)
    qty, price = _line_item_values(product, quantity, unit_price)
    item = OpportunityLineItem(opportunity_id=opp.id, product_id=product.id, quantity=qty, unit_price=price,
                               description=description.strip() or None)
    db.add(item)
    _refresh_items(db, opp)
    emit(db, entity_type="opportunity", entity_id=opp.id, event_type="opportunity_line_item_added", actor=actor,
         payload={"line_item_id": item.id, "product": product.name, "quantity": qty, "unit_price": price,
                  "amount": [before, opp.amount]})
    return item


def _item(db: Session, actor: User, line_item_id: uuid.UUID) -> tuple[OpportunityLineItem, Opportunity]:
    item = db.get(OpportunityLineItem, line_item_id)
    if item is None:
        raise NotFound("Line item not found")
    return item, get_editable_opportunity(db, actor, item.opportunity_id)


@mutation("opportunity_line_item_updated")
def update_line_item(db: Session, actor: User, line_item_id: uuid.UUID, *, quantity, unit_price,
                     description: str = "") -> OpportunityLineItem:
    item, opp = _item(db, actor, line_item_id)
    before = opp.amount
    qty, price = _line_item_values(item.product, quantity, unit_price)
    changes = {k: [getattr(item, k), v] for k, v in
               (("quantity", qty), ("unit_price", price), ("description", description.strip() or None))
               if getattr(item, k) != v}
    if not changes:
        raise ValidationError("Nothing to change")
    item.quantity, item.unit_price, item.description = qty, price, description.strip() or None
    _refresh_items(db, opp)
    emit(db, entity_type="opportunity", entity_id=opp.id, event_type="opportunity_line_item_updated", actor=actor,
         payload={"line_item_id": item.id, "product": item.product.name, "changes": changes,
                  "amount": [before, opp.amount]})
    return item


@mutation("opportunity_line_item_removed")
def remove_line_item(db: Session, actor: User, line_item_id: uuid.UUID) -> None:
    item, opp = _item(db, actor, line_item_id)
    if len(opp.line_items) <= 1:
        raise ValidationError("An opportunity needs at least one product; add another before removing this one")
    before = opp.amount
    product = item.product.name
    db.delete(item)
    _refresh_items(db, opp)
    emit(db, entity_type="opportunity", entity_id=opp.id, event_type="opportunity_line_item_removed", actor=actor,
         payload={"line_item_id": line_item_id, "product": product, "amount": [before, opp.amount]})


# ---------------------------------------------------------------- won → engagement bridge


def list_won_without_engagement(db: Session, user: User) -> list[Opportunity]:
    """Closed Won opportunities nothing is delivering yet: a handoff that hasn't happened."""
    won = list_visible_opportunities(db, user)
    return [o for o in won if o.is_won and not o.engagements]


def get_suggested_engagement_type(opp: Opportunity) -> str | None:
    """The engagement type of the first line item whose product is delivered as one."""
    return next((li.product.engagement_type_key for li in opp.line_items if li.product.engagement_type_key), None)

