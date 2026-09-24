import pytest

from app.errors import Forbidden, ValidationError
from app.models import EngagementMembership
from app.services import memberships as M


def test_owner_is_the_single_owner_membership(db, world):
    e = world.alice_eng
    assert M.get_engagement_owner(db, e.id) == world.alice and e.owner == world.alice
    M.assign_member(db, world.admin, e.id, user_id=world.bob.id, role="owner")
    assert M.get_engagement_owner(db, e.id) == world.bob and e.owner == world.bob
    assert db.get(EngagementMembership, (e.id, world.alice.id)).role == "collaborator"
    M.remove_member(db, world.admin, e.id, user_id=world.bob.id)
    assert M.get_engagement_owner(db, e.id) is None and e.owner is None


def test_engagements_have_no_owner_column():
    from app.models import Engagement
    assert "owner_user_id" not in Engagement.__table__.columns


def test_database_allows_only_one_owner_membership(db, world):
    import pytest
    from sqlalchemy.exc import IntegrityError
    sp = db.begin_nested()
    with pytest.raises(IntegrityError):
        db.add(EngagementMembership(engagement_id=world.alice_eng.id, user_id=world.bob.id, role="owner"))
        db.flush()
    sp.rollback()


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
