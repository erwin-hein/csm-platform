"""Waiting on unfinished work is only urgent once the waiting item has started;
before that it's ordinary sequencing and shows quietly (CLAUDE.md §3 dep_state)."""

from app.models import Deliverable
from app.services.deliverables import Progress, derive_dep_signal, derive_dep_state


def d(name="x", status="not_started", blocked=False):
    return Deliverable(name=name, pipeline_status=status, blocked=blocked, not_applicable=False,
                       blocked_reason="client IT" if blocked else None)


def signal(item, blockers):
    return derive_dep_signal(item, derive_dep_state(item, blockers), blockers)


def test_unstarted_item_waiting_on_open_work_is_quiet():
    s = signal(d(), [d("Model", "in_progress")])
    assert s.level == "info" and not s.needs_attention and "Model" in s.label


def test_started_item_waiting_on_open_work_warns():
    s = signal(d(status="in_progress"), [d("Model", "in_progress")])
    assert s.level == "warn" and s.needs_attention


def test_manual_block_is_always_loud_and_stale_flag_asks_for_a_check():
    assert signal(d(blocked=True), []).level == "alert"
    assert signal(d(blocked=True), [d("Model", "done")]).level == "check"


def test_clear_has_no_signal():
    assert signal(d(), [d("Model", "done")]) is None


def test_quiet_sequencing_does_not_count_towards_attention():
    p = Progress()
    p.add(d(), signal(d(), [d("Model", "in_progress")]))
    p.add(d(status="in_progress"), signal(d(status="in_progress"), [d("Model", "in_progress")]))
    assert (p.applicable, p.attention) == (2, 1)
