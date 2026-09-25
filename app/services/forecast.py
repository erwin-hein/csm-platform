"""The pipeline and forecast views over opportunities (CLAUDE.md §3 Opportunities).

- Pipeline: one column per stage with count, total and weighted total. Unlike engagement
  boards, a sales stage is linear (an opportunity is in exactly one), so columns fit.
- Forecast: opportunities whose close date falls in a period (month or quarter), by owner
  and forecast category, raw and probability-weighted, with the usual cumulative calls:
  commit forecast = closed won + commit; best-case forecast = that + best case.
- Movement: what changed recently, read straight from the opportunity events.
"""

import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import FORECAST_CATEGORIES, Event, Opportunity, OpportunityStage, User

ZERO = Decimal(0)
PERIOD_KINDS = {"quarter": "Quarter", "month": "Month"}


# ---------------------------------------------------------------- pipeline


@dataclass
class PipelineColumn:
    stage: OpportunityStage
    cards: list[Opportunity]

    @property
    def total(self) -> Decimal:
        return sum((o.amount for o in self.cards), ZERO)

    @property
    def weighted(self) -> Decimal:
        return sum((o.weighted_amount for o in self.cards), ZERO)


def derive_pipeline(stages: list[OpportunityStage], opps: list[Opportunity], *,
                    show_closed: bool) -> list[PipelineColumn]:
    cols = [PipelineColumn(s, []) for s in stages if show_closed or not s.is_closed]
    by_key = {c.stage.key: c for c in cols}
    for o in sorted(opps, key=lambda o: (o.close_date, -o.amount)):
        if o.stage_key in by_key:
            by_key[o.stage_key].cards.append(o)
    return cols


# ---------------------------------------------------------------- periods


@dataclass(frozen=True)
class Period:
    kind: str
    start: date
    end: date  # inclusive

    @property
    def label(self) -> str:
        if self.kind == "month":
            return self.start.strftime("%b %Y")
        return f"Q{(self.start.month - 1) // 3 + 1} {self.start.year}"

    def contains(self, d: date) -> bool:
        return self.start <= d <= self.end


def _add_months(d: date, months: int) -> date:
    m = d.month - 1 + months
    return date(d.year + m // 12, m % 12 + 1, 1)


def derive_period(kind: str, today: date | None = None, offset: int = 0) -> Period:
    kind = kind if kind in PERIOD_KINDS else "quarter"
    today = today or date.today()
    size = 3 if kind == "quarter" else 1
    first = date(today.year, (today.month - 1) // size * size + 1, 1)
    start = _add_months(first, offset * size)
    return Period(kind, start, _add_months(start, size) - timedelta(days=1))


# ---------------------------------------------------------------- forecast


@dataclass
class ForecastRow:
    label: str
    owner: User | None = None
    by_category: dict[str, Decimal] = field(default_factory=lambda: {k: ZERO for k in FORECAST_CATEGORIES})
    weighted_open: Decimal = ZERO
    lost: Decimal = ZERO
    count: int = 0

    def add(self, o: Opportunity) -> None:
        self.count += 1
        if o.is_closed and not o.is_won:
            self.lost += o.amount
            return
        self.by_category[o.effective_forecast_category] += o.amount
        if not o.is_closed:
            self.weighted_open += o.weighted_amount

    @property
    def won(self) -> Decimal:
        return self.by_category["closed"]

    @property
    def commit_forecast(self) -> Decimal:
        return self.won + self.by_category["commit"]

    @property
    def best_case_forecast(self) -> Decimal:
        return self.commit_forecast + self.by_category["best_case"]

    @property
    def open_pipeline(self) -> Decimal:
        return self.by_category["commit"] + self.by_category["best_case"] + self.by_category["pipeline"]


@dataclass
class Forecast:
    period: Period
    rows: list[ForecastRow]
    total: ForecastRow
    deals: list[Opportunity]


def derive_forecast(opps: list[Opportunity], period: Period) -> Forecast:
    in_period = [o for o in opps if period.contains(o.close_date)]
    rows: dict[uuid.UUID | None, ForecastRow] = {}
    total = ForecastRow("Total")
    for o in in_period:
        key = o.owner_user_id
        if key not in rows:
            rows[key] = ForecastRow(o.owner.label if o.owner else "No owner", o.owner)
        rows[key].add(o)
        total.add(o)
    # Open deals first (commit → best case → pipeline → omitted), then won, then lost.
    order = {"commit": 0, "best_case": 1, "pipeline": 2, "omitted": 3}
    deals = sorted(in_period, key=lambda o: (o.is_closed, o.is_closed and not o.is_won,
                                             order.get(o.effective_forecast_category, 9), o.close_date, -o.amount))
    return Forecast(period, sorted(rows.values(), key=lambda r: (r.owner is None, r.label.lower())), total, deals)


def derive_outlook(opps: list[Opportunity], kind: str, *, periods: int = 4,
                   today: date | None = None) -> list[tuple[Period, ForecastRow]]:
    """This period and the next few, one total row each."""
    out = []
    for i in range(periods):
        p = derive_period(kind, today, i)
        out.append((p, derive_forecast(opps, p).total))
    return out


# ---------------------------------------------------------------- movement (from events)


@dataclass
class Movement:
    at: datetime
    opportunity: Opportunity
    actor: User | None
    kind: str      # 'new' | 'stage' | 'won' | 'lost' | 'slipped' | 'pulled_in' | 'amount' | 'owner'
    text: str


def _usd(value) -> str:
    return f"${Decimal(str(value)):,.0f}"


def list_movement(db: Session, opps: list[Opportunity], *, days: int = 14,
                  stages: dict[str, OpportunityStage] | None = None) -> list[Movement]:
    """Recent changes to the given opportunities, described from their event payloads."""
    by_id = {o.id: o for o in opps}
    if not by_id:
        return []
    since = datetime.now(timezone.utc) - timedelta(days=days)
    events = db.scalars(select(Event).where(Event.entity_type == "opportunity", Event.entity_id.in_(by_id),
                                            Event.occurred_at >= since).order_by(Event.id.desc()))
    label = (lambda k: stages[k].label if stages and k in stages else k)
    out: list[Movement] = []
    for ev in events:
        o, p = by_id[ev.entity_id], ev.payload or {}

        def add(kind: str, text: str) -> None:
            out.append(Movement(ev.occurred_at, o, ev.actor, kind, text))

        if ev.event_type == "opportunity_created":
            add("new", f"New in {label(p.get('stage'))} at {_usd(p.get('amount', 0))}")
        elif ev.event_type == "opportunity_stage_changed":
            kind = "won" if p.get("is_won") else "lost" if p.get("is_closed") else "stage"
            text = f"{label(p.get('from'))} → {label(p.get('to'))}"
            if kind == "lost" and p.get("lost_reason"):
                text += f" ({p['lost_reason']})"
            add(kind, text)
        elif ev.event_type == "opportunity_updated":
            changes = p.get("changes", {})
            if "close_date" in changes:
                old, new = changes["close_date"]
                add("slipped" if new > old else "pulled_in",
                    f"Close date {date.fromisoformat(old):%b %d} → {date.fromisoformat(new):%b %d}")
            if "owner_user_id" in changes:
                add("owner", "Reassigned")
        elif ev.event_type.startswith("opportunity_line_item_"):
            before, after = p.get("amount", [0, 0])
            if Decimal(str(before)) != Decimal(str(after)):
                add("amount", f"Amount {_usd(before)} → {_usd(after)} ({p.get('product')})")
    return out
