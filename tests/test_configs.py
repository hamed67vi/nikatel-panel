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
    from nikatel.services import configs as configs_svc

    calls = []
    monkeypatch.setattr(probe, "add_config", lambda uri, push_url="": calls.append(("add", uri)) or {"ok": True})
    monkeypatch.setattr(probe, "update_config", lambda name, uri="", new_name="": calls.append(("update", name, uri, new_name)) or {"ok": True})
    monkeypatch.setattr(probe, "delete_many", lambda names: calls.append(("delete", names[0])) or {n: None for n in names})
    monkeypatch.setattr(configs_svc, "_spawn", lambda fn, *a: fn(*a))      # the probe is told in the background; run it here

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
    monkeypatch.setattr(probe, "add_many", lambda items: {i["name"]: None for i in items})
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


def test_delete_many_is_instant_and_one_bulk_call(client, monkeypatch):
    from nikatel.services import configs as configs_svc

    seen = []
    monkeypatch.setattr(probe, "add_config", lambda uri, push_url="": {"ok": True})
    monkeypatch.setattr(probe, "add_many", lambda items: {i["name"]: None for i in items})
    monkeypatch.setattr(probe, "delete_many", lambda names: seen.append(list(names)) or {n: (None if n != "b" else "locked") for n in names})
    monkeypatch.setattr(configs_svc, "_spawn", lambda fn, *a: fn(*a))

    for n in "abc":
        assert client.post("/api/configs", json={"uri": f"vless://u@h:443#{n}"}).status_code == 201

    ids = [c["id"] for c in client.get("/api/configs").json()["configs"]]
    r = client.post("/api/configs/delete-many", json={"ids": ids})
    assert r.status_code == 200 and r.json()["deleted"] == 3
    assert client.get("/api/configs").json()["configs"] == [] and len(seen) == 1 and sorted(seen[0]) == ["a", "b", "c"]
    ev = client.get("/api/events?domain=configs").json()["events"]
    assert any("could not be deleted" in e["text"] and "locked" in e["text"] for e in ev)
    assert configs_svc._PENDING == set()

def test_a_direct_tcp_config_is_down_only_when_its_port_does_not_open(client, monkeypatch):
    from nikatel.db import session
    from nikatel.models import Config

    health = {"ok": True, "configs": [{"name": "Direct", "slug": "direct", "port": 11000, "status": "down", "ping": 0, "checked_at": 1, "error": "curl: (28) SSL connection timeout", "speed": {}}]}
    monkeypatch.setattr(probe, "health", lambda: health)
    monkeypatch.setattr(probe, "config_uri", lambda name: "vless://u@d.example:443?type=tcp&security=reality#Direct")
    client.post("/api/servers", json={"name": "ir1", "host": "9.9.9.9", "role": "iran"})
    answer = {"v": "REACH d.example:443 1 42\n"}
    monkeypatch.setattr(configs, "run_script", lambda node, name, params=None, timeout=25: answer["v"])

    def status():
        c = client.get("/api/configs").json()["configs"][0]
        return c["status"], c["last_error"]

    configs.sync()                          # the config appears (down)
    configs.sync()                          # its link is read: the port is tried
    configs.sync()                          # the port opens from Iran: not filtered, so not down
    st, err = status()
    assert st == "up" and "port opens from Iran" in err
    assert client.get("/api/configs").json()["configs"][0]["ping_ms"] == 42      # the delay is the connect time

    answer["v"] = "REACH d.example:443 0 0\n"   # now the port does not open: that is a filtered address or a dead server
    with session() as db:
        c = db.query(Config).one()
        c.meta = {**c.meta, "reach": {"ok": True, "at": 1}}      # the old answer has expired
    configs.sync()
    configs.sync()
    st, err = status()
    assert st == "down" and "does not open from Iran" in err


def test_bulk_calls_read_the_probe_answer_the_way_it_is_nested(monkeypatch):
    real = {"results": {"add": [{"name": "A", "ok": True}, {"name": "B", "ok": False, "error": "config 'B' already exists"}], "update": [],
                        "delete": [{"name": "C", "ok": True}, {"name": "D", "ok": False, "error": "not found"}]}, "restarted": False, "ok": True}
    monkeypatch.setattr(probe, "request", lambda *a, **k: real)
    assert probe.add_many([{"uri": "u", "name": "A"}, {"uri": "u", "name": "B"}]) == {"A": None, "B": "config 'B' already exists"}
    assert probe.delete_many(["C", "D"]) == {"C": None, "D": None}          # a name the probe does not know is gone as well


def test_imported_configs_join_the_group_even_when_the_sync_job_registered_them_first(client, monkeypatch):
    from nikatel.db import session
    from nikatel.models import Config

    nodes = [{"name": "N1", "uri": "vless://u@h.example:80?type=ws#N1", "proto": "vless"}, {"name": "N2", "uri": "vless://u@h.example:81?type=ws#N2", "proto": "vless"}]
    monkeypatch.setattr(configs, "fetch_subscription", lambda url: nodes)
    monkeypatch.setattr(probe, "add_many", lambda items: {i["name"]: None for i in items})

    with session() as db:       # N1 was registered by the sync job in the meantime (no group), N2 was already there from an older import without group
        db.add(Config(name="N1", status="unknown", meta={}))
        db.add(Config(name="N2", status="unknown", meta={}))

    r = configs.import_subscription("grp", "https://sub.example.net/x")
    assert sorted(r["added"] + r["skipped"]) == ["N1", "N2"] and r["failed"] == []
    assert {c["name"]: c["group"] for c in client.get("/api/configs").json()["configs"]} == {"N1": "grp", "N2": "grp"}
