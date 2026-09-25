from datetime import date, timedelta

import pytest

from app.errors import Forbidden
from app.services import capacity, deliverables, engagements
from tests.conftest import login


def test_capacity_counts_open_work_per_person(db, world):
    today = date(2026, 9, 24)
    p = deliverables.create_deliverable(db, world.admin, world.alice_eng.id, kind="phase", name="P", stage_key="scoping",
                                        internal_assignee_user_id=world.alice.id, hours_estimated="6",
                                        target_date=today - timedelta(days=1))
    deliverables.create_deliverable(db, world.admin, world.alice_eng.id, kind="phase", name="Q", stage_key="scoping",
                                    internal_assignee_user_id=world.alice.id, target_date=today + timedelta(days=3))
    done = deliverables.create_deliverable(db, world.admin, world.alice_eng.id, kind="phase", name="Done", stage_key="scoping",
                                           internal_assignee_user_id=world.alice.id, hours_estimated="10")
    deliverables.update_deliverable(db, world.admin, done.id, pipeline_status="done")
    engagements.change_status(db, world.admin, world.globex_eng.id, status="complete")

    people = {x.user.id: x for x in capacity.list_capacity(db, world.admin, today=today)}
    alice, bob = people[world.alice.id], people[world.bob.id]
    assert (alice.open_items, alice.open_hours, alice.unestimated, alice.overdue, alice.due_soon) == (2, 6, 1, 1, 1)
    assert [a.engagement.id for a in alice.allocations] == [world.alice_eng.id]
    assert alice.allocations[0].open_items == 2
    # completed engagements don't count towards load
    assert [a.engagement.id for a in bob.allocations] == [world.bob_eng.id]
    assert p.pipeline_status == "not_started"


def test_capacity_is_ops_admin_only(http, db, world):
    with pytest.raises(Forbidden):
        capacity.list_capacity(db, world.alice)
    assert login(http, world.alice.email).get("/team").status_code == 403


def test_allocate_from_person_card(http, db, world):
    admin = login(http, world.admin.email)
    r = admin.post(f"/team/people/{world.bob.id}/allocate",
                   data={"engagement_id": str(world.alice_eng.id), "role": "collaborator"}, headers={"hx-request": "true"})
    assert r.status_code == 200 and f'id="person-{world.bob.id}"' in r.text and "Acme Migration" in r.text
    assert login(http, world.bob.email).get(f"/engagements/{world.alice_eng.id}").status_code == 200
