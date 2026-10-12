"""The old panel's features in the new one: AI advice, cores check, tunnel quality, the panel test config."""

from nikatel.services import ai, engines, panels, tunnel_test
from nikatel.services.panels import PanelError


def test_ai_parse_advice_keeps_only_known_actions():
    d = ai.parse_advice('noise {"diagnosis": "GRE link down", "actions": [{"action": "restart_gre", "reason": "iface missing"}, {"action": "format_disk", "reason": "x"}, {"action": "notify", "reason": "y"}, {"action": "rebuild"}]} tail')
    assert d["diagnosis"] == "GRE link down" and [a["action"] for a in d["actions"]] == ["restart_gre", "notify"]
    assert ai.parse_advice("not json") == {"diagnosis": "", "actions": []}
    assert ai.advise("facts") is None                       # off by default


def test_ai_settings_api_masks_key(client, monkeypatch):
    monkeypatch.setattr(ai, "ask", lambda *a, **k: "OK")
    r = client.put("/api/settings/ai", json={"enabled": True, "api_key": "sk-test-123", "model": "claude-sonnet-5-5", "actions": False})
    assert r.status_code == 200 and r.json()["has_api_key"] is True and r.json()["api_key"] == "" and r.json()["actions"] is False
    assert client.post("/api/settings/ai/test").json()["answer"] == "OK"
    assert client.delete("/api/settings/ai/key").json()["has_api_key"] is False


def test_engine_versions_parse():
    v = engines.parse_versions("ENGINE backhaul backhaul version v0.6.5\nENGINE xray NONE\n")
    assert v == {"backhaul": "v0.6.5", "xray": "none"}
    # a pre-release suffix stays part of the version, and a plain "1.12.0" gets its v
    v = engines.parse_versions("ENGINE paqet Version:    v1.0.0-alpha.21\nENGINE chisel 1.12.0\nENGINE backpack v1.8.5\n")
    assert v == {"paqet": "v1.0.0-alpha.21", "chisel": "v1.12.0", "backpack": "v1.8.5"}


def test_engines_check_api(client, monkeypatch):
    client.post("/api/servers", json={"name": "ir1", "host": "1.1.1.1", "role": "iran"})
    monkeypatch.setattr(engines, "latest", lambda e: {"version": "v0.6.6", "url": "u", "page": "p", "digest": ""})
    monkeypatch.setattr(engines, "run_script", lambda node, name, params=None, timeout=25: "ENGINE backhaul v0.6.5\nENGINE xray NONE\n")
    import time as _t

    engines._STATE.update(result=None, checked_at=0, checking=False)
    assert client.post("/api/engines/check").json()["started"] is True

    for _ in range(40):                                              # the check runs in the background: wait for it
        _t.sleep(0.1)

        if not client.get("/api/engines").json()["checking"]:
            break

    d = client.get("/api/engines").json()["engines"]
    assert d["backhaul"]["outdated"] == 1 and d["backhaul"]["nodes"][0]["state"] == "outdated"
    assert d["xray"]["nodes"][0]["state"] == "not installed"
    assert client.post("/api/engines/nope/update").status_code == 400


def test_tunnel_quality(client, monkeypatch):
    client.post("/api/servers", json={"name": "ir1", "host": "1.1.1.1", "role": "iran"})
    client.post("/api/servers", json={"name": "fi1", "host": "2.2.2.2", "role": "foreign"})
    from nikatel.db import session
    from nikatel.models import Tunnel

    with session() as db:
        db.add(Tunnel(name="fi-ir1", type="backhaul", transport="tcpmux", iran_server_id=1, foreign_server_id=2, port=9900, iran_tunnel_ip="10.1.1.1", foreign_tunnel_ip="10.1.1.2"))

    pings = "\n".join(f"64 bytes from 10.1.1.2: icmp_seq={i} ttl=64 time={80 + (i % 3)}.0 ms" for i in range(1, 31)) + "\n30 packets transmitted, 30 received, 0% packet loss\n"
    monkeypatch.setattr(tunnel_test, "run_script", lambda node, name, params=None, timeout=45: pings)
    r = client.post("/api/tunnels/1/quality").json()
    assert r["loss_pct"] == 0 and 80 <= r["latency_ms"] <= 82 and r["score"] > 0
    assert client.get("/api/tunnels").json()[0]["quality"]["score"] == r["score"]
    assert len(client.post("/api/tunnels/quality-all").json()["results"]) == 1


