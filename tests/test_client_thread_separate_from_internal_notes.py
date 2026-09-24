"""The client thread and internal notes are two tables, never one with a flag: nothing
posted internally can surface in the portal, and comments are escaped both ways."""

from app.services import client_portal, deliverables
from tests.conftest import login


def test_internal_notes_never_reach_the_portal(http, db, world, portal):
    deliverables.add_note(db, world.alice, portal.qa_tile.id, body="INTERNAL: client is slow to respond")
    client_portal.add_client_comment(db, world.alice, portal.qa_tile.id, body="Shared for your review")
    db.flush()
    page = login(http, portal.qa.email).get(f"/portal/deliverables/{portal.qa_tile.id}").text
    assert "Shared for your review" in page and "INTERNAL" not in page
    internal = login(http, world.alice.email)
    notes = internal.get(f"/deliverables/{portal.qa_tile.id}/chat?channel=internal").text
    thread = internal.get(f"/deliverables/{portal.qa_tile.id}/chat?channel=client").text
    assert "INTERNAL" in notes and "Shared for your review" not in notes
    assert "Shared for your review" in thread and "INTERNAL" not in thread and "Visible to the client" in thread


def test_internal_only_kinds_have_no_client_thread(http, world, portal):
    page = login(http, world.alice.email).get(f"/deliverables/{portal.phase.id}/chat?channel=client").text
    assert "Client thread" not in page and "Internal only" in page


def test_comments_are_escaped_in_both_directions(http, db, world, portal):
    payload = '<script>alert("x")</script>'
    client_portal.add_client_comment(db, portal.qa, portal.qa_tile.id, body=payload)
    client_portal.add_client_comment(db, world.alice, portal.qa_tile.id, body=payload)
    db.flush()
    for page in (login(http, world.alice.email).get(f"/deliverables/{portal.qa_tile.id}").text,
                 login(http, portal.qa.email).get(f"/portal/deliverables/{portal.qa_tile.id}").text):
        assert payload not in page and "&lt;script&gt;" in page


def test_client_owner_must_be_a_client_user_on_the_engagement(db, world, portal):
    import pytest

    from app.errors import ValidationError
    with pytest.raises(ValidationError, match="client user"):
        deliverables.update_deliverable(db, world.admin, portal.dash.id, client_owner_user_id=world.alice.id)
    with pytest.raises(ValidationError, match="can't see this kind"):
        deliverables.update_deliverable(db, world.admin, portal.phase.id, client_owner_user_id=portal.qa.id)
