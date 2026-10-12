"""The public demo: invented data, no sign-in, every change refused, and the secrets of a real install nowhere."""
import pytest

from nikatel.config import get_settings


@pytest.fixture
def demo_client(monkeypatch):
    from fastapi.testclient import TestClient

    from nikatel.main import create_app

    monkeypatch.setattr(get_settings(), "demo", True)

    with TestClient(create_app(run_jobs=False)) as c:
        yield c


def test_demo_shows_itself_without_a_sign_in_and_has_invented_data(demo_client):
    me = demo_client.get("/api/auth/me")
    assert me.status_code == 200 and me.json()["demo"] is True
    servers = demo_client.get("/api/servers").json()
    rows = servers["servers"] if isinstance(servers, dict) else servers
    assert len(rows) >= 10 and {r["role"] for r in rows} == {"iran", "foreign"}
    assert all(r["host"].startswith(("198.51.100.", "203.0.113.")) for r in rows)          # documentation ranges only: no real address
    assert demo_client.get("/api/tunnels").status_code == 200
    assert demo_client.get("/api/configs").status_code == 200
    assert demo_client.get("/api/events").status_code == 200


def test_demo_refuses_every_change(demo_client):
    for method, path in (("post", "/api/servers"), ("put", "/api/servers/1"), ("delete", "/api/servers/1"), ("post", "/api/tunnels"), ("post", "/api/auth/password"), ("put", "/api/settings/arvan")):
        r = demo_client.request(method.upper(), path, json={})
        assert r.status_code == 403 and "read-only demo" in r.json()["error"], (method, path, r.status_code)

    assert demo_client.get("/api/servers").json()          # still all there


def test_the_seed_does_not_touch_a_database_that_has_servers(client):
    from nikatel.services import demo

    assert demo.seed() == "demo data created"
    assert "not empty" in demo.seed()


def test_a_normal_install_is_not_a_demo(client):
    assert client.get("/api/auth/me").json().get("demo") is False