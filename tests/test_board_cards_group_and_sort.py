"""Boards are one card per engagement with viewer-chosen grouping and sorting, not a fixed
stage-column layout (CLAUDE.md §6, 2026-09-25)."""

from datetime import date, timedelta

from app.services import board, deliverables as D, engagements as E
from tests.conftest import login


def names(groups):
    return [(g.label, [c.engagement.name for c in g.cards]) for g in groups]


def test_default_grouping_follows_how_stages_work(db, world):
    migration = E.get_engagement_type(db, "migration")
    quickstart = E.get_engagement_type(db, "quickstart")
    assert board.derive_default_grouping(migration) == "none"      # derived, concurrent stages
    assert board.derive_default_grouping(quickstart) == "stage"    # hand-set, linear stages


def test_card_facts_and_deadline_sort(db, world):
    today = date(2026, 9, 25)
    a = world.admin
    extra = E.create_engagement(db, a, client_id=world.other_client.id, type_key="quickstart", name="Aardvark QS")
    D.create_deliverable(db, a, world.bob_eng.id, kind="module", name="Late", target_date=today - timedelta(days=2))
    D.create_deliverable(db, a, world.bob_eng.id, kind="module", name="Soon", target_date=today + timedelta(days=3))
    D.create_deliverable(db, a, world.globex_eng.id, kind="module", name="Later", target_date=today + timedelta(days=9))
    cards = [board.derive_card(db, e, today=today) for e in (world.bob_eng, world.globex_eng, extra)]
    bob = cards[0]
    assert (bob.overdue, bob.next_deadline, bob.next_deadline_item) == (1, today + timedelta(days=3), "Soon")
    qs = E.get_engagement_type(db, "quickstart")
    by_deadline = names(board.derive_board(qs, cards, group="none", sort="deadline"))[0][1]
    assert by_deadline == ["Acme QuickStart", "Globex QuickStart", "Aardvark QS"]   # overdue, dated, undated
    by_alpha = names(board.derive_board(qs, cards, group="none", sort="alpha"))[0][1]
    assert by_alpha == ["Acme QuickStart", "Aardvark QS", "Globex QuickStart"]   # by client, then name


def test_group_by_client_and_owner(db, world):
    qs = E.get_engagement_type(db, "quickstart")
    cards = [board.derive_card(db, e) for e in (world.bob_eng, world.globex_eng)]
    assert [g.label for g in board.derive_board(qs, cards, group="client", sort="alpha")] == ["Acme Corp", "Globex"]
    assert names(board.derive_board(qs, cards, group="owner", sort="alpha")) == [
        ("Bob Analyst", ["Acme QuickStart", "Globex QuickStart"])]


def test_board_page_has_controls_and_no_type_switcher(http, world):
    page = login(http, world.admin.email).get("/board/quickstart?group=client&sort=alpha").text
    assert 'name="group"' in page and 'name="sort"' in page
    assert "type-tabs" not in page
    assert page.index("Acme Corp") < page.index("Globex")
