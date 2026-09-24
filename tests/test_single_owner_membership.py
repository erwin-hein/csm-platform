import pytest

from app.errors import Forbidden, ValidationError
from app.models import EngagementMembership
from app.services import memberships as M


def test_new_owner_demotes_previous_and_mirrors_owner_column(db, world):
    e = world.alice_eng
    assert e.owner_user_id == world.alice.id
    M.assign_member(db, world.admin, e.id, user_id=world.bob.id, role="owner")
    assert e.owner_user_id == world.bob.id
    assert db.get(EngagementMembership, (e.id, world.alice.id)).role == "collaborator"
    M.remove_member(db, world.admin, e.id, user_id=world.bob.id)
    assert e.owner_user_id is None


def test_only_ops_or_admin_manage_memberships(db, world):
    with pytest.raises(Forbidden):
        M.assign_member(db, world.alice, world.alice_eng.id, user_id=world.bob.id, role="viewer")
    M.assign_member(db, world.ops, world.alice_eng.id, user_id=world.bob.id, role="viewer")


def test_reassigning_same_role_is_rejected(db, world):
    with pytest.raises(ValidationError, match="already owner"):
        M.assign_member(db, world.admin, world.alice_eng.id, user_id=world.alice.id, role="owner")


def test_team_panel_http_roundtrip(http, db, world):
    from tests.conftest import login
    admin = login(http, world.admin.email)
    r = admin.post(f"/engagements/{world.alice_eng.id}/members", data={"user_id": str(world.bob.id),
                   "role": "collaborator"}, headers={"hx-request": "true"})
    assert r.status_code == 200 and "Bob Analyst" in r.text
    r = admin.post(f"/engagements/{world.alice_eng.id}/members/{world.bob.id}/remove", headers={"hx-request": "true"})
    assert r.status_code == 200 and "Bob Analyst" not in r.text.split("Assign someone")[0]
