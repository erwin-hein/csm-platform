"""opportunities:use reads every opportunity but edits only its own; manage edits and
reassigns any. Products are maintained with manage."""

import pytest

from app.errors import Forbidden, ValidationError
from app.services import opportunities as O
from tests.conftest import login


def _deal(db, world, owner, product):
    return O.create_opportunity(db, owner, client_id=world.client.id, name=f"{owner.label} deal",
                                stage_key="discovery", close_date="2030-01-01", product_id=product.id)


def test_use_reads_all_edits_own(http, db, world, crm):
    theirs = _deal(db, world, crm.hybrid, crm.qs)
    mine = _deal(db, world, crm.rep, crm.qs)
    assert {o.id for o in O.list_visible_opportunities(db, crm.rep)} == {theirs.id, mine.id}
    O.update_opportunity(db, crm.rep, mine.id, next_step="Call")
    with pytest.raises(Forbidden):
        O.update_opportunity(db, crm.rep, theirs.id, next_step="Hijack")
    with pytest.raises(Forbidden):
        O.change_opportunity_stage(db, crm.rep, theirs.id, stage_key="proposal")
    rep = login(http, crm.rep.email)
    assert rep.post(f"/opportunities/{theirs.id}/edit", data={"next_step": "x"}, follow_redirects=False).status_code == 403


def test_use_cannot_reassign_or_create_for_others(db, world, crm):
    mine = _deal(db, world, crm.rep, crm.qs)
    with pytest.raises(ValidationError):
        O.update_opportunity(db, crm.rep, mine.id, owner_user_id=crm.hybrid.id)
    with pytest.raises(ValidationError):
        O.create_opportunity(db, crm.rep, client_id=world.client.id, name="X", stage_key="discovery",
                             close_date="2030-01-01", product_id=crm.qs.id, owner_user_id=crm.hybrid.id)


def test_manage_edits_and_reassigns_any(db, world, crm):
    o = _deal(db, world, crm.rep, crm.qs)
    O.update_opportunity(db, crm.lead, o.id, owner_user_id=crm.hybrid.id)
    assert o.owner_user_id == crm.hybrid.id
    with pytest.raises(ValidationError, match="access to opportunities"):
        O.update_opportunity(db, crm.lead, o.id, owner_user_id=world.alice.id)  # Delivery only


def test_products_need_manage(db, world, crm):
    with pytest.raises(Forbidden):
        O.create_product(db, crm.rep, name="Freebie", pricing_model="fixed_bid")
    O.create_product(db, crm.lead, name="Health check", pricing_model="fixed_bid", default_unit_price="5000")
