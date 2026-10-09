import base64
import json

import pytest

from nikatel.services import configs, probe
from nikatel.services.configs import (
    clean_name,
    decode_subscription,
    link_name,
    parse_health,
    with_name,
)

HEALTH = {"ok": True, "summary": {"total": 2, "up": 1}, "configs": [
    {"name": "Turkey-1", "slug": "turkey-1", "port": 11000, "push_url": "http://x", "status": "up", "ping": 607, "checked_at": 1791575762, "error": None,
     "speed": {"name": "Turkey-1", "ping": 350.0, "download": 34.93, "upload": 12.61, "timestamp": "2026-10-09T04:00:13+00:00", "ok": True}},
    {"name": "Czech", "slug": "czech", "port": 11001, "status": "down", "ping": 0, "checked_at": 1791575700, "error": "curl: (28) SSL connection timeout", "speed": {}}]}


def test_parse_health():
    rows = parse_health(HEALTH)
    assert rows[0]["status"] == "up" and rows[0]["down_mbps"] == 34.93 and rows[0]["ping_ms"] == 607
    assert rows[1]["status"] == "down" and rows[1]["down_mbps"] is None and "timeout" in rows[1]["error"]


def test_link_names():
    assert link_name("vless://u@h:80?type=ws#%F0%9F%87%A8%F0%9F%87%A6%20Canada") == "🇨🇦 Canada"
    v = "vmess://" + base64.b64encode(json.dumps({"ps": "Node A", "add": "h"}).encode()).decode()
    assert link_name(v) == "Node A"
    renamed = with_name(v, "Fresh")
    assert link_name(renamed) == "Fresh" and renamed.startswith("vmess://")
    assert with_name("vless://u@h:80?x=1#old", "new name") == "vless://u@h:80?x=1#new%20name"
    assert clean_name("🇨🇦 Canada!!") == "Canada" and clean_name("a" * 50) == "a" * 32


def test_decode_subscription_plain_and_b64():
    plain = b"vless://a@h:1#x\n\nhttps://ignored\ntrojan://p@h:2#y\n"
    assert len(decode_subscription(plain)) == 2
    b64 = base64.b64encode(plain).decode()
    assert len(decode_subscription(b64.encode())) == 2
    assert decode_subscription(b"garbage") == []


def test_sync_upserts_and_emits(client, monkeypatch):
    monkeypatch.setattr(probe, "health", lambda: HEALTH)
    configs.sync()
    r = client.get("/api/configs").json()
    assert [c["name"] for c in r["configs"]] == ["Czech", "Turkey-1"]
    assert r["configs"][0]["status"] == "down" and r["configs"][1]["down_mbps"] == 34.93

    ev = client.get("/api/events?domain=configs").json()["events"]
    assert any("Czech" in e["text"] and "not working" in e["text"] for e in ev)

    # the probe forgets Czech, Turkey comes back up with a new ping
    h2 = {"ok": True, "configs": [dict(HEALTH["configs"][0], ping=500)]}
    monkeypatch.setattr(probe, "health", lambda: h2)
    configs.sync()
    r = client.get("/api/configs").json()
    assert [c["name"] for c in r["configs"]] == ["Turkey-1"] and r["configs"][0]["ping_ms"] == 500
    cid = r["configs"][0]["id"]
    assert len(client.get(f"/api/configs/{cid}/samples").json()["samples"]) >= 1


def test_add_update_delete_go_through_probe(client, monkeypatch):
    calls = []
    monkeypatch.setattr(probe, "add_config", lambda uri, push_url="": calls.append(("add", uri)) or {"ok": True})
    monkeypatch.setattr(probe, "update_config", lambda name, uri="", new_name="": calls.append(("update", name, uri, new_name)) or {"ok": True})
    monkeypatch.setattr(probe, "delete_config", lambda name: calls.append(("delete", name)) or {"ok": True})

    r = client.post("/api/configs", json={"uri": "vless://u@h:443?type=tcp#My Node", "group": "g1"})
    assert r.status_code == 201 and r.json()["name"] == "My Node" and calls[0][1].endswith("#My%20Node")
    assert client.post("/api/configs", json={"uri": "vless://u@h:443#My Node"}).status_code == 400   # exists
    assert client.post("/api/configs", json={"uri": "http://nope"}).status_code == 400

    cid = client.get("/api/configs").json()["configs"][0]["id"]
    assert client.put(f"/api/configs/{cid}", json={"new_name": "Renamed", "tunnel": "fi-ir1", "cdn_port": "8075"}).status_code == 200
    c = client.get("/api/configs").json()["configs"][0]
    assert c["name"] == "Renamed" and c["tunnel"] == "fi-ir1" and c["cdn_port"] == "8075" and calls[-1][0] == "update"
    assert client.put(f"/api/configs/{cid}", json={"cdn_port": "abc"}).status_code == 422
    assert client.delete(f"/api/configs/{cid}").status_code == 200 and calls[-1] == ("delete", "Renamed")
    assert client.get("/api/configs").json()["configs"] == []


