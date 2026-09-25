from pathlib import Path

from fastapi import Request
from fastapi.templating import Jinja2Templates

from decimal import Decimal

from app.models import FORECAST_CATEGORIES, PRICING_MODELS
from app.services.client_portal import CLIENT_STATUS_LABELS, VERDICT_LABELS
from app.services.deliverables import PIPELINE_LABELS

templates = Jinja2Templates(directory=Path(__file__).resolve().parent.parent / "templates")
templates.env.globals["PIPELINE_LABELS"] = PIPELINE_LABELS
templates.env.globals["status_labels"] = CLIENT_STATUS_LABELS   # client-facing wording
templates.env.globals["verdict_labels"] = VERDICT_LABELS
templates.env.globals["FORECAST_CATEGORIES"] = FORECAST_CATEGORIES
templates.env.globals["PRICING_MODELS"] = PRICING_MODELS


def usd(value, compact: bool = False) -> str:
    """USD only for now (CLAUDE.md §3 Opportunities). compact=True → $72k / $1.2M."""
    v = Decimal(str(value or 0))
    if compact and abs(v) >= 1_000_000:
        return f"${v / 1_000_000:.1f}M"
    if compact and abs(v) >= 1_000:
        return f"${v / 1_000:.0f}k"
    return f"${v:,.0f}"


def qty(value) -> str:
    v = Decimal(str(value or 0))
    return f"{v:,.0f}" if v == v.to_integral() else f"{v:,.2f}"


templates.env.filters["usd"] = usd
templates.env.filters["qty"] = qty


def is_htmx(request: Request) -> bool:
    return request.headers.get("hx-request") == "true"


def render(request: Request, name: str, status_code: int = 200, **context):
    context.setdefault("user", getattr(request.state, "user", None))
    context.setdefault("nav", "")
    context.setdefault("nav_types", getattr(request.state, "nav_types", []))
    return templates.TemplateResponse(request, name, context, status_code=status_code)
