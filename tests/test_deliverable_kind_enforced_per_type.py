import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app.errors import ValidationError
from app.services import deliverables as D


def test_kind_must_belong_to_engagement_type(db, world):
    with pytest.raises(ValidationError, match="isn't a QuickStart deliverable kind"):
        D.create_deliverable(db, world.admin, world.bob_eng.id, kind="phase", name="Phase on a QS")
    with pytest.raises(ValidationError, match="isn't a Migration deliverable kind"):
        D.create_deliverable(db, world.admin, world.alice_eng.id, kind="module", name="Module on a migration")


def test_child_kinds_require_the_right_parent(db, world):
    eid = world.alice_eng.id
    phase = D.create_deliverable(db, world.admin, eid, kind="phase", name="Phase")
    dash = D.create_deliverable(db, world.admin, eid, kind="dashboard", name="Dash")
    with pytest.raises(ValidationError, match="must sit under a phase"):
        D.create_deliverable(db, world.admin, eid, kind="milestone", name="Orphan")
    with pytest.raises(ValidationError, match="must sit under a dashboard, not a phase"):
        D.create_deliverable(db, world.admin, eid, kind="tile", name="Tile under phase", parent_id=phase.id)
    with pytest.raises(ValidationError, match="top-level"):
        D.create_deliverable(db, world.admin, eid, kind="dashboard", name="Nested dash", parent_id=phase.id)
    tile = D.create_deliverable(db, world.admin, eid, kind="tile", name="Tile", parent_id=dash.id)
    assert tile.parent_id == dash.id and tile.engagement_type_key == "migration"


def test_parent_must_be_on_same_engagement(db, world):
    other = D.create_deliverable(db, world.admin, world.globex_eng.id, kind="module", name="Elsewhere")
    with pytest.raises(ValidationError, match="same engagement"):
        D.create_deliverable(db, world.admin, world.alice_eng.id, kind="milestone", name="X", parent_id=other.id)


def test_quickstart_modules_are_flat(db, world):
    m = D.create_deliverable(db, world.admin, world.bob_eng.id, kind="module", name="M1")
    with pytest.raises(ValidationError, match="top-level"):
        D.create_deliverable(db, world.admin, world.bob_eng.id, kind="module", name="M2", parent_id=m.id)


def test_database_rejects_kind_type_mismatch_directly(db, world):
    """The composite FKs back the service rules up at the DB level."""
    sp = db.begin_nested()
    with pytest.raises(IntegrityError):
        db.execute(text(
            "INSERT INTO deliverables (id, engagement_id, engagement_type_key, kind, name) "
            "VALUES (:id, :eid, 'quickstart', 'module', 'lies about its type')"
        ), {"id": uuid.uuid4(), "eid": world.alice_eng.id})
    sp.rollback()


def test_engagement_tree_differs_by_type(db, world):
    D.create_deliverable(db, world.admin, world.bob_eng.id, kind="module", name="M1")
    qs = D.engagement_tree(db, world.bob_eng)
    mig = D.engagement_tree(db, world.alice_eng)
    assert [(s.kind.kind, s.child_kind) for s in qs.sections] == [("module", None)]
    assert sorted((s.kind.kind, s.child_kind.kind) for s in mig.sections) == [("dashboard", "tile"),
                                                                               ("phase", "milestone")]


def test_progress_rolls_up_from_children_and_skips_not_applicable(db, world):
    eid = world.alice_eng.id
    phase = D.create_deliverable(db, world.admin, eid, kind="phase", name="P")
    m1 = D.create_deliverable(db, world.admin, eid, kind="milestone", name="m1", parent_id=phase.id)
    m2 = D.create_deliverable(db, world.admin, eid, kind="milestone", name="m2", parent_id=phase.id)
    m3 = D.create_deliverable(db, world.admin, eid, kind="milestone", name="m3", parent_id=phase.id)
    D.update_deliverable(db, world.admin, m1.id, pipeline_status="done")
    D.update_deliverable(db, world.admin, m3.id, not_applicable=True)
    tree = D.engagement_tree(db, world.alice_eng)
    node = next(s for s in tree.sections if s.kind.kind == "phase").nodes[0]
    assert (node.progress.done, node.progress.applicable) == (1, 2)
    assert m2.pipeline_status == "not_started"
