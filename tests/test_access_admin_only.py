"""Only admins edit access roles, grants and who holds them; an admin can't lock themselves out."""

import pytest

from app.errors import Forbidden, ValidationError
from app.services import access_roles
from tests.conftest import login


def test_non_admins_cannot_edit_access(http, db, world):
    ops = login(http, world.ops.email)  # Operations: manage everywhere, still not admin
    assert ops.get("/admin/access").status_code == 403
    with pytest.raises(Forbidden):
        access_roles.create_access_role(db, world.ops, name="Sneaky")
    delivery = next(r for r in access_roles.list_access_roles(db) if r.name == "Delivery")
    with pytest.raises(Forbidden):
        access_roles.set_role_grant(db, world.ops, delivery.id, module_key="opportunities", level="manage")


def test_admin_grants_take_effect(http, db, world):
    delivery = next(r for r in access_roles.list_access_roles(db) if r.name == "Delivery")
    admin = login(http, world.admin.email)
    assert admin.get("/admin/access").status_code == 200
    r = admin.post(f"/admin/access/roles/{delivery.id}/grants", data={"module_key": "opportunities", "level": "use"},
                   headers={"hx-request": "true"})
    assert r.status_code == 200
    db.expire_all()
    assert world.alice.can("opportunities")


def test_admin_cannot_remove_own_admin(db, world):
    with pytest.raises(ValidationError):
        access_roles.set_user_flags(db, world.admin, world.admin.id, is_admin=False)
