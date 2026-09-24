from pathlib import Path

from fastapi import Request
from fastapi.templating import Jinja2Templates

from app.services.deliverables import PIPELINE_LABELS

templates = Jinja2Templates(directory=Path(__file__).resolve().parent.parent / "templates")
templates.env.globals["PIPELINE_LABELS"] = PIPELINE_LABELS


def is_htmx(request: Request) -> bool:
    return request.headers.get("hx-request") == "true"


def render(request: Request, name: str, status_code: int = 200, **context):
    context.setdefault("user", getattr(request.state, "user", None))
    context.setdefault("nav", "")
    context.setdefault("nav_types", getattr(request.state, "nav_types", []))
    return templates.TemplateResponse(request, name, context, status_code=status_code)
