"""The CRM pages render and the pipeline's inline stage move works over htmx."""

from datetime import date

from app.services import opportunities as O
from tests.conftest import login


def test_pages(http, db, world, crm):
    o = O.create_opportunity(db, crm.rep, client_id=world.client.id, name="Acme expansion", stage_key="discovery",
                             close_date=date.today(), product_id=crm.qs.id)
    rep = login(http, crm.rep.email)
    board = rep.get("/pipeline").text
    assert "Acme expansion" in board and "$35,000" in board
    later = rep.get("/forecast?period=month&offset=1").text   # the deal closes this month
    assert "Acme expansion" in rep.get("/forecast?period=month&offset=0").text.split("Recent movement")[0]
    assert "Acme expansion" not in later.split("Recent movement")[0]
    assert "Acme expansion" in rep.get(f"/opportunities/{o.id}").text
    assert rep.get("/products").status_code == 200
    assert rep.get("/clients").status_code == 200
    r = rep.post(f"/opportunities/{o.id}/stage", data={"stage_key": "proposal", "panel": "pipeline"},
                 headers={"hx-request": "true"})
    assert r.status_code == 200 and 'id="pipeline-board"' in r.text
    db.expire_all()
    assert o.stage_key == "proposal"
    r = rep.post(f"/opportunities/{o.id}/stage", data={"stage_key": "closed_lost", "panel": "pipeline"},
                 headers={"hx-request": "true"})
    assert "why" in r.text.lower() and r.headers.get("HX-Retarget") == "#flash"
