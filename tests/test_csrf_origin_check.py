from fastapi.testclient import TestClient

from app.main import app
from tests.conftest import login


def test_state_changing_requests_need_same_origin(http, world):
    c = login(http, world.admin.email)
    ok = c.post("/clients", data={"name": "Same Origin"}, follow_redirects=False)
    assert ok.status_code == 303
    cookies = c.cookies
    for headers in ({}, {"origin": "https://evil.example"}, {"referer": "https://evil.example/x"},
                    {"origin": "null"}):
        bare = TestClient(app, cookies=cookies)
        r = bare.post("/clients", data={"name": "Forged"}, headers=headers, follow_redirects=False)
        assert r.status_code == 403, headers
    same_referer = TestClient(app, cookies=cookies)
    r = same_referer.post("/clients", data={"name": "Via referer"}, headers={"referer": "http://testserver/clients/new"},
                          follow_redirects=False)
    assert r.status_code == 303
