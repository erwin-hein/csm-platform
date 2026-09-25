"""Internal auth: Google OAuth restricted to the allowed domain server-side, plus the
ADMIN_EMAILS first-login bootstrap."""

from urllib.parse import parse_qs, urlsplit

import pytest

from app.errors import ValidationError
from app.services.users import get_user_by_email
from app.web import auth


@pytest.mark.parametrize("info, message", [
    ({"email": "eve@gmail.com", "email_verified": True}, "Only @shearwaterdata.com"),
    ({"email": "eve@shearwaterdata.com", "email_verified": True}, "Only @shearwaterdata.com"),  # no hd claim
    ({"email": "eve@gmail.com", "email_verified": True, "hd": "shearwaterdata.com"}, "Only @shearwaterdata.com"),
    ({"email": "eve@shearwaterdata.com", "email_verified": False, "hd": "shearwaterdata.com"}, "verified"),
    ({"email": "eve@shearwaterdata.com.evil.io", "email_verified": True, "hd": "shearwaterdata.com"}, "Only"),
])
def test_verify_rejects_non_workspace_identities(info, message):
    with pytest.raises(ValidationError, match=message):
        auth.verify_google_identity(info)


def _google_login(make_client, monkeypatch, info):
    c = make_client()
    r = c.get("/auth/google", follow_redirects=False)
    assert r.status_code == 303 and "hd=shearwaterdata.com" in r.headers["location"]
    state = parse_qs(urlsplit(r.headers["location"]).query)["state"][0]
    monkeypatch.setattr(auth, "exchange_code", lambda code, uri: {"access_token": "tok"})
    monkeypatch.setattr(auth, "fetch_userinfo", lambda tok: info)
    return c, c.get(f"/auth/google/callback?code=abc&state={state}", follow_redirects=False)


def test_first_google_login_bootstraps_admin_from_env(http, db, monkeypatch):
    c, r = _google_login(http, monkeypatch, {"email": "Boss@shearwaterdata.com", "email_verified": True,
                                             "hd": "shearwaterdata.com", "name": "The Boss"})
    assert r.status_code == 303 and r.headers["location"] == "/"
    user = get_user_by_email(db, "boss@shearwaterdata.com")
    assert user.is_admin and user.display_name == "The Boss"
    assert c.get("/team").status_code == 200


def test_first_google_login_defaults_to_analyst(http, db, monkeypatch):
    _, r = _google_login(http, monkeypatch, {"email": "newbie@shearwaterdata.com", "email_verified": True,
                                             "hd": "shearwaterdata.com"})
    assert r.status_code == 303
    newbie = get_user_by_email(db, "newbie@shearwaterdata.com")
    assert not newbie.is_admin and [r.name for r in newbie.access_roles] == ["Delivery"]


def test_outside_domain_google_login_creates_nothing(http, db, monkeypatch):
    c, r = _google_login(http, monkeypatch, {"email": "eve@gmail.com", "email_verified": True, "hd": "gmail.com"})
    assert r.headers["location"].startswith("/login?error=")
    assert get_user_by_email(db, "eve@gmail.com") is None
    assert c.get("/", follow_redirects=False).headers["location"] == "/login"


def test_callback_rejects_bad_state(http):
    c = http()
    c.get("/auth/google", follow_redirects=False)
    r = c.get("/auth/google/callback?code=abc&state=forged", follow_redirects=False)
    assert "error" in r.headers["location"]


def test_demo_login_requires_passcode_and_domain(http, db, world):
    c = http()
    assert c.post("/demo-login", data={"passcode": "wrong", "email": world.alice.email}).status_code == 401
    assert c.post("/demo-login", data={"passcode": "test-pass", "email": "x@gmail.com"}).status_code == 400
    assert get_user_by_email(db, "x@gmail.com") is None


def test_unauthenticated_requests_redirect_to_login(http):
    c = http()
    assert c.get("/", follow_redirects=False).headers["location"] == "/login"
    assert c.get("/", headers={"hx-request": "true"}).headers.get("hx-redirect") == "/login"


def test_session_cookie_is_httponly(http, world):
    c = http()
    r = c.post("/demo-login", data={"passcode": "test-pass", "email": world.alice.email}, follow_redirects=False)
    cookie = r.headers["set-cookie"].lower()
    assert "csm_session=" in cookie and "httponly" in cookie
