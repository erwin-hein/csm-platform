"""Admin page for the Malloy semantic model (CLAUDE.md §3 Semantic layer): browse its sources,
fields and views, read each view's Malloy and compiled SQL, and run a view read-only."""

from fastapi import APIRouter, Depends, Form, Request
from sqlalchemy.orm import Session

from app import semantic
from app.access import require_admin
from app.db import get_db
from app.errors import NotFound, ValidationError
from app.models import User
from app.web.auth import current_user
from app.web.templating import render

router = APIRouter(prefix="/admin/semantic")


@router.get("")
def semantic_model(request: Request, source: str = "", user: User = Depends(current_user)):
    require_admin(user)
    try:
        model = semantic.load_model()
    except semantic.SemanticModelMissing as e:
        return render(request, "admin/semantic.html", nav="semantic", missing=str(e))
    sources = {s["name"]: s for s in model["sources"]}
    if source == "_file":
        current = None
    else:
        current = sources.get(source) or model["sources"][0]
    views = [v for v in model["views"] if current and v["source"] == current["name"]]
    return render(request, "admin/semantic.html", nav="semantic", model=model, current=current,
                  show_file=source == "_file", views=views, stale=semantic.is_stale(model),
                  view_counts={s: sum(v["source"] == s for v in model["views"]) for s in sources})


@router.post("/run")
def semantic_run(request: Request, source: str = Form(""), view: str = Form(""), db: Session = Depends(get_db),
                 user: User = Depends(current_user)):
    require_admin(user)
    v = semantic.find_view(semantic.load_model(), source, view)
    if v is None:
        raise NotFound("No such view in the compiled model")
    try:
        result = semantic.run_view(db, v)
    except ValueError as e:
        raise ValidationError(str(e))
    return render(request, "admin/_semantic_result.html", result=result, view=v, limit=semantic.ROW_LIMIT)
