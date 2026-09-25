"""A Closed Won opportunity with no engagement delivering it is flagged until one is created
from it or linked to it. The bridge is optional and only for won deals on the same client."""

import pytest

from app.errors import ValidationError
from app.services import engagements as E
from app.services import opportunities as O
from tests.conftest import login


def _won(db, world, crm):
    o = O.create_opportunity(db, crm.rep, client_id=world.client.id, name="Acme QS 2", stage_key="negotiation",
                             close_date="2030-01-01", product_id=crm.qs.id)
    O.change_opportunity_stage(db, crm.rep, o.id, stage_key="closed_won")
    return o


def test_flag_until_an_engagement_is_created_from_it(http, db, world, crm):
    o = _won(db, world, crm)
    assert [x.id for x in O.list_won_without_engagement(db, crm.rep)] == [o.id]
    assert "no engagement yet" in login(http, crm.rep.email).get("/pipeline").text
    admin = login(http, world.admin.email)
    assert "Acme QS 2" in admin.get("/").text                              # flagged on the portfolio too
    form = admin.get(f"/engagements/new?opportunity_id={o.id}").text
    assert 'value="quickstart" required checked' in form                    # type suggested by the product
    r = admin.post("/engagements", data={"client_id": str(world.client.id), "type_key": "quickstart",
                                         "name": "Acme QS 2", "opportunity_id": str(o.id)}, follow_redirects=False)
    assert r.status_code == 303
    db.expire_all()
    assert O.list_won_without_engagement(db, crm.rep) == []
    assert o.engagements[0].name == "Acme QS 2"


def test_linking_an_existing_engagement_clears_it(db, world, crm):
    o = _won(db, world, crm)
    E.link_opportunity(db, world.admin, world.bob_eng.id, opportunity_id=o.id)
    assert O.list_won_without_engagement(db, crm.rep) == []


def test_bridge_rules(db, world, crm):
    o = _won(db, world, crm)
    with pytest.raises(ValidationError, match="different client"):
        E.link_opportunity(db, world.admin, world.globex_eng.id, opportunity_id=o.id)
    O.change_opportunity_stage(db, crm.rep, o.id, stage_key="proposal")
    with pytest.raises(ValidationError, match="Closed Won"):
        E.link_opportunity(db, world.admin, world.bob_eng.id, opportunity_id=o.id)
