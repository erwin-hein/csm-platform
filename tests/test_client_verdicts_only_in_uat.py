"""Clients can see and comment any time, but a verdict needs the item to be in UAT, and
comes from the project lead or the item's own client owner. It never moves the pipeline."""

import pytest

from app.errors import Forbidden, ValidationError
from app.services import client_portal, deliverables
from tests.conftest import login


def to_uat(db, world, d):
    deliverables.update_deliverable(db, world.admin, d.id, pipeline_status="external_validation")


def test_no_verdict_before_uat(db, portal):
    with pytest.raises(ValidationError, match="UAT"):
        client_portal.submit_review(db, portal.qa, portal.qa_tile.id, verdict="accepted")


def test_owner_and_lead_can_review_in_uat_others_cannot(db, world, portal):
    to_uat(db, world, portal.qa_tile)
    to_uat(db, world, portal.dash)
    client_portal.submit_review(db, portal.qa, portal.qa_tile.id, verdict="rejected", comment="Off by one day")
    client_portal.submit_review(db, portal.lead, portal.qa_tile.id, verdict="accepted")
    # the QA user sees the dashboard for context only; it isn't theirs to review
    with pytest.raises(Forbidden):
        client_portal.submit_review(db, portal.qa, portal.dash.id, verdict="accepted")
    with pytest.raises(Forbidden):
        client_portal.submit_review(db, world.admin, portal.qa_tile.id, verdict="accepted")


def test_verdict_history_latest_wins_and_pipeline_untouched(db, world, portal):
    to_uat(db, world, portal.qa_tile)
    client_portal.submit_review(db, portal.qa, portal.qa_tile.id, verdict="rejected", comment="Numbers are off")
    client_portal.submit_review(db, portal.qa, portal.qa_tile.id, verdict="accepted")
    assert client_portal.get_latest_verdicts(db, [portal.qa_tile.id])[portal.qa_tile.id].verdict == "accepted"
    assert [r.verdict for r in client_portal.list_reviews(db, portal.qa_tile.id)] == ["accepted", "rejected"]
    assert portal.qa_tile.pipeline_status == "external_validation"
    assert "[Rejected] Numbers are off" in [c.body for c in client_portal.list_comments(db, portal.qa, portal.qa_tile.id)]


def test_http_review_buttons_only_in_uat(http, db, world, portal):
    qa = login(http, portal.qa.email)
    assert "Verdicts open once this is in UAT" in qa.get(f"/portal/deliverables/{portal.qa_tile.id}").text
    assert qa.post(f"/portal/deliverables/{portal.qa_tile.id}/review", data={"verdict": "accepted"},
                   follow_redirects=False).status_code == 400
    to_uat(db, world, portal.qa_tile)
    db.flush()
    assert "✓ Accept" in qa.get(f"/portal/deliverables/{portal.qa_tile.id}").text
    assert qa.post(f"/portal/deliverables/{portal.qa_tile.id}/review", data={"verdict": "accepted"},
                   follow_redirects=False).status_code == 303
