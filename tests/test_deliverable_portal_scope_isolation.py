"""A scoped client user can't see past what they own; no client user ever sees internal-only
kinds or internal pages (CLAUDE.md §3 Testing strategy: *_portal_scope_isolation)."""

import pytest

from app.errors import NotFound
from app.services import client_portal
from tests.conftest import login


def names(sections):
    return {x.deliverable.name for s in sections for i in s.items for x in [i] + i.children}


def test_lead_sees_every_client_visible_item_but_no_internal_kinds(db, portal):
    m = client_portal.list_client_engagements(db, portal.lead)[0][1]
    seen = names(client_portal.derive_client_view(db, portal.eng, viewer=portal.lead, membership=m))
    assert seen == {"Sales dashboard", "QA tile", "Lead-only tile", "Other dashboard"}


def test_scoped_user_sees_only_owned_items_plus_parent_for_context(db, portal):
    m = client_portal.list_client_engagements(db, portal.qa)[0][1]
    assert names(client_portal.derive_client_view(db, portal.eng, viewer=portal.qa, membership=m)) == {
        "Sales dashboard", "QA tile"}


def test_http_scoped_user_gets_404_past_their_scope(http, portal):
    qa = login(http, portal.qa.email)
    assert qa.get(f"/portal/deliverables/{portal.qa_tile.id}").status_code == 200
    assert qa.get(f"/portal/deliverables/{portal.dash.id}").status_code == 200   # parent, for context
    for d in (portal.other_tile, portal.other_dash, portal.phase):
        assert qa.get(f"/portal/deliverables/{d.id}").status_code == 404
        assert qa.post(f"/portal/deliverables/{d.id}/comments", data={"body": "hi"},
                       follow_redirects=False).status_code == 404
    page = qa.get(f"/portal/engagements/{portal.eng.id}").text
    assert "QA tile" in page and "Lead-only tile" not in page and "Internal phase" not in page


def test_client_users_never_reach_internal_pages(http, world, portal):
    lead = login(http, portal.lead.email)
    for path in ("/", f"/engagements/{portal.eng.id}", f"/deliverables/{portal.dash.id}",
                 f"/deliverables/{portal.dash.id}/chat", "/team", "/events", f"/clients/{world.client.id}"):
        r = lead.get(path, follow_redirects=False)
        assert r.status_code == 303 and r.headers["location"] == "/portal", path
    r = lead.post(f"/deliverables/{portal.dash.id}/chat", data={"body": "x"}, follow_redirects=False)
    assert r.headers["location"] == "/portal"


def test_internal_users_dont_get_portal_pages(http, world, portal):
    admin = login(http, world.admin.email)
    assert admin.get(f"/portal/engagements/{portal.eng.id}", follow_redirects=False).headers["location"] == "/"


def test_client_user_of_one_engagement_cant_see_another(http, db, world, portal):
    lead = login(http, portal.lead.email)
    assert lead.get(f"/portal/engagements/{world.bob_eng.id}").status_code == 404
    with pytest.raises(NotFound):
        client_portal.add_client_comment(db, portal.lead, portal.phase.id, body="internal kind")


def test_revoking_access_removes_the_portal_view(http, db, world, portal):
    client_portal.revoke_client_access(db, world.admin, portal.eng.id, user_id=portal.lead.id)
    db.flush()
    assert login(http, portal.lead.email).get(f"/portal/engagements/{portal.eng.id}").status_code == 404
