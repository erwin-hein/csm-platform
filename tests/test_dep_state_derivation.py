"""dep_state is derived from the manual block flag + blocker edges, never stored."""

from app.models import Deliverable
from app.services.deliverables import derive_dep_state


def d(status="not_started", blocked=False, na=False):
    return Deliverable(pipeline_status=status, blocked=blocked, not_applicable=na, blocked_reason="r" if blocked else None)


def test_clear_with_no_blockers():
    assert derive_dep_state(d(), []) == "clear"


def test_clear_when_all_blockers_resolved():
    assert derive_dep_state(d(), [d("done"), d(na=True)]) == "clear"


def test_waiting_on_unfinished_blocker():
    assert derive_dep_state(d(), [d("done"), d("external_validation")]) == "waiting"


def test_blocked_flag_without_edges():
    assert derive_dep_state(d(blocked=True), []) == "blocked"


def test_blocked_flag_with_open_blocker():
    assert derive_dep_state(d(blocked=True), [d("in_progress")]) == "blocked"


def test_maybe_unblocked_when_flagged_but_blockers_all_done():
    assert derive_dep_state(d(blocked=True), [d("done")]) == "maybe_unblocked"


def test_no_dep_state_column_exists():
    assert "dep_state" not in Deliverable.__table__.columns
