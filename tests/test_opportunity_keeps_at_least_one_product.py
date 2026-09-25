"""Every opportunity has at least one product: it's created with one, and the last one can't be removed."""

import pytest

from app.errors import ValidationError
from app.services import opportunities as O


def test_needs_a_product_to_exist(db, world, crm):
    with pytest.raises(ValidationError):
        O.create_opportunity(db, crm.rep, client_id=world.client.id, name="Deal", stage_key="discovery",
                             close_date="2030-01-01", product_id=None)


def test_last_product_cannot_be_removed(db, world, crm):
    o = O.create_opportunity(db, crm.rep, client_id=world.client.id, name="Deal", stage_key="discovery",
                             close_date="2030-01-01", product_id=crm.qs.id)
    with pytest.raises(ValidationError, match="at least one product"):
        O.remove_line_item(db, crm.rep, o.line_items[0].id)
    extra = O.add_line_item(db, crm.rep, o.id, product_id=crm.hours.id, quantity=5)
    O.remove_line_item(db, crm.rep, o.line_items[0].id)
    assert [li.id for li in o.line_items] == [extra.id]
