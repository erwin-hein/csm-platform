"""The Malloy semantic model (CLAUDE.md §3 Semantic layer): the compiled build is current, every
view's compiled SQL runs, its numbers match the app's, and only admins can browse or run it."""

import hashlib
from decimal import Decimal

import pytest

from app import seed, semantic
from app.services import forecast as F
from app.services import opportunities as O
from tests.conftest import login


@pytest.fixture
def demo(db):
    seed.seed(db)
    db.flush()
    return db


def test_build_is_compiled_from_the_current_model_file():
    model = semantic.load_model()
    assert model["model_sha256"] == hashlib.sha256(semantic.MODEL_FILE.read_bytes()).hexdigest(), \
        "analytics/models/csm.malloy changed: run `npm run compile` in analytics/ and commit the build"
    assert not semantic.is_stale(model)


def test_every_view_runs_read_only(demo):
    model = semantic.load_model()
    assert len(model["views"]) >= 10
    for v in model["views"]:
        result = semantic.run_view(demo, v)
        assert result.columns or not result.rows, v["name"]


def test_pipeline_by_stage_matches_the_pipeline_board(demo):
    view = semantic.find_view(semantic.load_model(), "opportunities", "pipeline_by_stage")
    result = semantic.run_view(demo, view)
    col = result.columns.index
    malloy = {r[col("stage_label")]: (r[col("deal_count")], Decimal(str(r[col("open_pipeline")])),
                                      Decimal(str(r[col("weighted_pipeline")])))
              for r in result.rows}
    admin = next(u for u in O.list_sales_users(demo) if u.is_admin)
    board = F.derive_pipeline(O.list_stages(demo), O.list_visible_opportunities(demo, admin, include_closed=False),
                              show_closed=False)
    app_side = {c.stage.label: (len(c.cards), c.total, c.weighted) for c in board if c.cards}
    assert malloy == app_side and len(app_side) >= 3


def test_run_refuses_anything_that_is_not_a_read(db):
    with pytest.raises(ValueError):
        semantic.run_view(db, {"sql": "DELETE FROM events"})


def test_admin_only(http, world, crm):
    admin = login(http, world.admin.email)
    page = admin.get("/admin/semantic?source=opportunities").text
    assert "pipeline_by_stage" in page and "weighted_pipeline" in page
    assert "source: users is" in admin.get("/admin/semantic?source=_file").text
    r = admin.post("/admin/semantic/run", data={"source": "opportunities", "view": "pipeline_by_stage"})
    assert r.status_code == 200 and "Ran read-only" in r.text
    assert admin.post("/admin/semantic/run", data={"source": "opportunities", "view": "nope"}).status_code == 404
    for u in (world.ops, crm.lead):
        c = login(http, u.email)
        assert c.get("/admin/semantic").status_code == 403
        assert c.post("/admin/semantic/run", data={"source": "opportunities", "view": "pipeline_by_stage"}).status_code == 403
