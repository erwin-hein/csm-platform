"""Shared test Postgres, each test wrapped in a rolled-back transaction (CLAUDE.md §3
Testing strategy). Point TEST_DATABASE_URL at a throwaway database — it is
downgraded/upgraded from scratch once per run."""

import os

TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL", "postgresql+psycopg://postgres@127.0.0.1:5432/csm_test")
# Settings are read at import time, so configure the environment before importing app.*
os.environ["DATABASE_URL"] = TEST_DATABASE_URL
os.environ["ADMIN_EMAILS"] = "boss@shearwaterdata.com"
os.environ["DEV_LOGIN_PASSCODE"] = "test-pass"
os.environ["GOOGLE_CLIENT_ID"] = "test-client-id"
os.environ["GOOGLE_CLIENT_SECRET"] = "test-client-secret"
os.environ["SESSION_HTTPS_ONLY"] = "false"

import pytest  # noqa: E402
from alembic import command  # noqa: E402
from alembic.config import Config  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from app.db import get_db  # noqa: E402
from app.main import app  # noqa: E402
from app.services import clients, engagements, memberships, users  # noqa: E402

ORIGIN = "http://testserver"


@pytest.fixture(scope="session")
def engine():
    cfg = Config(os.path.join(os.path.dirname(__file__), "..", "alembic.ini"))
    cfg.set_main_option("sqlalchemy.url", TEST_DATABASE_URL)
    command.downgrade(cfg, "base")
    command.upgrade(cfg, "head")
    eng = create_engine(TEST_DATABASE_URL)
    yield eng
    eng.dispose()


@pytest.fixture
def db(engine):
    connection = engine.connect()
    outer = connection.begin()
    session = Session(bind=connection, join_transaction_mode="create_savepoint", expire_on_commit=False)
    try:
        yield session
    finally:
        session.close()
        outer.rollback()
        connection.close()


@pytest.fixture
def http(db):
    app.dependency_overrides[get_db] = lambda: db
    try:
        yield lambda: TestClient(app, headers={"origin": ORIGIN})
    finally:
        app.dependency_overrides.clear()


def login(make_client, email: str) -> TestClient:
    c = make_client()
    r = c.post("/demo-login", data={"passcode": "test-pass", "email": email}, follow_redirects=False)
    assert r.status_code == 303, r.text
    return c


@pytest.fixture
def world(db):
    """Two analysts, an admin, and two engagements on the same client — one each."""
    admin = users.create_internal_user(db, None, email="admin@shearwaterdata.com", display_name="Ada Admin",
                                       role="admin")
    alice = users.create_internal_user(db, None, email="alice@shearwaterdata.com", display_name="Alice Analyst",
                                       role="analyst")
    bob = users.create_internal_user(db, None, email="bob@shearwaterdata.com", display_name="Bob Analyst",
                                     role="analyst")
    ops = users.create_internal_user(db, None, email="olu@shearwaterdata.com", display_name="Olu Ops", role="ops")
    client = clients.create_client(db, admin, name="Acme Corp", domains="acme.com")
    other_client = clients.create_client(db, admin, name="Globex", domains="globex.com")
    alice_eng = engagements.create_engagement(db, admin, client_id=client.id, type_key="migration",
                                              name="Acme Migration")
    bob_eng = engagements.create_engagement(db, admin, client_id=client.id, type_key="quickstart",
                                            name="Acme QuickStart")
    globex_eng = engagements.create_engagement(db, admin, client_id=other_client.id, type_key="quickstart",
                                               name="Globex QuickStart")
    memberships.assign_member(db, admin, alice_eng.id, user_id=alice.id, role="owner")
    memberships.assign_member(db, admin, bob_eng.id, user_id=bob.id, role="owner")
    memberships.assign_member(db, admin, globex_eng.id, user_id=bob.id, role="owner")
    db.flush()
    return type("World", (), dict(admin=admin, alice=alice, bob=bob, ops=ops, client=client,
                                  other_client=other_client, alice_eng=alice_eng, bob_eng=bob_eng,
                                  globex_eng=globex_eng))
