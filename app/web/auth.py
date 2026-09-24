"""Internal authentication — CLAUDE.md §2 row 16 (internal half only).

Google OAuth restricted to @shearwaterdata.com, verified server-side: the `hd`
request parameter is only a UI hint, so the callback independently checks the
verified email's domain *and* the returned `hd` claim. Session = Starlette's signed
httponly cookie holding only the user id. CSRF = Origin/Referer check (see csrf.py).

The demo login (DEV_LOGIN_PASSCODE) exists only so seeded users with no real Google
account can be logged into for a demo; it's off unless that env var is set.
"""

import hmac
import secrets
import uuid
from urllib.parse import urlencode

import httpx
from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.db import get_db
from app.errors import ValidationError
from app.models import EngagementType, User
from app.services import users as user_service
from app.web.templating import render

router = APIRouter()

GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_USERINFO_URL = "https://openidconnect.googleapis.com/v1/userinfo"


class LoginRequired(Exception):
    pass


def current_user(request: Request, db: Session = Depends(get_db)) -> User:
    raw = request.session.get("user_id")
    user = None
    if raw:
        try:
            user = db.get(User, uuid.UUID(raw))
        except ValueError:
            user = None
    if user is None or user.status != "active" or user.user_type != "internal":
        request.session.clear()
        raise LoginRequired()
    request.state.user = user
    request.state.nav_types = [(t.key, t.display_name) for t in
                               db.scalars(select(EngagementType).order_by(EngagementType.display_name.desc()))]
    return user


def _start_session(request: Request, user: User) -> None:
    request.session.clear()
    request.session["user_id"] = str(user.id)


def _redirect_uri(request: Request) -> str:
    return str(request.url_for("google_callback"))


# -- Google OAuth, split out so tests can stub the network calls


def exchange_code(code: str, redirect_uri: str) -> dict:
    resp = httpx.post(GOOGLE_TOKEN_URL, data={
        "code": code, "client_id": settings.google_client_id, "client_secret": settings.google_client_secret,
        "redirect_uri": redirect_uri, "grant_type": "authorization_code",
    }, timeout=10)
    resp.raise_for_status()
    return resp.json()


def fetch_userinfo(access_token: str) -> dict:
    resp = httpx.get(GOOGLE_USERINFO_URL, headers={"Authorization": f"Bearer {access_token}"}, timeout=10)
    resp.raise_for_status()
    return resp.json()


def verify_google_identity(info: dict) -> tuple[str, str | None]:
    """Server-side enforcement of the domain restriction. Returns (email, name)."""
    email = (info.get("email") or "").strip().lower()
    if not email or not info.get("email_verified"):
        raise ValidationError("Google didn't return a verified email address")
    if info.get("hd", "").lower() != settings.allowed_domain or not user_service.is_allowed_internal_email(email):
        raise ValidationError(f"Only @{settings.allowed_domain} Google Workspace accounts can sign in")
    return email, info.get("name")


# -- routes


@router.get("/login")
def login_page(request: Request, error: str | None = None):
    return render(request, "login.html", error=error, google_enabled=settings.google_enabled,
                  dev_login_enabled=settings.dev_login_enabled)


@router.get("/auth/google")
def google_start(request: Request):
    if not settings.google_enabled:
        return RedirectResponse("/login?error=Google+sign-in+isn't+configured", status_code=303)
    state = secrets.token_urlsafe(24)
    request.session["oauth_state"] = state
    params = {
        "client_id": settings.google_client_id, "redirect_uri": _redirect_uri(request), "response_type": "code",
        "scope": "openid email profile", "state": state, "hd": settings.allowed_domain, "prompt": "select_account",
    }
    return RedirectResponse(f"{GOOGLE_AUTH_URL}?{urlencode(params)}", status_code=303)


@router.get("/auth/google/callback", name="google_callback")
def google_callback(request: Request, code: str = "", state: str = "", error: str = "",
                    db: Session = Depends(get_db)):
    expected = request.session.pop("oauth_state", None)
    if error or not code or not expected or not hmac.compare_digest(expected, state):
        return RedirectResponse("/login?error=Sign-in+was+cancelled+or+expired", status_code=303)
    try:
        tokens = exchange_code(code, _redirect_uri(request))
        email, name = verify_google_identity(fetch_userinfo(tokens["access_token"]))
        user = user_service.login_or_bootstrap(db, email=email, display_name=name)
    except ValidationError as e:
        return RedirectResponse("/login?" + urlencode({"error": str(e)}), status_code=303)
    except (httpx.HTTPError, KeyError):
        return RedirectResponse("/login?error=Couldn't+reach+Google+—+try+again", status_code=303)
    db.commit()
    _start_session(request, user)
    return RedirectResponse("/", status_code=303)


@router.get("/demo-login")
def demo_login_page(request: Request, db: Session = Depends(get_db)):
    if not settings.dev_login_enabled:
        return RedirectResponse("/login", status_code=303)
    return render(request, "demo_login.html", users=user_service.list_internal_users(db), error=None)


@router.post("/demo-login")
def demo_login(request: Request, passcode: str = Form(""), email: str = Form(""), db: Session = Depends(get_db)):
    if not settings.dev_login_enabled:
        return RedirectResponse("/login", status_code=303)
    if not hmac.compare_digest(passcode.encode(), settings.dev_login_passcode.encode()):
        return render(request, "demo_login.html", users=user_service.list_internal_users(db),
                      error="Wrong demo passcode", status_code=401)
    try:
        # Same path as Google login, so ADMIN_EMAILS bootstrap and the domain rule apply identically.
        if not user_service.is_allowed_internal_email(email):
            raise ValidationError(f"Only @{settings.allowed_domain} accounts can sign in")
        user = user_service.login_or_bootstrap(db, email=email, display_name=None)
    except ValidationError as e:
        return render(request, "demo_login.html", users=user_service.list_internal_users(db), error=str(e),
                      status_code=400)
    db.commit()
    _start_session(request, user)
    return RedirectResponse("/", status_code=303)


@router.post("/logout")
def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/login", status_code=303)
