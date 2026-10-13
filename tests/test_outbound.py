"""The Outbound section: the pool, the forwards, the gateway state it makes, the check, the API. No server is touched (the gateway functions are replaced)."""
import pytest

from nikatel.config import get_settings
from nikatel.db import session
from nikatel.models import Gateway, Server
from nikatel.services import autopilot, gateway, outbound
from nikatel.services import settings as st

VLESS_A = "vless://11111111-1111-1111-1111-111111111111@198.51.100.7:443?encryption=none&security=tls&type=ws&host=cdn.example.test&path=%2Fws&sni=cdn.example.test#alpha"
VLESS_B = "vless://22222222-2222-2222-2222-222222222222@203.0.113.8:8443?encryption=none&security=none&type=tcp#beta"


@pytest.fixture(autouse=True)
def writable(monkeypatch):
    monkeypatch.setattr(get_settings(), "read_only", False)


def _iran(name="ir1", host="203.0.113.5"):
    with session() as db:
        s = Server(name=name, host=host, role="iran", online=True)
        db.add(s)
        db.flush()
        return s.id


def test_pure_helpers():
    assert outbound.split_links("  vless://a\n\n# a comment\r\nss://b  \n") == ["vless://a", "ss://b"]
    assert outbound.norm("vless://x@h:1?a=b#Name") == outbound.norm("vless://x@h:1?a=b#Other") == "vless://x@h:1?a=b"
    assert outbound.next_port({30000, 30001}) == 30002
    assert outbound.next_port({30000}, start=21079) not in range(gateway.TEST_BASE - 2, gateway.TEST_BASE + 61)           # never a port of the gateway's own test doors
    assert outbound.ports_of([{"listen": 30000, "target_host": "h", "target_port": 443, "name": "n", "enabled": False}]) == [{"port": 30000, "target_host": "h", "target_port": 443, "udp": False, "enabled": False, "note": "n"}]
    rows = [{"key": "a1", "ok": True, "ms": 400, "ip": "1.1.1.1", "country": "DE"}, {"key": "b2", "ok": False, "ms": None, "ip": "", "country": ""}, {"key": "gateway", "ok": True, "ms": 500, "ip": "1.1.1.1", "country": "DE"}]
    sm = outbound.summarize(rows, ["a1", "b2"])
    assert sm["alive"] == 1 and sm["total"] == 2 and sm["gateway_ok"] and sm["exits"]["b2"]["ok"] is False
    assert outbound.verdict(sm) == "degraded" and outbound.verdict(None) == "unknown"
    assert outbound.verdict({**sm, "alive": 2}) == "ok" and outbound.verdict({**sm, "gateway_ok": False}) == "down" and outbound.verdict({**sm, "alive": 0}) == "down"
    assert outbound.urlnote("vless://x#My%20exit") == "My exit" and outbound.urlnote("vless://x") == ""


def test_the_pool_takes_links_reads_them_and_never_lists_them():
    r = outbound.add_configs(f"{VLESS_A}\n{VLESS_B}\n{VLESS_A}\nnot a link\nhttp://example.test/x\n")
    assert len(r["added"]) == 2 and sorted(s["why"] for s in r["skipped"]) == ["already in the pool", "only vless, vmess, trojan and ss links are supported", "this is not a config link"]
    assert {a["info"] for a in r["added"]} == {"vless · 198.51.100.7:443 · ws", "vless · 203.0.113.8:8443 · tcp"}
    s = outbound.state()
    assert len(s["configs"]) == 2 and all("uri" not in c for c in s["configs"]) and "11111111-1111" not in str(s)         # the link (its id) is never in an answer
    assert {c["note"] for c in s["configs"]} == {"alpha", "beta"}
    cid = s["configs"][0]["id"]
    outbound.set_config(cid, enabled=False, note="first exit")
    c = next(c for c in outbound.state()["configs"] if c["id"] == cid)
    assert c["enabled"] is False and c["note"] == "first exit"
    outbound.drop_config(cid)
    assert len(outbound.state()["configs"]) == 1
    with pytest.raises(ValueError):
        outbound.drop_config(cid)
    with pytest.raises(ValueError):
        outbound.add_configs("   \n# only a comment")


