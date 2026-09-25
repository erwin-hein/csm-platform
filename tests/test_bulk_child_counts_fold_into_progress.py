"""A dashboard can track most of its tiles by count and list only the troublesome ones;
both fold into the same progress (CLAUDE.md §3 Deliverables)."""

import pytest

from app.errors import ValidationError
from app.services import client_portal
from app.services import deliverables as D


def dash_node(db, e, name):
    tree = D.engagement_tree(db, e)
    return next(n for s in tree.sections for n in s.nodes if n.deliverable.name == name), tree


def test_listing_a_tile_takes_it_out_of_the_count(db, world):
    e, a = world.alice_eng, world.admin
    dash = D.create_deliverable(db, a, e.id, kind="dashboard", name="Big", child_count=99)
    D.create_deliverable(db, a, e.id, kind="tile", name="Tricky 1", parent_id=dash.id)
    D.create_deliverable(db, a, e.id, kind="tile", name="Tricky 2", parent_id=dash.id)
    assert dash.child_counts == {"not_started": 97}
    node, _ = dash_node(db, e, "Big")
    assert node.progress.applicable == 99


def test_counts_and_listed_tiles_roll_up_together(db, world):
    e, a = world.alice_eng, world.admin
    dash = D.create_deliverable(db, a, e.id, kind="dashboard", name="Big", child_count=10)
    t = D.create_deliverable(db, a, e.id, kind="tile", name="Tricky", parent_id=dash.id)
    D.update_deliverable(db, a, t.id, pipeline_status="done")
    D.set_child_counts(db, a, dash.id, counts={"done": 3, "in_progress": 2, "not_started": 4})
    node, tree = dash_node(db, e, "Big")
    assert (node.progress.done, node.progress.applicable) == (4, 10)
    assert node.progress.by_status["in_progress"] == 2
    section = next(s for s in tree.sections if s.kind.kind == "dashboard")
    assert (section.progress.done, section.progress.applicable) == (4, 10)


def test_counts_validation(db, world):
    e, a = world.alice_eng, world.admin
    phase = D.create_deliverable(db, a, e.id, kind="phase", name="P", stage_key="scoping")
    with pytest.raises(ValidationError, match="by count"):
        D.set_child_counts(db, a, phase.id, counts={"done": 1})
    dash = D.create_deliverable(db, a, e.id, kind="dashboard", name="D")
    with pytest.raises(ValidationError, match="negative"):
        D.set_child_counts(db, a, dash.id, counts={"done": -1})
    with pytest.raises(ValidationError, match="Unknown status"):
        D.set_child_counts(db, a, dash.id, counts={"shipped": 1})


def test_portal_lead_sees_counted_totals_scoped_user_does_not(db, world, portal):
    D.set_child_counts(db, world.admin, portal.dash.id, counts={"done": 5, "not_started": 5})
    lead_m = client_portal.list_client_engagements(db, portal.lead)[0][1]
    qa_m = client_portal.list_client_engagements(db, portal.qa)[0][1]
    lead = client_portal.derive_client_view(db, portal.eng, viewer=portal.lead, membership=lead_m)
    qa = client_portal.derive_client_view(db, portal.eng, viewer=portal.qa, membership=qa_m)
    assert sum(s.total for s in lead) == 4 + 10 and sum(s.done for s in lead) == 5
    assert sum(s.total for s in qa) == 2
