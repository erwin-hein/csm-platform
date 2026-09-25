"""An opportunity's amount is always the sum of its line items; there is no stored amount."""

from decimal import Decimal

from app.services import opportunities as O


def test_amount_follows_line_items(db, world, crm):
    o = O.create_opportunity(db, crm.rep, client_id=world.client.id, name="Deal", stage_key="proposal",
                             close_date="2030-01-01", product_id=crm.qs.id)
    assert o.amount == Decimal("35000")                         # product default price
    li = O.add_line_item(db, crm.rep, o.id, product_id=crm.hours.id, quantity=10)
    assert o.amount == Decimal("37000")
    O.update_line_item(db, crm.rep, li.id, quantity=20, unit_price="250")
    assert o.amount == Decimal("40000")
    assert o.weighted_amount == Decimal("24000")                 # proposal: 60%
    O.update_opportunity(db, crm.rep, o.id, probability=50)
    assert o.weighted_amount == Decimal("20000")


def test_editing_a_product_never_reprices_a_deal(db, world, crm):
    o = O.create_opportunity(db, crm.rep, client_id=world.client.id, name="Deal", stage_key="proposal",
                             close_date="2030-01-01", product_id=crm.qs.id)
    O.update_product(db, world.admin, crm.qs.id, name="QuickStart", pricing_model="fixed_bid",
                     default_unit_price="50000", engagement_type_key="quickstart")
    db.expire(o, ["line_items"])
    assert o.amount == Decimal("35000")