def test_the_pool_is_limited(monkeypatch):
    monkeypatch.setattr(outbound, "MAX_CONFIGS", 2)
    links = "\n".join(f"vless://33333333-3333-3333-3333-33333333333{i}@198.51.100.{i}:443?encryption=none&security=none&type=tcp" for i in range(4))
    r = outbound.add_configs(links)
    assert len(r["added"]) == 2 and [s["why"] for s in r["skipped"]] == ["the pool holds at most 2 configs"] * 2


def test_forwards_get_a_port_once_and_keep_it():
    r = outbound.add_routes([{"target_host": "198.51.100.20", "target_port": 443, "name": "n1"}, {"target_host": "198.51.100.21", "target_port": 443}, {"target_host": "198.51.100.20", "target_port": 443}])
    assert r == {"added": 2, "skipped": 1}
    routes = outbound.state()["routes"]
    assert [x["listen"] for x in routes] == [30000, 30001] and routes[1]["name"] == "198.51.100.21:443"
    outbound.drop_routes([routes[0]["id"]])
    outbound.add_routes([{"target_host": "198.51.100.22", "target_port": 80}])
    assert [x["listen"] for x in outbound.state()["routes"]] == [30001, 30000]                 # the freed port is reused, the other one never moved
    outbound.set_route(outbound.state()["routes"][0]["id"], False)
    assert outbound.state()["routes"][0]["enabled"] is False
    with pytest.raises(ValueError):
        outbound.add_routes([{"target_host": "bad host!", "target_port": 1}])
    with pytest.raises(ValueError):
        outbound.add_routes([{"target_host": "198.51.100.23", "target_port": 70000}])


def test_auto_routes_make_node_times_port_forwards():
    r = outbound.auto_routes(["198.51.100.30", "198.51.100.31"], [443, 2053, 2096])
    assert r["added"] == 6 and {x["source"] for x in outbound.state()["routes"]} == {"auto"}
    assert outbound.auto_routes(["198.51.100.30"], [443])["skipped"] == 1
    with pytest.raises(ValueError, match="too many"):
        outbound.auto_routes([f"198.51.100.{i}" for i in range(1, 60)], list(range(1000, 1020)))


def test_gateways_must_be_enabled_iran_servers():
    sid = _iran()
    with session() as db:
        f = Server(name="fo1", host="203.0.113.99", role="foreign", online=True)
        db.add(f)
        db.flush()
        fid = f.id

    with pytest.raises(ValueError):
        outbound.set_gateways([fid])
    outbound.set_gateways([sid, sid])
    assert outbound.state()["servers"][0]["chosen"] is True and st.get("outbound")["gateways"] == [sid]
    with pytest.raises(ValueError):
        outbound.set_mode("fastest")
    outbound.set_mode("fast")
    assert outbound.state()["mode"] == "fast"


def test_desired_is_the_pool_and_the_enabled_forwards():
    outbound.add_configs(f"{VLESS_A}\n{VLESS_B}")
    outbound.add_routes([{"target_host": "198.51.100.40", "target_port": 443}, {"target_host": "198.51.100.41", "target_port": 443}])
    outbound.set_route(outbound.state()["routes"][1]["id"], False)
    cid = next(c["id"] for c in outbound.state()["configs"] if c["note"] == "beta")
    outbound.set_config(cid, enabled=False)
    d = outbound.desired()
    assert d["mode"] == "spread" and len(d["borrowed"]) == 1 and d["borrowed"][0]["uri"] == VLESS_A and len(d["ports"]) == 1 and d["ports"][0]["port"] == 30000
    gateway.validate_state(d)                                                                # the gateway accepts what the outbound hands it
    cfg = gateway.build_config(gateway.validate_state(d))
    bal = cfg["routing"]["balancers"][0]
    assert bal["strategy"] == {"type": "roundRobin"} and bal["fallbackTag"] == "block" and bal["selector"] == ["b-"]       # spread: only the pool, never direct
    fast = gateway.build_config(gateway.validate_state({**d, "mode": "fast"}))["routing"]["balancers"][0]
    auto = gateway.build_config(gateway.validate_state({**d, "mode": "auto"}))["routing"]["balancers"][0]
    assert fast["strategy"] == {"type": "leastPing"} and auto["fallbackTag"] == "direct" and auto["selector"] == ["direct", "b-"]


