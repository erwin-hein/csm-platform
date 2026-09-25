"""One stage mechanism for every type: a type with a stage kind (migration → phase) derives
each stage's state from its phases, so several can be active at once; a type without one
(quickstart) keeps a hand-set stage (CLAUDE.md §3 Engagement + type system)."""

import pytest
from sqlalchemy import select

from app.errors import ValidationError
from app.models import Event
from app.services import deliverables as D
from app.services import engagements as E
from app.services.stages import derive_active_keys, derive_board_keys
from tests.conftest import login


def states(e):
    return {s.key: s.state for s in e.stage_states}


def test_migration_stages_follow_phases_and_overlap(db, world):
    e, a = world.alice_eng, world.admin
    assert e.stage is None and set(states(e).values()) == {"empty"}
    parity = D.create_deliverable(db, a, e.id, kind="phase", name="Parity", stage_key="semantic_parity")
    build = D.create_deliverable(db, a, e.id, kind="phase", name="Build", stage_key="dashboard_build")
    assert states(e)["semantic_parity"] == "upcoming"
    D.update_deliverable(db, a, parity.id, pipeline_status="in_progress")
    D.update_deliverable(db, a, build.id, pipeline_status="in_progress")
    assert derive_active_keys(e.stage_states) == {"semantic_parity", "dashboard_build"}
    assert e.stage_label == "Semantic-layer Parity + Dashboard Build"
    D.update_deliverable(db, a, parity.id, pipeline_status="done")
    assert states(e)["semantic_parity"] == "done" and derive_board_keys(e.stage_states) == ["dashboard_build"]


def test_a_started_milestone_makes_its_stage_active(db, world):
    e, a = world.alice_eng, world.admin
    phase = D.create_deliverable(db, a, e.id, kind="phase", name="Scoping", stage_key="scoping")
    m = D.create_deliverable(db, a, e.id, kind="milestone", name="Inventory", parent_id=phase.id)
    D.update_deliverable(db, a, m.id, pipeline_status="in_progress")
    assert states(e)["scoping"] == "active"


def test_stage_changes_still_land_on_the_spine(db, world):
    e, a = world.alice_eng, world.admin
    phase = D.create_deliverable(db, a, e.id, kind="phase", name="Scoping", stage_key="scoping")
    D.update_deliverable(db, a, phase.id, pipeline_status="in_progress")
    ev = db.scalar(select(Event).where(Event.event_type == "stage_state_changed").order_by(Event.id.desc()))
    assert ev.entity_id == e.id and ev.payload["changes"] == [{"stage": "scoping", "from": "upcoming", "to": "active"}]


def test_derived_stages_cant_be_set_by_hand_and_phases_need_a_stage(db, world):
    with pytest.raises(ValidationError, match="follow its phases"):
        E.change_stage(db, world.admin, world.alice_eng.id, stage="scoping")
    with pytest.raises(ValidationError, match="Pick which stage"):
        D.create_deliverable(db, world.admin, world.alice_eng.id, kind="phase", name="Unlinked")
    with pytest.raises(ValidationError, match="Only phase deliverables"):
        D.create_deliverable(db, world.admin, world.alice_eng.id, kind="dashboard", name="D", stage_key="scoping")


def test_quickstart_keeps_a_hand_set_linear_stage(db, world):
    E.change_stage(db, world.admin, world.bob_eng.id, stage="codev")
    s = states(world.bob_eng)
    assert (s["kickoff"], s["dev_training"], s["codev"], s["wrapup"]) == ("done", "done", "active", "upcoming")


def test_board_shows_a_card_in_every_active_column(http, db, world):
    e, a = world.alice_eng, world.admin
    for key in ("semantic_parity", "dashboard_build"):
        p = D.create_deliverable(db, a, e.id, kind="phase", name=key, stage_key=key)
        D.update_deliverable(db, a, p.id, pipeline_status="in_progress")
    db.flush()
    page = login(http, world.admin.email).get("/board/migration").text
    assert page.count("Acme Migration") == 2
