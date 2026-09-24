"""An analyst without membership can't see or act on an engagement — through the
services and over HTTP — even when it's on a client they *can* see."""

import pytest
from sqlalchemy import func, select

from app.errors import Forbidden, NotFound
from app.models import Deliverable, Event
from app.services import clients, deliverables, engagements, memberships
from tests.conftest import login


@pytest.fixture
def setup(db, world):
    phase = deliverables.create_deliverable(db, world.admin, world.alice_eng.id, kind="phase", name="Secret phase")
    milestone = deliverables.create_deliverable(db, world.admin, world.alice_eng.id, kind="milestone",
                                                name="Secret milestone", parent_id=phase.id)
    module = deliverables.create_deliverable(db, world.admin, world.bob_eng.id, kind="module", name="Bob's module")
    db.flush()
    return world, phase, milestone, module


def _event_count(db) -> int:
    return db.scalar(select(func.count()).select_from(Event))


def test_service_reads_exclude_non_member_engagements(db, setup):
    w, phase, _, _ = setup
    visible = {e.id for e in engagements.list_visible_engagements(db, w.bob)}
    assert w.alice_eng.id not in visible
    assert visible == {w.bob_eng.id, w.globex_eng.id}
    # Bob sees Acme (via his QuickStart) but that doesn't leak Alice's Acme engagement.
    assert {c.id for c in clients.list_visible_clients(db, w.bob)} == {w.client.id, w.other_client.id}
    assert {c.id for c in clients.list_visible_clients(db, w.alice)} == {w.client.id}


def test_service_writes_refuse_non_member(db, setup):
    w, phase, milestone, _ = setup
    before = _event_count(db)
    with pytest.raises(NotFound):
        deliverables.create_deliverable(db, w.bob, w.alice_eng.id, kind="phase", name="Sneaky")
    with pytest.raises(NotFound):
        deliverables.update_deliverable(db, w.bob, milestone.id, pipeline_status="done")
    with pytest.raises(NotFound):
        deliverables.add_note(db, w.bob, milestone.id, body="hi")
    with pytest.raises(NotFound):
        deliverables.add_blocker(db, w.bob, milestone.id, blocker_id=phase.id)
    with pytest.raises(NotFound):
        engagements.change_stage(db, w.bob, w.alice_eng.id, stage="access_setup")
    assert _event_count(db) == before
    assert db.get(Deliverable, milestone.id).pipeline_status == "not_started"


def test_http_non_member_gets_404_everywhere(http, db, setup):
    w, phase, milestone, _ = setup
    bob = login(http, w.bob.email)
    eid = w.alice_eng.id

    for path in (f"/engagements/{eid}", f"/deliverables/{milestone.id}", f"/deliverables/{phase.id}",
                 f"/engagements/{eid}/deliverables/new", f"/engagements/{eid}/deliverables-panel",
                 f"/deliverables/{milestone.id}/chat"):
        assert bob.get(path).status_code == 404, path

    before = _event_count(db)
    posts = [
        (f"/engagements/{eid}/stage", {"stage": "access_setup"}),
        (f"/engagements/{eid}/status", {"status": "paused"}),
        (f"/engagements/{eid}/deliverables", {"kind": "phase", "name": "Sneaky"}),
        (f"/deliverables/{milestone.id}/pipeline", {"pipeline_status": "done"}),
        (f"/deliverables/{milestone.id}/edit", {"name": "Renamed"}),
        (f"/deliverables/{milestone.id}/block", {"blocked": "true", "blocked_reason": "x"}),
        (f"/deliverables/{milestone.id}/notes", {"body": "hello"}),
        (f"/deliverables/{milestone.id}/chat", {"body": "hello"}),
        (f"/deliverables/{milestone.id}/blockers", {"blocker_id": str(phase.id)}),
    ]
    for path, data in posts:
        assert bob.post(path, data=data, follow_redirects=False).status_code == 404, path
    assert _event_count(db) == before


def test_http_lists_hide_non_member_engagements(http, setup):
    w, *_ = setup
    bob = login(http, w.bob.email)
    for path in ("/", "/board/migration", "/board/quickstart", f"/clients/{w.client.id}"):
        body = bob.get(path).text
        assert "Acme Migration" not in body, path
    assert "Acme QuickStart" in bob.get(f"/clients/{w.client.id}").text


def test_client_without_any_membership_is_invisible(http, db, setup):
    w, *_ = setup
    alice = login(http, w.alice.email)
    assert alice.get(f"/clients/{w.other_client.id}").status_code == 404
    assert "Globex" not in alice.get("/").text


def test_viewer_can_see_but_not_edit(http, db, setup):
    w, phase, milestone, _ = setup
    memberships.assign_member(db, w.admin, w.alice_eng.id, user_id=w.bob.id, role="viewer")
    db.flush()
    bob = login(http, w.bob.email)
    assert bob.get(f"/engagements/{w.alice_eng.id}").status_code == 200
    assert bob.get(f"/deliverables/{milestone.id}").status_code == 200
    r = bob.post(f"/deliverables/{milestone.id}/pipeline", data={"pipeline_status": "done"}, follow_redirects=False)
    assert r.status_code == 403
    with pytest.raises(Forbidden):
        deliverables.update_deliverable(db, w.bob, milestone.id, pipeline_status="done")
    # ...but commenting only needs visibility, so a viewer can use the chat pop-up.
    r = bob.post(f"/deliverables/{milestone.id}/chat", data={"body": "viewer note"}, headers={"hx-request": "true"})
    assert r.status_code == 200 and "viewer note" in r.text and r.headers["hx-trigger"] == "notesChanged"


def test_membership_revocation_removes_access(http, db, setup):
    w, *_ = setup
    memberships.remove_member(db, w.admin, w.alice_eng.id, user_id=w.alice.id)
    db.flush()
    alice = login(http, w.alice.email)
    assert alice.get(f"/engagements/{w.alice_eng.id}").status_code == 404


def test_member_can_act(http, db, setup):
    w, _, milestone, _ = setup
    alice = login(http, w.alice.email)
    r = alice.post(f"/deliverables/{milestone.id}/pipeline", data={"pipeline_status": "in_progress"},
                   follow_redirects=False)
    assert r.status_code == 303
    db.refresh(milestone)
    assert milestone.pipeline_status == "in_progress"
