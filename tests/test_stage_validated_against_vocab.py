import pytest

from app.errors import ValidationError
from app.services import engagements as E


def test_stage_must_come_from_the_type_vocab(db, world):
    with pytest.raises(ValidationError, match="not a QuickStart stage"):
        E.change_stage(db, world.admin, world.bob_eng.id, stage="semantic_parity")
    E.change_stage(db, world.admin, world.bob_eng.id, stage="codev")
    E.change_stage(db, world.admin, world.alice_eng.id, stage="semantic_parity")


def test_new_engagement_starts_in_first_stage(db, world):
    e = E.create_engagement(db, world.admin, client_id=world.client.id, type_key="migration", name="Second")
    assert e.stage == "scoping"
    with pytest.raises(ValidationError, match="Unknown engagement type"):
        E.create_engagement(db, world.admin, client_id=world.client.id, type_key="retainer", name="Nope")
