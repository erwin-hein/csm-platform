"""Event-emission drift guard (CLAUDE.md §3 Testing strategy): every mutating service
function is registered via @mutation, and each one actually lands its event row."""

import importlib
import inspect
import pkgutil

import pytest
from sqlalchemy import select

import app.services
from app.events import MUTATIONS, mutation
from app.models import Event
from app.services import access_roles, client_portal, clients, deliverables, engagements, memberships, opportunities, users

READ_PREFIXES = ("get_", "list_", "derive_", "kinds_for_", "blocker_", "blocking_", "engagement_", "parent_",
                 "is_allowed_", "can_")
# Not a mutation itself: delegates to create_internal_user (which is) only on first login.
EXEMPT = {"app.services.users.login_or_bootstrap"}


def _public_service_functions():
    for info in pkgutil.iter_modules(app.services.__path__):
        module = importlib.import_module(f"app.services.{info.name}")
        for name, fn in inspect.getmembers(module, inspect.isfunction):
            if fn.__module__ == module.__name__ and not name.startswith("_"):
                yield f"{module.__name__}.{name}", fn


def test_every_public_service_function_is_a_declared_mutation_or_a_read():
    unclassified = [
        qual for qual, fn in _public_service_functions()
        if not hasattr(fn, "__mutation_event__") and not qual.rsplit(".", 1)[1].startswith(READ_PREFIXES)
        and qual not in EXEMPT
    ]
    assert unclassified == [], f"Mutating functions must use @mutation: {unclassified}"


def test_mutation_guard_raises_when_no_event_is_emitted(db):
    @mutation("never_emitted")
    def sloppy(db, actor):
        return None

    with pytest.raises(RuntimeError, match="without emitting"):
        sloppy(db, None)
    MUTATIONS.pop(f"{sloppy.__module__}.{sloppy.__qualname__}", None)


def test_each_mutation_writes_its_event_row(db, world):
    a = world.admin
    exercised: set[str] = set()

    def run(fn, *args, **kwargs):
        result = fn(db, *args, **kwargs)
        event_type = fn.__mutation_event__
        assert db.scalar(select(Event).where(Event.event_type == event_type).order_by(Event.id.desc())), event_type
        exercised.add(f"{fn.__module__}.{fn.__qualname__}")
        return result

    run(users.create_internal_user, a, email="new@shearwaterdata.com", display_name="New")
    c = run(clients.create_client, a, name="Initech")
    run(clients.add_alias, a, c.id, alias="ITC", alias_type="acronym")
    run(clients.add_contact, a, c.id, name="Bill")
    qs = engagements.create_engagement(db, a, client_id=c.id, type_key="quickstart", name="Initech QS")
    run(engagements.change_stage, a, qs.id, stage="dev_training")
    e = run(engagements.create_engagement, a, client_id=c.id, type_key="migration", name="Initech Migration")
    run(engagements.change_status, a, e.id, status="paused")
    run(memberships.assign_member, a, e.id, user_id=world.alice.id, role="owner")
    run(memberships.remove_member, a, e.id, user_id=world.alice.id)
    p1 = run(deliverables.create_deliverable, a, e.id, kind="phase", name="P1", stage_key="scoping")
    p2 = run(deliverables.create_deliverable, a, e.id, kind="phase", name="P2", stage_key="scoping")
    run(deliverables.update_deliverable, a, p1.id, pipeline_status="in_progress")
    run(deliverables.add_note, a, p1.id, body="note")
    run(deliverables.add_blocker, a, p2.id, blocker_id=p1.id)
    counted = deliverables.create_deliverable(db, a, e.id, kind="dashboard", name="Counted", child_count=10)
    run(deliverables.set_child_counts, a, counted.id, counts={"done": 4, "not_started": 6})
    run(deliverables.remove_blocker, a, p2.id, blocker_id=p1.id)

    contact = clients.add_contact(db, a, c.id, name="Carla Client", email="carla@initech.com")
    m = run(client_portal.invite_client_contact, a, e.id, contact_id=contact.id, viewer_scope="full")
    run(client_portal.set_client_scope, a, e.id, user_id=m.user_id, viewer_scope="assigned_only")
    dash = deliverables.create_deliverable(db, a, e.id, kind="dashboard", name="Dash")
    deliverables.update_deliverable(db, a, dash.id, client_owner_user_id=m.user_id, pipeline_status="external_validation")
    run(client_portal.add_client_comment, a, dash.id, body="Ready for review")
    run(client_portal.submit_review, m.user, dash.id, verdict="accepted")
    run(client_portal.revoke_client_access, a, e.id, user_id=m.user_id)

    role = run(access_roles.create_access_role, a, name="Finance")
    run(access_roles.update_access_role, a, role.id, is_default=True)
    run(access_roles.set_role_grant, a, role.id, module_key="opportunities", level="use")
    run(access_roles.set_user_roles, a, world.bob.id, role_ids=[role.id])
    run(access_roles.set_user_flags, a, world.bob.id, is_contractor=True)

    prod = run(opportunities.create_product, a, name="Widget", pricing_model="fixed_bid", default_unit_price="1000")
    run(opportunities.update_product, a, prod.id, name="Widget", pricing_model="fixed_bid", default_unit_price="1200")
    o = run(opportunities.create_opportunity, a, client_id=c.id, name="Initech deal", stage_key="discovery",
            close_date="2030-01-01", product_id=prod.id)
    run(opportunities.update_opportunity, a, o.id, next_step="Call")
    li = run(opportunities.add_line_item, a, o.id, product_id=prod.id, quantity=2)
    run(opportunities.update_line_item, a, li.id, quantity=3, unit_price="1000")
    run(opportunities.remove_line_item, a, li.id)
    run(opportunities.change_opportunity_stage, a, o.id, stage_key="closed_won")
    run(engagements.link_opportunity, a, qs.id, opportunity_id=o.id)

    assert exercised == set(MUTATIONS), f"Add a case for: {set(MUTATIONS) - exercised}"


def test_event_carries_actor_and_entity(db, world):
    e = engagements.change_stage(db, world.admin, world.bob_eng.id, stage="dev_training")
    ev = db.scalar(select(Event).where(Event.event_type == "stage_changed").order_by(Event.id.desc()))
    assert ev.entity_type == "engagement" and ev.entity_id == e.id
    assert ev.actor_user_id == world.admin.id
    assert ev.payload == {"from": "kickoff", "to": "dev_training"}


def test_emit_is_in_same_transaction_as_the_write(db, world):
    """A failed request rolls back both the mutation and its event."""
    sp = db.begin_nested()
    engagements.change_stage(db, world.admin, world.bob_eng.id, stage="dev_training")
    sp.rollback()
    assert db.scalar(select(Event).where(Event.event_type == "stage_changed")) is None
    db.refresh(world.bob_eng)
    assert world.bob_eng.stage == "kickoff"
