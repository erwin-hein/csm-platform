from app.services import clients, deliverables, engagements
from tests.conftest import login


def test_admin_and_ops_see_everything_without_membership(db, world):
    all_ids = {world.alice_eng.id, world.bob_eng.id, world.globex_eng.id}
    for user in (world.admin, world.ops):
        assert {e.id for e in engagements.list_visible_engagements(db, user)} == all_ids
        assert len(clients.list_visible_clients(db, user)) == 2


def test_admin_can_act_without_membership(db, world):
    d = deliverables.create_deliverable(db, world.ops, world.alice_eng.id, kind="phase", name="Ops-created", stage_key="scoping")
    assert d.engagement_id == world.alice_eng.id


def test_admin_pages(http, world):
    admin = login(http, world.admin.email)
    body = admin.get("/").text
    for name in ("Acme Migration", "Acme QuickStart", "Globex QuickStart"):
        assert name in body
    assert admin.get("/team").status_code == 200
    assert admin.get("/events").status_code == 200


def test_analyst_cannot_reach_admin_screens(http, world):
    alice = login(http, world.alice.email)
    assert alice.get("/team").status_code == 403
    assert alice.get("/events").status_code == 403
    assert alice.get("/clients/new").status_code == 403
    assert alice.post("/clients", data={"name": "Nope"}, follow_redirects=False).status_code == 403
    r = alice.post(f"/engagements/{world.alice_eng.id}/members", data={"user_id": str(world.bob.id), "role": "viewer"},
                   follow_redirects=False)
    assert r.status_code == 403
