"""The "gate" stage concept was removed (CLAUDE.md §6): no stage vocabulary carries it."""

from sqlalchemy import select

from app.models import EngagementType


def test_no_stage_is_flagged_as_a_gate(db):
    for etype in db.scalars(select(EngagementType)):
        assert all("gate" not in stage for stage in etype.stage_vocab), etype.key
        assert all(set(stage) == {"key", "label"} for stage in etype.stage_vocab), etype.key
