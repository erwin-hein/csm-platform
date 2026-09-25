"""Stage rules are minimal: Closed Lost needs a reason; closing sets the close date to today;
closed stages fix probability and forecast category (won 100/closed, lost 0/omitted)."""

from datetime import date

import pytest

from app.errors import ValidationError
from app.services import opportunities as O


def _deal(db, world, crm, **kw):
    return O.create_opportunity(db, crm.rep, client_id=world.client.id, name="Deal", stage_key="negotiation",
                                close_date="2030-06-30", product_id=crm.qs.id, **kw)


def test_closed_lost_needs_a_reason(db, world, crm):
    o = _deal(db, world, crm)
    with pytest.raises(ValidationError, match="why"):
        O.change_opportunity_stage(db, crm.rep, o.id, stage_key="closed_lost")
    O.change_opportunity_stage(db, crm.rep, o.id, stage_key="closed_lost", lost_reason="Went with a competitor")
    assert o.lost_reason == "Went with a competitor" and o.effective_forecast_category == "omitted"
    assert o.effective_probability == 0


def test_closing_fixes_the_forecast_and_date(db, world, crm):
    o = _deal(db, world, crm, probability=90, forecast_category="best_case")
    assert (o.effective_probability, o.effective_forecast_category) == (90, "best_case")
    O.change_opportunity_stage(db, crm.rep, o.id, stage_key="closed_won")
    assert o.close_date == date.today() and o.closed_at is not None
    assert (o.effective_probability, o.effective_forecast_category) == (100, "closed")
    with pytest.raises(ValidationError, match="fixed by its stage"):
        O.update_opportunity(db, crm.rep, o.id, probability=50)
    O.change_opportunity_stage(db, crm.rep, o.id, stage_key="negotiation")  # reopening keeps the overrides
    assert o.closed_at is None and o.effective_probability == 90