def _fake_gateway(monkeypatch, results):
    calls = []
    def install(sid):
        calls.append(("install", sid))

        with session() as db:
            g = db.get(Gateway, sid)

            if g is None:
                g = Gateway(server_id=sid)
                db.add(g)

            g.installed = True

        return {"version": "Xray 26.0.0 test"}

    monkeypatch.setattr(gateway, "install", install)
    monkeypatch.setattr(gateway, "apply", lambda sid: calls.append(("apply", sid)) or {"active": True, "ports": [str(p["port"]) for p in gateway.get_state(sid)["ports"]]})
    monkeypatch.setattr(gateway, "test", lambda sid: results)
    monkeypatch.setattr(gateway, "remove", lambda sid: calls.append(("remove", sid)))
    monkeypatch.setattr(autopilot, "_say", lambda aid, line, done=None, ok=None: calls.append(("say", line)))
    monkeypatch.setattr(outbound.telegram, "send_text", lambda text, require_enabled=True: calls.append(("telegram", text)) or (True, ""))
    return calls


def test_apply_installs_sets_the_state_and_checks(monkeypatch):
    sid = _iran()
    outbound.add_configs(f"{VLESS_A}\n{VLESS_B}")
    ids = [c["id"] for c in outbound.state()["configs"]]
    outbound.add_routes([{"target_host": "198.51.100.50", "target_port": 443}])
    outbound.set_gateways([sid])
    results = [{"key": "direct", "ok": False, "ms": None, "ip": "", "country": ""}, {"key": ids[0], "ok": True, "ms": 400, "ip": "192.0.2.1", "country": "DE"},
               {"key": ids[1], "ok": True, "ms": 700, "ip": "192.0.2.2", "country": "FI"}, {"key": "gateway", "ok": True, "ms": 500, "ip": "192.0.2.1", "country": "DE"}]
    calls = _fake_gateway(monkeypatch, results)
    outbound._apply_story(1)
    assert ("install", sid) in calls and ("apply", sid) in calls and any(c[0] == "say" and "2 exit(s), 1 forward(s), mode spread" in c[1] for c in calls)
    assert any(c[0] == "say" and c[1].startswith("Done: 1 of 1") for c in calls)
    with session() as db:
        g = db.get(Gateway, sid)
        assert g.mode == "spread" and len(g.borrowed) == 2 and g.ports[0]["port"] == 30000
    s = outbound.state()
    sv = s["servers"][0]
    assert sv["verdict"] == "ok" and sv["summary"]["alive"] == 2 and sv["installed"] is True
    assert [c["alive"] for c in s["configs"]] == [1, 1] and s["configs"][0]["exit"] == {"ip": "192.0.2.1", "country": "DE"}
    assert not [c for c in calls if c[0] == "telegram"]                                      # a first look that is good says nothing


def test_apply_without_a_config_or_a_server_says_why(monkeypatch):
    calls = _fake_gateway(monkeypatch, [])
    outbound._apply_story(1)
    assert any("No Iran server is chosen" in c[1] for c in calls if c[0] == "say")
    sid = _iran()
    outbound.set_gateways([sid])
    outbound._apply_story(2)
    assert any("no enabled config" in c[1] for c in calls if c[0] == "say") and not [c for c in calls if c[0] == "apply"]


