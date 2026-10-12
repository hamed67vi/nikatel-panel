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

def test_demo_does_not_serve_the_key_pair_the_logs_or_the_api_description(demo_client):
    for path in ("/api/nodes/user-key", "/api/nodes/user-key?part=public", "/api/logs", "/api/system/update", "/api/openapi.json", "/api/docs", "/api/servers/1/launcher", "/api/radar", "/api/arvan/records"):
        r = demo_client.get(path)
        assert r.status_code == 403, (path, r.status_code)

    from nikatel.services import nodes

    assert not nodes.user_key_path().exists()           # nothing made a key: the route was refused before it ran


def test_demo_slows_down_a_client_that_asks_too_often(demo_client, monkeypatch):
    from nikatel import main

    main._DEMO_HITS.clear()
    monkeypatch.setattr(main, "_DEMO_LIMIT", 20)
    codes = [demo_client.get("/api/health").status_code for _ in range(30)]
    assert codes[:20] == [200] * 20 and set(codes[20:]) == {429}
    main._DEMO_HITS.clear()


def test_a_named_proxy_is_believed_about_the_client_address(client, monkeypatch):
    from types import SimpleNamespace

    from nikatel import auth
    from nikatel.config import get_settings

    def req(peer, xff):
        return SimpleNamespace(client=SimpleNamespace(host=peer), headers={"x-forwarded-for": xff}, url=SimpleNamespace(scheme="http"))

    assert auth.client_ip(req("203.0.113.9", "198.51.100.7")) == "203.0.113.9"           # a stranger cannot name itself
    monkeypatch.setattr(get_settings(), "trusted_proxies", "203.0.113.9, 203.0.113.10")
    assert auth.client_ip(req("203.0.113.9", "198.51.100.7, 10.0.0.1")) == "198.51.100.7"
    assert auth.client_ip(req("203.0.113.77", "198.51.100.7")) == "203.0.113.77"
    assert auth.client_ip(req("127.0.0.1", "198.51.100.8")) == "198.51.100.8"