def test_panel_test_user_marzban(monkeypatch):
    calls = []

    def fake(c, method, path, **kw):
        calls.append((method, path))

        if method == "GET" and path.startswith("/api/user/"):
            raise PanelError("HTTP 404", 404)
        if path == "/api/inbounds":
            return {"vless": [{"tag": "VLESS_WS"}], "vmess": [{"tag": "VMESS"}]}
        if method == "POST" and path == "/api/user":
            body = kw["body"]
            assert body["username"].startswith("nikatel-test-") and body["proxies"] == {"vless": {}, "vmess": {}} and body["inbounds"]["vless"] == ["VLESS_WS"]
            return {"username": body["username"], "subscription_url": "/sub/abc"}
        raise PanelError("HTTP 404", 404)

    monkeypatch.setattr(panels, "call", fake)
    r = panels.test_user({"id": 1, "url": "https://p.example", "type": "marzban"}, "Shop One")
    assert r["username"] == "nikatel-test-shop-one" and r["sub"] == "https://p.example/sub/abc"
    assert ("POST", "/api/user") in calls


def test_panel_test_config_imports_to_probe(client, monkeypatch):
    from nikatel.services import configs

    monkeypatch.setattr(panels, "_token", lambda c, fresh=False: "t")
    monkeypatch.setattr(panels, "call", lambda c, m, p, **kw: {"version": "1", "total_user": 1} if p == "/api/system" else [] if p == "/api/nodes" else (_ for _ in ()).throw(PanelError("HTTP 404", 404)))
    pid = client.post("/api/panels", json={"name": "shop", "type": "marzban", "url": "https://1.1.1.1", "username": "a", "password": "p"}).json()["panel"]["id"]
    monkeypatch.setattr(panels, "test_user", lambda c, name: {"username": "nikatel-test-shop", "sub": "https://1.1.1.1/sub/x"})
    monkeypatch.setattr(configs, "import_subscription", lambda name, url, picks=None, prefix="": {"added": [prefix + " a", prefix + " b"], "skipped": [], "failed": []})
    r = client.post(f"/api/panels/{pid}/test-config")
    assert r.status_code == 200 and r.json()["added"] == ["shop a", "shop b"] and r.json()["username"] == "nikatel-test-shop"
    assert client.get("/api/panels").json()["panels"][0]["stats"]["test_user"] == "nikatel-test-shop"


def test_ai_check_keeps_the_status_and_names_the_model(client, monkeypatch):
    from nikatel.services import settings as st

    def fake(*a, **k):
        ai.LAST["model"] = "claude-sonnet-5-5-20260101"
        return "OK"

    monkeypatch.setattr(ai, "ask", fake)
    r = client.put("/api/settings/ai", json={"enabled": True, "api_key": "sk-test-1234567890", "model": "claude-sonnet-5-5", "actions": True}).json()
    assert r["status"]["ok"] is True and r["status"]["model"] == "claude-sonnet-5-5-20260101" and r["api_key_tail"] == "7890" and r["api_key"] == ""

    def boom(*a, **k):
        raise RuntimeError("Anthropic API answered 401: invalid x-api-key")

    monkeypatch.setattr(ai, "ask", boom)
    assert client.post("/api/settings/ai/test").status_code == 502
    s = st.get("ai")["status"]
    assert s["ok"] is False and "401" in s["error"]
    assert client.delete("/api/settings/ai/key").json()["status"] == {}
