"""User-editable names (products, opportunities, engagements) are never interpolated into inline
JavaScript on the CRM pages: confirmations go through hx-confirm, which is plain text."""

from app.services import opportunities as O
from tests.conftest import login

NASTY = "x');alert(document.cookie);('"


def test_hostile_names_stay_inert(http, db, world, crm):
    p = O.create_product(db, world.admin, name=NASTY, pricing_model="fixed_bid", default_unit_price="10")
    o = O.create_opportunity(db, crm.rep, client_id=world.client.id, name=NASTY, stage_key="discovery",
                             close_date="2030-01-01", product_id=p.id)
    O.add_line_item(db, crm.rep, o.id, product_id=crm.qs.id)
    rep = login(http, crm.rep.email)
    for page in (rep.get(f"/opportunities/{o.id}").text, rep.get("/pipeline").text, rep.get("/products").text):
        assert "alert(document.cookie)" not in page.replace("alert(document.cookie);(&#39;", "")
        assert "');alert" not in page
