"""Module access comes from access roles: each grants 'use' or 'manage' per module, a user
can hold several, and the highest level any of them grants wins (CLAUDE.md §3 Identity & access)."""

from sqlalchemy import select

from app.access import MODULES
from app.models import Module
from app.services import access_roles, users


def test_levels_union_across_roles(db, world, crm):
    h = crm.hybrid  # Delivery + Sales
    assert h.module_level("engagements") == "use" and h.module_level("opportunities") == "use"
    assert h.module_level("team") is None
    ops = next(r for r in access_roles.list_access_roles(db) if r.name == "Operations")
    access_roles.set_user_roles(db, world.admin, h.id, role_ids=[r.id for r in h.access_roles] + [ops.id])
    assert all(h.module_level(m) == "manage" for m in MODULES)  # highest level wins


def test_admin_is_manage_everywhere_and_client_users_nothing(db, world, portal):
    assert all(world.admin.can(m, "manage") for m in MODULES)
    assert not any(portal.lead.can(m) for m in MODULES)


def test_new_users_get_the_default_roles(db, world):
    finance = access_roles.create_access_role(db, world.admin, name="Finance")
    access_roles.update_access_role(db, world.admin, finance.id, is_default=True)
    u = users.create_internal_user(db, None, email="newbie@shearwaterdata.com", display_name="New")
    assert sorted(r.name for r in u.access_roles) == ["Delivery", "Finance"]


def test_code_and_registry_agree_on_modules(db):
    assert set(db.scalars(select(Module.key))) == set(MODULES)
