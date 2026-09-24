"""The client-facing portal (CLAUDE.md §3 Client-facing portal). Its own routes and
templates, never the internal pages behind a flag: a client user can only reach
/portal/*, and everything here resolves access through the client_* rules."""

import uuid

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from app.access import get_client_deliverable, get_client_engagement
from app.db import get_db
from app.models import UAT_STATUS, User
from app.services import client_portal
from app.web.auth import current_client_user
from app.web.templating import render

router = APIRouter(prefix="/portal")

def _render(request: Request, name: str, **ctx):
    return render(request, name, nav="portal", uat_status=UAT_STATUS, **ctx)


@router.get("")
def portal_home(request: Request, db: Session = Depends(get_db), user: User = Depends(current_client_user)):
    rows = []
    for engagement, membership in client_portal.list_client_engagements(db, user):
        sections = client_portal.derive_client_view(db, engagement, viewer=user, membership=membership)
        rows.append({"engagement": engagement, "membership": membership,
                     "done": sum(s.done for s in sections), "total": sum(s.total for s in sections),
                     "to_review": sum(1 for s in sections for i in s.items for x in [i] + i.children if x.can_review)})
    return _render(request, "portal/home.html", rows=rows)


@router.get("/engagements/{engagement_id}")
def portal_engagement(engagement_id: uuid.UUID, request: Request, db: Session = Depends(get_db),
                      user: User = Depends(current_client_user)):
    engagement, membership = get_client_engagement(db, user, engagement_id)
    return _render(request, "portal/engagement.html", engagement=engagement, membership=membership,
                   sections=client_portal.derive_client_view(db, engagement, viewer=user, membership=membership))


def _deliverable_context(db: Session, user: User, deliverable_id: uuid.UUID) -> dict:
    d, membership = get_client_deliverable(db, user, deliverable_id)
    sections = client_portal.derive_client_view(db, d.engagement, viewer=user, membership=membership)
    visible_children = [c for s in sections for i in s.items if i.deliverable.id == d.id for c in i.children]
    return {
        "d": d, "engagement": d.engagement, "membership": membership,
        "is_reviewer": client_portal.client_can_review(user, membership, d),
        "can_review": client_portal.can_review_now(user, membership, d),
        "reviews": client_portal.list_reviews(db, d.id),
        "comments": client_portal.list_comments(db, user, d.id),
        "children": visible_children,
    }


@router.get("/deliverables/{deliverable_id}")
def portal_deliverable(deliverable_id: uuid.UUID, request: Request, db: Session = Depends(get_db),
                       user: User = Depends(current_client_user)):
    return _render(request, "portal/deliverable.html", **_deliverable_context(db, user, deliverable_id))


@router.post("/deliverables/{deliverable_id}/comments")
def portal_comment(deliverable_id: uuid.UUID, body: str = Form(""), db: Session = Depends(get_db),
                   user: User = Depends(current_client_user)):
    client_portal.add_client_comment(db, user, deliverable_id, body=body)
    db.commit()
    return RedirectResponse(f"/portal/deliverables/{deliverable_id}#thread", status_code=303)


@router.post("/deliverables/{deliverable_id}/review")
def portal_review(deliverable_id: uuid.UUID, verdict: str = Form(""), comment: str = Form(""),
                  db: Session = Depends(get_db), user: User = Depends(current_client_user)):
    client_portal.submit_review(db, user, deliverable_id, verdict=verdict, comment=comment)
    db.commit()
    return RedirectResponse(f"/portal/deliverables/{deliverable_id}", status_code=303)