def test_probe_settings_mask_and_failover(client, monkeypatch):
    r = client.put("/api/settings/probe", json={"api_url": "http://10.0.0.9:18080", "api_token": "T" * 40, "standby": [{"name": "sb", "api_url": "http://10.0.0.8:18080", "api_token": "S" * 40}]})
    assert r.status_code == 200 and r.json()["has_api_token"] is True and "TTTT" not in r.text and r.json()["standby"][0]["has_token"] is True
    assert client.put("/api/settings/probe", json={"api_url": "nope"}).status_code == 400

    def fake_call(p, method, path, body=None, timeout=30):
        if "10.0.0.9" in p["api_url"]:
            raise RuntimeError("down")
        return {"ok": True, "configs": [1, 2]}

    monkeypatch.setattr(probe, "_call", fake_call)
    assert probe.list_configs() == [1, 2] and probe.active()["active"] == "sb"


def test_subscription_import(client, monkeypatch):
    body = base64.b64encode(b"vless://u@h:1#A\nvless://u@h:2#B\n").decode().encode()

    class R:
        status_code = 200
        content = body

    monkeypatch.setattr(configs.httpx, "get", lambda *a, **k: R())
    monkeypatch.setattr(probe, "add_config", lambda uri, push_url="": {"ok": True})
    p = client.post("/api/subscriptions/preview", json={"name": "grp", "url": "https://sub.example.net/x"}).json()
    assert [n["name"] for n in p["nodes"]] == ["A", "B"]
    r = client.post("/api/subscriptions/import", json={"name": "grp", "url": "https://sub.example.net/x", "picks": ["B"]}).json()
    assert r["added"] == ["B"] and r["skipped"] == []
    assert client.get("/api/configs").json()["configs"][0]["group"] == "grp"
    s = client.get("/api/subscriptions").json()["subscriptions"][0]
    assert s["name"] == "grp" and s["nodes"] == ["B"] and "sub.example.net" == s["url_host"]
    with pytest.raises(ValueError):
        configs.fetch_subscription("ftp://x")
    # Sync of a known group: empty link = the saved one; unknown group needs a link
    seen = []
    monkeypatch.setattr(configs.httpx, "get", lambda url, *a, **k: seen.append(url) or R())
    p = client.post("/api/subscriptions/preview", json={"name": "grp", "url": ""}).json()
    assert seen == ["https://sub.example.net/x"] and [n["exists"] for n in p["nodes"]] == [False, True]
    assert client.post("/api/subscriptions/preview", json={"name": "other", "url": ""}).status_code == 400


def test_probe_install_guards(client, monkeypatch):
    from nikatel.services import probe_install

    # read-only panel refuses
    r = client.post("/api/settings/probe/install?server_id=1")
    assert r.status_code == 400 and "read-only" in r.json()["detail"]

    class S:
        read_only = False

        class data_dir:
            parent = probe_install.SRC.parent / "tests"

        ssh_key = "k"
        known_hosts = "kh"

    monkeypatch.setattr(probe_install, "get_settings", lambda: S())
    r = client.post("/api/settings/probe/install?server_id=1")
    # no Xray in the (fake) engine cache -> 502 with a clear hint
    assert r.status_code == 502 and "Xray" in r.json()["detail"]
    assert sorted(p.name for p in probe_install.SRC.glob("*.py"))[:2] == ["bulk-import.py", "daily-speed.py"]
    assert set(probe_install.UNITS) >= {"nikatel-speed-api.service", "nikatel-probe-manager.service", "nikatel-daily-speed.timer"}
