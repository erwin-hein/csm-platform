import pytest

from app.errors import ValidationError
from app.services import deliverables as D


def test_blockers_reject_self_cycles_and_cross_engagement(db, world):
    eid = world.alice_eng.id
    a, b, c = (D.create_deliverable(db, world.admin, eid, kind="phase", name=n, stage_key="scoping") for n in "abc")
    D.add_blocker(db, world.admin, b.id, blocker_id=a.id)   # b waits on a
    D.add_blocker(db, world.admin, c.id, blocker_id=b.id)   # c waits on b
    with pytest.raises(ValidationError, match="cycle"):
        D.add_blocker(db, world.admin, a.id, blocker_id=c.id)
    with pytest.raises(ValidationError, match="itself"):
        D.add_blocker(db, world.admin, a.id, blocker_id=a.id)
    with pytest.raises(ValidationError, match="Already"):
        D.add_blocker(db, world.admin, b.id, blocker_id=a.id)
    elsewhere = D.create_deliverable(db, world.admin, world.bob_eng.id, kind="module", name="x")
    with pytest.raises(ValidationError, match="same engagement"):
        D.add_blocker(db, world.admin, a.id, blocker_id=elsewhere.id)


def test_blocked_flag_requires_reason(db, world):
    p = D.create_deliverable(db, world.admin, world.alice_eng.id, kind="phase", name="p", stage_key="scoping")
    with pytest.raises(ValidationError, match="why"):
        D.update_deliverable(db, world.admin, p.id, blocked=True, blocked_reason="  ")
    D.update_deliverable(db, world.admin, p.id, blocked=True, blocked_reason="waiting on client")
    D.update_deliverable(db, world.admin, p.id, blocked=False)
    assert p.blocked is False and p.blocked_reason is None


def test_assignee_must_be_on_the_team(db, world):
    p = D.create_deliverable(db, world.admin, world.alice_eng.id, kind="phase", name="p", stage_key="scoping")
    with pytest.raises(ValidationError, match="isn't on this engagement's team"):
        D.update_deliverable(db, world.admin, p.id, internal_assignee_user_id=world.bob.id)
    D.update_deliverable(db, world.admin, p.id, internal_assignee_user_id=world.alice.id)
