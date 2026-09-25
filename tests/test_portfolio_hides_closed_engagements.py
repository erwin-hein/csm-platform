from app.services import engagements
from tests.conftest import login


def test_completed_engagements_hidden_by_default(http, db, world):
    engagements.change_status(db, world.admin, world.globex_eng.id, status="complete")
    db.flush()
    admin = login(http, world.admin.email)
    page = admin.get("/").text
    assert "Globex QuickStart" not in page and "1 completed/cancelled engagement hidden" in page
    assert "Globex QuickStart" in admin.get("/?show_closed=true").text
