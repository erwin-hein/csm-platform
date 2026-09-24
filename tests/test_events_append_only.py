import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError


def test_events_reject_update_and_delete(db, world):
    for stmt in ("UPDATE events SET event_type = 'tampered'", "DELETE FROM events"):
        sp = db.begin_nested()
        with pytest.raises(DBAPIError, match="append-only"):
            db.execute(text(stmt))
        sp.rollback()