def test_a_change_in_what_the_exits_do_is_told(monkeypatch):
    sid = _iran()
    outbound.add_configs(f"{VLESS_A}\n{VLESS_B}")
    ids = [c["id"] for c in outbound.state()["configs"]]
    outbound.set_gateways([sid])
    with session() as db:
        db.add(Gateway(server_id=sid, installed=True))

    def rows(a, b, gw):
        return [{"key": ids[0], "ok": a, "ms": 400 if a else None, "ip": "", "country": ""}, {"key": ids[1], "ok": b, "ms": 400 if b else None, "ip": "", "country": ""}, {"key": "gateway", "ok": gw, "ms": 500 if gw else None, "ip": "", "country": ""}]

    calls = _fake_gateway(monkeypatch, rows(True, True, True))
    outbound.check()
    assert outbound.state()["servers"][0]["verdict"] == "ok" and not [c for c in calls if c[0] == "telegram"]
    monkeypatch.setattr(gateway, "test", lambda s: rows(True, False, True))
    outbound.check()
    assert outbound.state()["servers"][0]["verdict"] == "degraded" and any("1 of 2 exits answer" in c[1] for c in calls if c[0] == "telegram")
    monkeypatch.setattr(gateway, "test", lambda s: rows(False, False, False))
    outbound.check()
    assert outbound.state()["servers"][0]["verdict"] == "down" and any("no exit answers" in c[1] for c in calls if c[0] == "telegram")
    n = len([c for c in calls if c[0] == "telegram"])
    outbound.check()
    assert len([c for c in calls if c[0] == "telegram"]) == n                                # still down: said once

    def boom(s):
        raise RuntimeError("ssh timed out")

    monkeypatch.setattr(gateway, "test", boom)
    outbound.check()                                                                          # an unreachable server is a result, not a crash
    assert outbound.state()["servers"][0]["summary"]["error"].startswith("ssh timed out")


def test_read_only_refuses_every_change(monkeypatch):
    monkeypatch.setattr(get_settings(), "read_only", True)
    for fn, args in ((outbound.add_configs, (VLESS_A,)), (outbound.set_mode, ("fast",)), (outbound.set_gateways, ([],)), (outbound.add_routes, ([{"target_host": "h", "target_port": 1}],)), (outbound.apply_all, ())):
        with pytest.raises(ValueError, match="read-only"):
            fn(*args)
    assert outbound.state()["read_only"] is True                                              # looking is always allowed


def test_the_api(client, monkeypatch):
    r = client.post("/api/outbound/configs", json={"text": f"{VLESS_A}\nnonsense"})
    assert r.status_code == 200 and len(r.json()["added"]) == 1 and r.json()["skipped"][0]["why"] == "this is not a config link"
    j = client.get("/api/outbound").json()
    assert j["ok"] and j["mode"] == "spread" and len(j["configs"]) == 1 and "uri" not in j["configs"][0] and "11111111" not in client.get("/api/outbound").text
    cid = j["configs"][0]["id"]
    assert client.patch(f"/api/outbound/configs/{cid}", json={"enabled": False}).status_code == 200
    assert client.patch("/api/outbound/configs/nope", json={"enabled": True}).status_code == 400
    assert client.put("/api/outbound/mode", json={"mode": "fast"}).status_code == 200 and client.put("/api/outbound/mode", json={"mode": "turbo"}).status_code == 422
    sid = _iran()
    assert client.put("/api/outbound/gateways", json={"server_ids": [sid]}).status_code == 200 and client.put("/api/outbound/gateways", json={"server_ids": [9999]}).status_code == 400
    assert client.post("/api/outbound/routes", json={"routes": [{"target_host": "198.51.100.60", "target_port": 443}]}).json() == {"ok": True, "added": 1, "skipped": 0}
    assert client.post("/api/outbound/routes/auto", json={"addresses": ["198.51.100.61"], "ports": [80, 443]}).json()["added"] == 2
    rid = client.get("/api/outbound").json()["routes"][0]["id"]
    assert client.patch(f"/api/outbound/routes/{rid}", json={"enabled": False}).status_code == 200
    assert client.post("/api/outbound/routes/drop", json={"ids": [rid]}).json()["removed"] == 1
    calls = _fake_gateway(monkeypatch, [])
    assert client.post("/api/outbound/apply").status_code == 202
    assert client.post(f"/api/outbound/servers/{sid}/remove").json()["ok"] is True and ("remove", sid) in calls
    assert client.delete(f"/api/outbound/configs/{cid}").json()["ok"] is True
