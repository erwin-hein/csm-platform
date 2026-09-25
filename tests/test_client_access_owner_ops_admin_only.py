"""Client access is provisioned per engagement by admin, ops or the engagement's owner, not
by collaborators, viewers or contractors (CLAUDE.md §3 Portal)."""

import pytest

from app.errors import Forbidden
from app.services import client_portal, clients, memberships, users
from tests.conftest import login


@pytest.fixture
def contact(db, world):
    return clients.add_contact(db, world.admin, world.client.id, name="Cara", email="cara@acme.com")


def test_owner_ops_and_admin_can_invite(db, world, contact):
    m = client_portal.invite_client_contact(db, world.alice, world.alice_eng.id, contact_id=contact.id)
    client_portal.set_client_scope(db, world.ops, world.alice_eng.id, user_id=m.user_id, viewer_scope="assigned_only")
    client_portal.revoke_client_access(db, world.admin, world.alice_eng.id, user_id=m.user_id)


def test_collaborators_viewers_and_contractors_cannot(db, world, contact):
    carl = users.create_internal_user(db, None, email="carl@shearwaterdata.com", display_name="Carl",
                                  roles=["Delivery"], is_contractor=True)
    other_eng = world.bob_eng
    memberships.assign_member(db, world.admin, world.alice_eng.id, user_id=world.bob.id, role="collaborator")
    memberships.assign_member(db, world.admin, other_eng.id, user_id=carl.id, role="owner")  # contractor owner
    with pytest.raises(Forbidden):
        client_portal.invite_client_contact(db, world.bob, world.alice_eng.id, contact_id=contact.id)
    assert not client_portal.can_manage_client_access(db, carl, other_eng.id)


def test_http_collaborator_sees_panel_read_only_and_gets_403(http, db, world, contact):
    memberships.assign_member(db, world.admin, world.alice_eng.id, user_id=world.bob.id, role="collaborator")
    db.flush()
    bob = login(http, world.bob.email)
    page = bob.get(f"/engagements/{world.alice_eng.id}").text
    assert "Client access" in page and "Invite a client contact" not in page
    r = bob.post(f"/engagements/{world.alice_eng.id}/client-access", data={"contact_id": str(contact.id)},
                 follow_redirects=False)
    assert r.status_code == 403
    owner_page = login(http, world.alice.email).get(f"/engagements/{world.alice_eng.id}").text
    assert "Invite a client contact" in owner_page


def test_team_screens_dont_carry_client_access(http, world):
    admin = login(http, world.admin.email)
    for path in ("/team", "/team/engagements"):
        assert "Client access" not in admin.get(path).text
