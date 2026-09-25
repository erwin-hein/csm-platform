"""The schema explorer is an admin-only PoC demo aid."""

from tests.conftest import login


def test_only_admins(http, world, crm):
    assert login(http, world.admin.email).get("/admin/schema").status_code == 200
    for u in (world.ops, world.alice, crm.lead):
        assert login(http, u.email).get("/admin/schema").status_code == 403
