"""Access administration (admin only): access roles, their per-module grants, and who holds
which role (CLAUDE.md §3 Identity & access)."""

import uuid

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from app.access import require_admin
from app.db import get_db
from app.errors import ValidationError
from app.models import ACCESS_LEVELS, User
from app.services import access_roles as access_service
from app.services import users as user_service
from app.web.auth import current_user
from app.web.templating import is_htmx, render

router = APIRouter(prefix="/admin/access")


def _context(db: Session) -> dict:
    roles = access_service.list_access_roles(db)
    people = user_service.list_internal_users(db)
    return {"modules": access_service.list_modules(db), "roles": roles, "people": people, "levels": ACCESS_LEVELS,
            "role_counts": {r.id: sum(1 for u in people if r in u.access_roles) for r in roles}}


def _respond(request: Request, db: Session, fragment: str):
    if is_htmx(request):
        return render(request, f"admin/_{fragment}.html", **_context(db))
    return RedirectResponse("/admin/access", status_code=303)


@router.get("")
def access_page(request: Request, db: Session = Depends(get_db), user: User = Depends(current_user)):
    require_admin(user)
    return render(request, "admin/access.html", nav="access", **_context(db))


@router.post("/roles")
def role_create(name: str = Form(""), description: str = Form(""), db: Session = Depends(get_db),
                user: User = Depends(current_user)):
    access_service.create_access_role(db, user, name=name, description=description)
    db.commit()
    return RedirectResponse("/admin/access", status_code=303)


@router.post("/roles/{role_id}")
def role_update(role_id: uuid.UUID, request: Request, is_default: bool = Form(False),
                db: Session = Depends(get_db), user: User = Depends(current_user)):
    access_service.update_access_role(db, user, role_id, is_default=is_default)
    db.commit()
    return _respond(request, db, "roles")


@router.post("/roles/{role_id}/grants")
def role_grant(role_id: uuid.UUID, request: Request, module_key: str = Form(""), level: str = Form(""),
               db: Session = Depends(get_db), user: User = Depends(current_user)):
    access_service.set_role_grant(db, user, role_id, module_key=module_key, level=level or None)
    db.commit()
    return _respond(request, db, "roles")


@router.post("/users/{user_id}/roles")
async def user_roles(user_id: uuid.UUID, request: Request, db: Session = Depends(get_db),
                     user: User = Depends(current_user)):
    form = await request.form()
    try:
        role_ids = [uuid.UUID(v) for v in form.getlist("role_ids")]
    except ValueError:
        raise ValidationError("Invalid role")
    access_service.set_user_roles(db, user, user_id, role_ids=role_ids)
    db.commit()
    return _respond(request, db, "people")


@router.post("/users/{user_id}/flags")
async def user_flags(user_id: uuid.UUID, request: Request, db: Session = Depends(get_db),
                     user: User = Depends(current_user)):
    form = await request.form()
    access_service.set_user_flags(db, user, user_id, is_admin="is_admin" in form,
                                  is_contractor="is_contractor" in form)
    db.commit()
    return _respond(request, db, "people")
