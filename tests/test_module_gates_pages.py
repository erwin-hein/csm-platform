"""A module the user has no access to doesn't exist for them: its pages and records 404,
and navigation doesn't offer it. Every combination of modules works."""

from app.services import access_roles, memberships, opportunities
from tests.conftest import login


def _opp(db, world, crm):
    return opportunities.create_opportunity(db, crm.rep, client_id=world.client.id, name="Acme deal",
                                            stage_key="discovery", close_date="2030-01-01", product_id=crm.qs.id)


def test_sales_only_user_sees_no_engagements(http, db, world, crm):
    rep = login(http, crm.rep.email)
    r = rep.get("/", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/pipeline"
    assert rep.get("/board/quickstart").status_code == 404
    assert rep.get(f"/engagements/{world.alice_eng.id}").status_code == 404
    page = rep.get("/pipeline").text
    assert "Pipeline" in page and "Migration board" not in page and 'href="/team"' not in page
    # Clients are shared: a rep sees them all (to find prospects) but not their engagements.
    client_page = rep.get(f"/clients/{world.client.id}").text
    assert "Acme Corp" in client_page and "Acme Migration" not in client_page


def test_delivery_only_user_sees_no_opportunities(http, db, world, crm):
    o = _opp(db, world, crm)
    alice = login(http, world.alice.email)
    assert alice.get("/pipeline").status_code == 404
    assert alice.get("/forecast").status_code == 404
    assert alice.get(f"/opportunities/{o.id}").status_code == 404
    assert "Acme deal" not in alice.get(f"/clients/{world.client.id}").text


def test_hybrid_user_gets_both(http, db, world, crm):
    memberships.assign_member(db, world.admin, world.alice_eng.id, user_id=crm.hybrid.id, role="collaborator")
    o = _opp(db, world, crm)
    h = login(http, crm.hybrid.email)
    assert h.get(f"/engagements/{world.alice_eng.id}").status_code == 200
    assert h.get(f"/opportunities/{o.id}").status_code == 200
    assert h.get(f"/engagements/{world.bob_eng.id}").status_code == 404  # still membership-scoped


def test_team_manage_alone_allocates(http, db, world):
    resourcing = next(r for r in access_roles.list_access_roles(db) if r.name == "Resourcing")
    access_roles.set_user_roles(db, world.admin, world.bob.id, role_ids=[resourcing.id])
    bob = login(http, world.bob.email)
    assert bob.get("/team").status_code == 200
    r = bob.post(f"/team/people/{world.alice.id}/allocate", headers={"hx-request": "true"},
                 data={"engagement_id": str(world.globex_eng.id), "role": "viewer"})
    assert r.status_code == 200 and "Globex QuickStart" in r.text
    assert bob.get(f"/engagements/{world.globex_eng.id}").status_code == 404  # no engagements module


def test_user_without_any_module_gets_the_no_access_page(http, db, world):
    access_roles.set_user_roles(db, world.admin, world.bob.id, role_ids=[])
    page = login(http, world.bob.email).get("/").text
    assert "No access yet" in page
