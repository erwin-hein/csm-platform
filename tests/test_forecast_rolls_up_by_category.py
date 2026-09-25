"""The forecast groups opportunities closing in the period by forecast category, per owner:
commit forecast = closed won + commit; best case adds best case; lost and omitted stay out."""

from datetime import date
from decimal import Decimal

from app.services import forecast as F
from app.services import opportunities as O


def test_forecast_rollup(db, world, crm):
    def deal(name, stage, close, price, owner=crm.rep, **kw):
        return O.create_opportunity(db, owner, client_id=world.client.id, name=name, stage_key=stage,
                                    close_date=close, product_id=crm.hours.id, quantity=1, unit_price=price, **kw)

    deal("Won", "closed_won", "2030-02-10", "10000")
    deal("Commit", "negotiation", "2030-03-01", "20000")                   # negotiation → commit, 80%
    deal("Best", "proposal", "2030-01-15", "30000")                        # proposal → best case, 60%
    deal("Pipe", "discovery", "2030-02-20", "40000", owner=crm.hybrid)     # pipeline, 40%
    deal("Omitted", "discovery", "2030-02-20", "50000", forecast_category="omitted")
    deal("Lost", "closed_lost", "2030-02-01", "60000", lost_reason="No budget")
    deal("Next quarter", "negotiation", "2030-04-02", "70000")

    period = F.derive_period("quarter", date(2030, 2, 1))
    assert (period.start, period.end, period.label) == (date(2030, 1, 1), date(2030, 3, 31), "Q1 2030")
    fc = F.derive_forecast(O.list_visible_opportunities(db, crm.lead), period)
    t = fc.total
    assert t.won == Decimal("10000")
    assert t.commit_forecast == Decimal("30000")
    assert t.best_case_forecast == Decimal("60000")
    assert t.open_pipeline == Decimal("90000")
    assert t.lost == Decimal("60000")
    assert t.weighted_open == Decimal("16000") + Decimal("18000") + Decimal("16000") + Decimal("20000")
    assert {r.label: r.count for r in fc.rows} == {"Hana Hybrid": 1, "Sam Sales": 5}
    assert "Next quarter" not in {o.name for o in fc.deals}


def test_month_periods(db):
    p = F.derive_period("month", date(2030, 12, 15), offset=1)
    assert (p.start, p.end, p.label) == (date(2031, 1, 1), date(2031, 1, 31), "Jan 2031")
