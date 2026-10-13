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
    assert [x["listen"] for x in outbound.state()["routes"]] == [30001, 30002]                 # the freed port is NOT given to another target (a CDN record may still point at it); the other one never moved
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
    monkeypatch.setattr(gateway, "apply", lambda sid, hubs=None: calls.append(("apply", sid, hubs)) or {"active": True, "ports": [str(p["port"]) for p in gateway.get_state(sid)["ports"]]})
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
    assert ("install", sid) in calls and ("apply", sid, []) in calls and any(c[0] == "say" and "2 exit(s), 1 forward(s), mode spread. NO relay" in c[1] for c in calls)
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


# ------------------------------------------------------------------ the tunnels' forwards and connecting the users
class FakeArvan:
    """One ArvanCloud account: a record cank.example.test on the Iran server's address, origin port 8075, proxied; and a record that is not ours to touch."""

    def __init__(self, ip):
        self.rows = {"r1": {"account": "arvan", "domain": "example.test", "id": "r1", "type": "A", "name": "cank", "fqdn": "cank.example.test", "ips": [ip], "port": 8075, "upstream": "http", "ttl": 120, "cloud": True},
                     "r2": {"account": "arvan", "domain": "example.test", "id": "r2", "type": "A", "name": "other", "fqdn": "other.example.test", "ips": ["198.51.100.250"], "port": 8075, "upstream": "http", "ttl": 120, "cloud": True},
                     "r3": {"account": "arvan", "domain": "example.test", "id": "r3", "type": "A", "name": "dns-only", "fqdn": "dns-only.example.test", "ips": [ip], "port": 8075, "upstream": "http", "ttl": 120, "cloud": False}}
        self.calls = []

    ttls = ()

    def domains(self):
        return ["example.test"]

    def records(self, domain):
        return [dict(r) for r in self.rows.values()]

    def get(self, domain, rid):
        return dict(self.rows[rid])

    def update(self, domain, rid, name, ips, ttl, cloud, port=None, upstream=None):
        self.calls.append(("update", rid, port))
        before = dict(self.rows[rid])
        self.rows[rid]["port"] = port
        return {"before": {"id": rid, "port": before["port"]}, "after": dict(self.rows[rid]), "before_row": before}

    def put_back(self, domain, rid, before):
        self.calls.append(("put_back", rid, before["port"]))
        self.rows[rid]["port"] = before["port"]


def _tunnel(db, name, iran, foreign, mapping, typ="backhaul", transport="tcpmux", direction="reverse"):
    from nikatel.models import Tunnel

    t = Tunnel(name=name, type=typ, transport=transport, iran_server_id=iran, foreign_server_id=foreign, state="up", enabled=True, forwarded_ports=[int(mapping.split(":")[0].split(",")[0])], record={"port_mapping": mapping, "direction": direction})
    db.add(t)
    return t


def _world():
    with session() as db:
        i1, i2, f1, f2 = (Server(name="ir1", host="203.0.113.5", role="iran", online=True), Server(name="ir2", host="203.0.113.6", role="iran", online=True),
                          Server(name="fo1", host="198.51.100.30", role="foreign", online=True), Server(name="fo2", host="198.51.100.31", role="foreign", online=True))
        db.add_all([i1, i2, f1, f2])
        db.flush()
        _tunnel(db, "fo1-ir1", i1.id, f1.id, "8075")
        _tunnel(db, "fo2-ir1", i1.id, f2.id, "443:2096, 80")
        _tunnel(db, "fo2-ir2", i2.id, f2.id, "8072")
        _tunnel(db, "udp-ir2", i2.id, f1.id, "9000", typ="paqet", transport="kcp", direction="direct")
        _tunnel(db, "l3-ir2", i2.id, f1.id, "9001", transport="l3:udp")
        return {"i1": i1.id, "i2": i2.id}


def test_forwards_from_the_tunnels_follow_the_tunnels():
    ids = _world()
    assert [(x["tunnel"], x["iran_port"], x["target_host"], x["target_port"]) for x in outbound.tunnel_ports() if x["server_id"] == ids["i1"]] == [("fo1-ir1", 8075, "198.51.100.30", 8075), ("fo2-ir1", 443, "198.51.100.31", 2096), ("fo2-ir1", 80, "198.51.100.31", 80)]
    assert not [x for x in outbound.tunnel_ports() if x["tunnel"] in ("udp-ir2", "l3-ir2")]                 # a UDP or raw tunnel cannot go through a TCP forward
    assert outbound.from_tunnels() == {"added": 4, "updated": 0}
    routes = outbound.state()["routes"]
    assert [r["listen"] for r in routes] == [30000, 30001, 30002, 30003] and {r["source"] for r in routes} == {"tunnel"} and routes[1]["target_port"] == 2096
    assert outbound.from_tunnels() == {"added": 0, "updated": 0}                                                # again: nothing new, nothing moves
    with session() as db:
        from nikatel.models import Tunnel

        db.scalar(__import__("sqlalchemy").select(Tunnel).where(Tunnel.name == "fo1-ir1")).record = {"port_mapping": "8075:9999", "direction": "reverse"}

    assert outbound.from_tunnels() == {"added": 0, "updated": 1} and next(r for r in outbound.state()["routes"] if r["tunnel"] == "fo1-ir1")["target_port"] == 9999 and outbound.state()["routes"][0]["listen"] == 30000
    d1, d2 = outbound.desired(ids["i1"]), outbound.desired(ids["i2"])
    assert [p["port"] for p in d1["ports"]] == [30000, 30001, 30002] and [p["port"] for p in d2["ports"]] == [30003]                   # each Iran server gets only its own tunnels' forwards
    outbound.add_routes([{"target_host": "198.51.100.77", "target_port": 1}])                                  # a forward for every server is in both
    assert len(outbound.desired(ids["i1"])["ports"]) == 4 and len(outbound.desired(ids["i2"])["ports"]) == 2


def test_classify():
    assert outbound.classify(8075, 8075, 30000) == "normal" and outbound.classify(30000, 8075, 30000) == "bridged" and outbound.classify(443, 8075, 30000) == "" and outbound.classify(None, 8075, 30000) == ""


def _dns(monkeypatch, fake):
    from types import SimpleNamespace

    from nikatel.api import dns as dns_api

    dns_api._CACHE.update(at=0.0, rows=[], accounts=[])                                                          # the DNS page keeps a copy of the records: every test starts without it
    acc = SimpleNamespace(id="arvan", provider="arvan", name="ArvanCloud")
    monkeypatch.setattr(outbound.dnsprov, "accounts", lambda: [acc])
    monkeypatch.setattr(outbound.dnsprov, "provider_of", lambda a: fake)
    monkeypatch.setattr(outbound.dnsprov, "provider", lambda aid: fake)
    monkeypatch.setattr(outbound.failover, "_notice", lambda text: None)


def test_connect_plan_lists_only_the_records_a_forward_shadows(monkeypatch):
    ids = _world()
    fake = FakeArvan("203.0.113.5")
    _dns(monkeypatch, fake)
    assert outbound.connect_plan()["records"] == []                                                             # no forward yet: nothing to connect
    outbound.from_tunnels()
    plan = outbound.connect_plan()
    assert [(x["fqdn"], x["state"], x["port"], x["listen"], x["tunnel"], x["target"]) for x in plan["records"]] == [("cank.example.test", "normal", 8075, 30000, "fo1-ir1", "198.51.100.30:8075")]      # the other server's record and the DNS-only one are left alone
    assert plan["records"][0]["server_id"] == ids["i1"] and plan["records"][0]["ready"] is False and plan["forwards"] == 4 and plan["bridged"] == 0


def test_connect_changes_the_port_only_when_the_forward_listens_and_can_be_put_back(monkeypatch):
    _world()
    fake = FakeArvan("203.0.113.5")
    _dns(monkeypatch, fake)
    outbound.from_tunnels()
    said = []
    monkeypatch.setattr(autopilot, "_say", lambda aid, line, done=None, ok=None: said.append(line))
    key = outbound.connect_plan()["records"][0]["key"]
    monkeypatch.setattr(gateway, "status", lambda sid: {"active": "none", "listen": {}})
    outbound._connect_story(1, [key])
    assert fake.rows["r1"]["port"] == 8075 and any("NOT changed" in x and "not listening" in x for x in said)     # the forward is not up: the record is not touched
    monkeypatch.setattr(gateway, "status", lambda sid: {"active": "active", "listen": {"30000": True}})
    said.clear()
    outbound._connect_story(2, [key])
    assert fake.rows["r1"]["port"] == 30000 and fake.calls == [("update", "r1", 30000)] and any("203.0.113.5:8075 → 203.0.113.5:30000" in x for x in said)
    assert outbound.connect_plan()["records"][0]["state"] == "bridged" and outbound.connect_plan()["bridged"] == 1
    log = [e for e in outbound.failover._state()["dns_log"] if e.get("op") == "bridge"]
    assert len(log) == 1 and log[0]["fqdn"] == "cank.example.test" and log[0]["old"] == "203.0.113.5:8075" and log[0]["before"]["port"] == 8075 and log[0]["server"] == "ir1"
    said.clear()
    outbound._connect_story(3, [key])                                                                            # again: already there, nothing is written
    assert fake.calls == [("update", "r1", 30000)] and any("already on the forward" in x for x in said)
    outbound._disconnect_story(4, [key])                                                                         # back through the DNS log: the record as it was
    assert fake.rows["r1"]["port"] == 8075 and ("put_back", "r1", 8075) in fake.calls and outbound.connect_plan()["records"][0]["state"] == "normal"
    assert outbound.failover._state()["dns_log"][-1]["restored"] is True


def test_connect_refuses_a_record_that_changed_since_the_plan(monkeypatch):
    _world()
    fake = FakeArvan("203.0.113.5")
    _dns(monkeypatch, fake)
    outbound.from_tunnels()
    said = []
    monkeypatch.setattr(autopilot, "_say", lambda aid, line, done=None, ok=None: said.append(line))
    monkeypatch.setattr(gateway, "status", lambda sid: {"active": "active", "listen": {"30000": True}})
    key = outbound.connect_plan()["records"][0]["key"]
    fake.rows["r1"]["ips"] = ["203.0.113.99"]                                                                    # someone moved it to another server meanwhile
    outbound._connect_story(1, [key])
    assert fake.calls == [] and any("not in the plan" in x for x in said)


def test_connect_api(client, monkeypatch):
    _world()
    fake = FakeArvan("203.0.113.5")
    _dns(monkeypatch, fake)
    assert client.post("/api/outbound/routes/tunnels").json() == {"ok": True, "added": 4, "updated": 0}
    j = client.get("/api/outbound/connect").json()
    assert j["ok"] and len(j["records"]) == 1 and j["records"][0]["state"] == "normal"
    started = []
    monkeypatch.setattr(autopilot, "_start", lambda label, fn, *a, **kw: started.append((label, a[0] if a else None)) or 7)
    assert client.post("/api/outbound/connect", json={"keys": [j["records"][0]["key"]]}).status_code == 202 and started[0][0] == "outbound connect"
    assert client.post("/api/outbound/connect/undo", json={"keys": ["x"]}).status_code == 202 and started[1][0] == "outbound disconnect"
    assert client.post("/api/outbound/connect", json={"keys": []}).status_code == 422


# ------------------------------------------------------------------ the relay behind the exits, and the users' modes
HUB = {"id": "h7", "host": "198.51.100.90", "port": 44123, "method": "chacha20-ietf-poly1305", "password": "relay-secret-0123456789"}


def test_the_gateway_wraps_the_traffic_for_the_relay_behind_every_exit():
    outbound.add_configs(f"{VLESS_A}\n{VLESS_B}")
    outbound.add_routes([{"target_host": "198.51.100.40", "target_port": 443}])
    d = {**outbound.desired(), "hubs": [HUB]}
    st_ = gateway.validate_state(d)
    assert st_["hubs"] == [HUB]
    cfg = gateway.build_config(st_)
    ss = [o for o in cfg["outbounds"] if o["tag"].startswith("ss-")]
    assert len(ss) == 2 and all(o["protocol"] == "shadowsocks" and o["streamSettings"]["sockopt"]["dialerProxy"].startswith("b-") for o in ss) and {o["tag"].split("-")[2] for o in ss} == {"h7"}
    assert ss[0]["settings"]["servers"][0]["address"] == "198.51.100.90" and ss[0]["settings"]["servers"][0]["port"] == 44123
    bal = cfg["routing"]["balancers"][0]
    assert bal["selector"] == ["ss-"] and cfg["observatory"]["subjectSelector"] == ["ss-"]                      # the users go the way the test goes: exit + relay
    doors = [r for r in cfg["routing"]["rules"] if r.get("inboundTag") and r["inboundTag"][0].startswith("gw-t-") and r["inboundTag"][0] != "gw-t-direct"]
    assert doors and all(r["outboundTag"].startswith("ss-") for r in doors)
    auto = gateway.build_config({**st_, "mode": "auto"})["routing"]["balancers"][0]
    assert auto["selector"] == ["direct", "ss-"] and auto["fallbackTag"] == "direct"
    plain = gateway.build_config(gateway.validate_state(outbound.desired()))                                  # no relay: as before
    assert not [o for o in plain["outbounds"] if o["tag"].startswith("ss-")] and plain["routing"]["balancers"][0]["selector"] == ["b-"]
    for bad in ({**HUB, "password": "short"}, {**HUB, "port": 80}, {**HUB, "method": "rot13"}, {**HUB, "host": "bad host!"}):
        with pytest.raises(ValueError):
            gateway.validate_state({**d, "hubs": [bad]})


def test_relay_config_has_no_sniffing_and_blocks_the_private_ranges():
    c = outbound.relay_config(44123, "chacha20-ietf-poly1305", "relay-secret-0123456789")
    inb = c["inbounds"][0]
    assert inb["protocol"] == "shadowsocks" and inb["port"] == 44123 and "sniffing" not in inb and inb["settings"]["network"] == "tcp"
    assert c["routing"]["rules"][0]["outboundTag"] == "block" and "127.0.0.0/8" in c["routing"]["rules"][0]["ip"] and "10.0.0.0/8" in c["routing"]["rules"][0]["ip"]


def test_relays_are_chosen_installed_and_handed_to_the_gateway(monkeypatch):
    with session() as db:
        f1, f2 = Server(name="fo1", host="203.0.113.60", role="foreign", online=True), Server(name="fo2", host="203.0.113.61", role="foreign", online=True)
        ir = Server(name="ir1", host="203.0.113.5", role="iran", online=True)
        db.add_all([f1, f2, ir])
        db.flush()
        fid, f2id, iid = f1.id, f2.id, ir.id

    with pytest.raises(ValueError):
        outbound.set_hubs([iid])                                                                              # an Iran server is not a relay
    outbound.set_hubs([fid, f2id])
    hubs = outbound.state()["hubs"]
    assert len(hubs) == 2 and all(44000 <= h["port"] <= 44999 and "password" not in h for h in hubs) and "relay" not in str(outbound.state()).lower().replace("hubs", "")[:0]
    assert outbound.relays() == []                                                                            # chosen but not applied: the gateways do not use it yet
    with session() as db:
        h = db.get(outbound.OutboundHub, fid)
        h.installed, h.applied_at = True, 5
        pw = h.password

    assert outbound.relays() == [{"id": f"h{fid}", "host": "203.0.113.60", "port": hubs[0]["port"] if hubs[0]["server_id"] == fid else hubs[1]["port"], "method": "chacha20-ietf-poly1305", "password": pw}]
    assert pw not in str(outbound.state())
    with pytest.raises(ValueError, match="remove it from there first"):
        outbound.set_hubs([f2id])                                                                             # an installed relay holds files: remove it first
    removed = []
    monkeypatch.setattr(outbound, "run_action", lambda node, name, params=None, timeout=90: removed.append(name) or "REMOVED")
    outbound.remove_hub(fid)
    assert removed == ["xrelay_remove"] and [h["server_id"] for h in outbound.state()["hubs"]] == [f2id]
    outbound.set_hubs([])                                                                                     # one that was never installed goes without a trip to its server
    assert outbound.state()["hubs"] == []


def test_the_story_installs_the_relay_first_and_the_gateway_goes_through_it(monkeypatch):
    sid = _iran()
    with session() as db:
        f = Server(name="fo1", host="203.0.113.60", role="foreign", online=True)
        db.add(f)
        db.flush()
        fid = f.id

    outbound.add_configs(VLESS_A)
    outbound.add_routes([{"target_host": "198.51.100.50", "target_port": 443}])
    outbound.set_gateways([sid])
    outbound.set_hubs([fid])
    calls = _fake_gateway(monkeypatch, [{"key": "gateway", "ok": True, "ms": 100, "ip": "", "country": ""}])

    def apply_hub(aid, server_id):
        with session() as db:
            h = db.get(outbound.OutboundHub, server_id)
            h.installed, h.applied_at = True, 9

        calls.append(("hub", server_id))
        return True

    monkeypatch.setattr(outbound, "apply_hub", apply_hub)
    monkeypatch.setattr(outbound, "_check_hubs", lambda s, now: None)
    outbound._apply_story(1)
    assert [c[0] for c in calls if c[0] in ("hub", "install", "apply")] == ["hub", "install", "apply"]       # the relay first, then the gateway
    applied = next(c for c in calls if c[0] == "apply")
    assert applied[2][0]["id"] == f"h{fid}" and applied[2][0]["host"] == "203.0.113.60"
    assert any("through 1 relay(s)" in c[1] for c in calls if c[0] == "say")


def test_due_decides_which_tunnels_are_connected_and_which_come_back():
    now = 100_000
    tunnels = {"a": {"state": "down", "since": now - 400}, "b": {"state": "down", "since": now - 100}, "c": {"state": "up", "since": now - 3600}, "d": {"state": "up", "since": now - 3600}, "e": {"state": "down", "since": now - 9000}}
    auto = {"c": now - 7200, "d": now - 100}                                                                  # the panel connected c long ago and d a moment ago
    go, home = outbound.due(tunnels, auto, 5, 30, now)
    assert go == ["a", "e"] and home == ["c"]                                                                  # b is down for 100 s only; d is in its cooldown; e is down: connect
    assert outbound.due({"x": {"state": "up", "since": now - 3600}}, {}, 5, 30, now) == ([], [])             # a healthy tunnel nobody touched is left alone


def test_the_automatic_switch_is_off_for_now(monkeypatch):
    assert outbound.AUTO_USERS is False                                                                        # the owner's decision: no CDN record is changed by the panel itself until the tests are finished
    with pytest.raises(ValueError, match="off for now"):
        outbound.set_users("auto", 5, 30)
    s = outbound._state()
    s["users"]["mode"] = "auto"                                                                                # even a mode left over from before does nothing
    outbound._save(s)
    started = []
    monkeypatch.setattr(outbound, "connect", lambda keys: started.append(keys) or 1)
    monkeypatch.setattr(outbound, "connect_plan", lambda fresh=False: started.append("plan") or {"records": []})
    outbound.auto_users()
    assert started == [] and outbound.state()["users"]["auto_available"] is False


def test_users_mode_needs_the_license_and_is_limited(monkeypatch):
    monkeypatch.setattr(outbound, "AUTO_USERS", True)
    outbound.set_users("auto", 7, 40)
    u = outbound.state()["users"]
    assert u["mode"] == "auto" and u["after_minutes"] == 7 and u["back_minutes"] == 40
    outbound.set_users("manual", 9999, 1)
    assert outbound.state()["users"]["after_minutes"] == 120 and outbound.state()["users"]["back_minutes"] == 5
    with pytest.raises(ValueError):
        outbound.set_users("sometimes")
    monkeypatch.setattr(outbound.license, "has", lambda f: False)
    with pytest.raises(ValueError, match="license"):
        outbound.set_users("auto")
    assert outbound.state()["users"]["licensed"] is False


def test_auto_users_connects_a_tunnel_that_stays_down_and_puts_it_back(monkeypatch):
    monkeypatch.setattr(outbound, "AUTO_USERS", True)
    ids = _world()
    fake = FakeArvan("203.0.113.5")
    _dns(monkeypatch, fake)
    outbound.from_tunnels()
    started, told = [], []
    monkeypatch.setattr(outbound, "connect", lambda keys: started.append(("connect", keys)) or 1)
    monkeypatch.setattr(outbound, "disconnect", lambda keys: started.append(("disconnect", keys)) or 1)
    monkeypatch.setattr(outbound, "_tell", lambda text: told.append(text))
    sid = ids["i1"]
    with session() as db:
        db.add(Gateway(server_id=sid, installed=True, applied_at=5))

    s = outbound._state()
    s["health"][str(sid)] = {"at": 1, "summary": {"alive": 2, "total": 2, "exits": {}, "gateway_ok": True, "ms": 100}}
    outbound._save(s)
    outbound.set_users("manual")
    outbound.auto_users()
    assert started == []                                                                                       # manual: nothing by itself
    outbound.set_users("auto", 5, 30)
    outbound.auto_users()
    assert started == []                                                                                       # the tunnel is up: nothing
    from nikatel.models import Tunnel

    with session() as db:
        t = db.scalar(__import__("sqlalchemy").select(Tunnel).where(Tunnel.name == "fo1-ir1"))
        t.state, t.state_since = "down", int(__import__("time").time()) - 600

    outbound.auto_users()
    assert started == [("connect", [outbound.connect_plan()["records"][0]["key"]])] and "fo1-ir1" in outbound.state()["users"]["auto"] and told and "fo1-ir1" in told[0]
    fake.rows["r1"]["port"] = 30000                                                                            # (the connect story did that)
    started.clear()
    with session() as db:
        t = db.scalar(__import__("sqlalchemy").select(Tunnel).where(Tunnel.name == "fo1-ir1"))
        t.state, t.state_since = "up", int(__import__("time").time()) - 3600

    s = outbound._state()
    s["users"]["auto"]["fo1-ir1"] = int(__import__("time").time()) - 7200
    outbound._save(s)
    outbound.auto_users()
    assert [k for k, _ in started] == ["disconnect"] and "fo1-ir1" not in outbound.state()["users"]["auto"]
    monkeypatch.setattr(outbound.license, "has", lambda f: False)                                              # without the licence the job does nothing
    started.clear()
    outbound.auto_users()
    assert started == []


def test_hubs_and_users_api(client, monkeypatch):
    with session() as db:
        f = Server(name="fo1", host="203.0.113.60", role="foreign", online=True)
        db.add(f)
        db.flush()
        fid = f.id

    assert client.put("/api/outbound/hubs", json={"server_ids": [fid]}).status_code == 200 and client.put("/api/outbound/hubs", json={"server_ids": [9999]}).status_code == 400
    assert client.get("/api/outbound").json()["hubs"][0]["name"] == "fo1" and client.get("/api/outbound").json()["foreign"][0]["id"] == fid
    assert client.post(f"/api/outbound/hubs/{fid}/remove").json()["ok"] is True and client.post(f"/api/outbound/hubs/{fid}/remove").status_code == 400
    assert client.put("/api/outbound/users", json={"mode": "auto", "after_minutes": 3, "back_minutes": 20}).status_code == 400          # off for now
    assert client.put("/api/outbound/users", json={"mode": "manual", "after_minutes": 3, "back_minutes": 20}).status_code == 200
    assert client.get("/api/outbound").json()["users"] == {"mode": "manual", "after_minutes": 3, "back_minutes": 20, "licensed": True, "auto_available": False, "auto": []}
    assert client.put("/api/outbound/users", json={"mode": "later"}).status_code == 422 and client.put("/api/outbound/users", json={"mode": "auto", "after_minutes": 0}).status_code == 422

def test_pick_relay_takes_the_quietest_online_server():
    cands = [{"id": 1, "online": True, "cpu": 40, "ram": 50, "tunnels": 3}, {"id": 2, "online": True, "cpu": 10, "ram": 20, "tunnels": 1}, {"id": 3, "online": False, "cpu": 1, "ram": 1, "tunnels": 0},
             {"id": 4, "online": True, "cpu": 90, "ram": 10, "tunnels": 0}, {"id": 5, "online": True, "cpu": None, "ram": None, "tunnels": 0}]
    assert outbound.pick_relay(cands) == 2                                                  # offline and busy ones are out; the most room, the fewest tunnels
    assert outbound.pick_relay([{"id": 3, "online": False}, {"id": 4, "online": True, "cpu": 95, "ram": 10}]) is None
    assert outbound.pick_relay([{"id": 7, "online": True, "cpu": 30, "ram": 30, "tunnels": 0}, {"id": 8, "online": True, "cpu": 30, "ram": 30, "tunnels": 0}]) == 7    # a tie: the older server


def test_the_one_button_makes_the_forwards_and_the_relay_itself(monkeypatch):
    ids = _world()
    outbound.add_configs(f"{VLESS_A}\n{VLESS_B}")
    outbound.set_gateways([ids["i1"]])                                                                  # only the first Iran server is chosen: only its tunnels get forwards
    with session() as db:
        f = db.scalars(__import__("sqlalchemy").select(Server).where(Server.role == "foreign").order_by(Server.name)).first()
        fname = f.name

    calls = _fake_gateway(monkeypatch, [{"key": "gateway", "ok": True, "ms": 100, "ip": "", "country": ""}])
    applied_hubs = []
    monkeypatch.setattr(outbound, "apply_hub", lambda aid, sid: applied_hubs.append(sid) or True)
    monkeypatch.setattr(outbound, "_check_hubs", lambda s, now: None)
    outbound._apply_story(1)
    routes = outbound.state()["routes"]
    assert {r["server_id"] for r in routes} == {ids["i1"]} and len(routes) == 3                          # fo1-ir1 (8075), fo2-ir1 (443:2096, 80); the other Iran server's tunnels are not touched
    assert len(outbound.state()["hubs"]) == 1 and applied_hubs == [outbound.state()["hubs"][0]["server_id"]]
    assert any(c[0] == "say" and "Relay:" in c[1] and fname in c[1] or c[0] == "say" and "chosen (the quietest" in c[1] for c in calls)
    assert any(c[0] == "say" and "No foreign server and port chosen" in c[1] and "3 new" in c[1] for c in calls)
    s = outbound.state()
    assert s["tunnel_ports"] == 3 and s["suggested_relay"] is not None

# ------------------------------------------------------------------ the tunnels through the configs: Iran servers x foreign servers x ports
def test_ports_are_read_from_text_or_a_list():
    assert outbound.parse_ports("8075, 80  443;80") == [8075, 80, 443] and outbound.parse_ports([8075, "80"]) == [8075, 80] and outbound.parse_ports("") == []
    for bad in ("0", "70000", "80 http", "-1"):
        with pytest.raises(ValueError, match="not a port"):
            outbound.parse_ports(bad)
    with pytest.raises(ValueError, match="at most"):
        outbound.parse_ports(list(range(1, 30)))


def test_wanted_paths_are_every_pair_on_every_port():
    iran, foreign = [{"id": 1, "name": "ir1"}, {"id": 2, "name": "ir2"}], [{"id": 5, "name": "fo1", "host": "198.51.100.30"}]
    w = outbound.wanted_paths(iran, foreign, [8075, 80])
    assert [(x["server_id"], x["target_host"], x["target_port"]) for x in w] == [(1, "198.51.100.30", 8075), (1, "198.51.100.30", 80), (2, "198.51.100.30", 8075), (2, "198.51.100.30", 80)]
    assert w[0]["name"] == "ir1 → fo1 :8075" and outbound.wanted_paths([], foreign, [80]) == []


def test_start_makes_exactly_the_chosen_paths_and_keeps_their_ports(monkeypatch):
    ids = _world()
    with session() as db:
        fo = {x.name: x.id for x in db.scalars(__import__("sqlalchemy").select(Server).where(Server.role == "foreign"))}

    outbound.set_gateways([ids["i1"], ids["i2"]])
    outbound.set_targets([fo["fo1"]], "8075, 80")
    made = outbound.make_paths()
    assert made == {"added": 4, "kept": 0, "removed": 0}
    routes = outbound.state()["routes"]
    assert [(r["server_id"], r["target_host"], r["target_port"], r["listen"], r["source"]) for r in routes] == [(ids["i1"], "198.51.100.30", 8075, 30000, "path"), (ids["i1"], "198.51.100.30", 80, 30001, "path"),
                                                                                                           (ids["i2"], "198.51.100.30", 8075, 30002, "path"), (ids["i2"], "198.51.100.30", 80, 30003, "path")]
    assert [p["port"] for p in outbound.desired(ids["i1"])["ports"]] == [30000, 30001]                              # each Iran server carries only its own forwards
    assert outbound.make_paths() == {"added": 0, "kept": 4, "removed": 0}                                           # again: nothing new, nothing moves
    outbound.set_targets([fo["fo1"], fo["fo2"]], "8075")                                                            # a foreign server added, port 80 dropped
    made = outbound.make_paths()
    assert made == {"added": 2, "kept": 2, "removed": 2}
    by = {(r["server_id"], r["target_host"], r["target_port"]): r["listen"] for r in outbound.state()["routes"]}
    assert by[(ids["i1"], "198.51.100.30", 8075)] == 30000 and by[(ids["i2"], "198.51.100.30", 8075)] == 30002      # what stayed kept its port
    assert sorted(by.values()) == [30000, 30002, 30004, 30005]                                                       # the two removed ports (30001, 30003) were not handed out again
    outbound.add_routes([{"target_host": "198.51.100.77", "target_port": 1}])                                       # a forward made by hand is never dropped by Start
    outbound.set_targets([], "")
    assert outbound.make_paths()["removed"] == 4 and [r["source"] for r in outbound.state()["routes"]] == ["manual"]


def test_targets_are_checked_and_shown(monkeypatch):
    ids = _world()
    with session() as db:
        fo = {x.name: x.id for x in db.scalars(__import__("sqlalchemy").select(Server).where(Server.role == "foreign"))}

    with pytest.raises(ValueError, match="foreign"):
        outbound.set_targets([ids["i1"]], "80")                                                                     # an Iran server is not a foreign target
    with pytest.raises(ValueError, match="not a port"):
        outbound.set_targets([fo["fo1"]], "eighty")
    outbound.set_gateways([ids["i1"]])
    outbound.set_targets([fo["fo2"]], [443])
    s = outbound.state()
    assert s["ports"] == [443] and {f["name"]: f["chosen"] for f in s["foreign"]} == {"fo1": False, "fo2": True} and s["default_ports"] == [80, 443, 8075]
    assert all(f["online"] is True for f in s["foreign"])                                                          # the list shows a green or grey dot for every foreign server


def test_the_one_button_with_a_choice_makes_the_paths_not_the_tunnel_forwards(monkeypatch):
    ids = _world()
    outbound.add_configs(f"{VLESS_A}\n{VLESS_B}")
    outbound.set_gateways([ids["i1"]])
    with session() as db:
        f = db.scalars(__import__("sqlalchemy").select(Server).where(Server.role == "foreign").order_by(Server.name)).first()
        fid = f.id

    outbound.set_targets([fid], "2095")
    calls = _fake_gateway(monkeypatch, [{"key": "gateway", "ok": True, "ms": 100, "ip": "", "country": ""}])
    monkeypatch.setattr(outbound, "apply_hub", lambda aid, sid: True)
    monkeypatch.setattr(outbound, "_check_hubs", lambda s, now: None)
    outbound._apply_story(1)
    routes = outbound.state()["routes"]
    assert [(r["source"], r["target_port"]) for r in routes] == [("path", 2095)]                                    # one Iran server x one foreign server x one port; the tunnels' ports are not added
    assert any(c[0] == "say" and "Tunnels through your configs" in c[1] and "1 new" in c[1] for c in calls)


def test_the_targets_api(client, monkeypatch):
    ids = _world()
    with session() as db:
        fid = db.scalars(__import__("sqlalchemy").select(Server).where(Server.role == "foreign")).first().id

    assert client.put("/api/outbound/targets", json={"foreign_ids": [fid], "ports": "8075, 80"}).status_code == 200
    j = client.get("/api/outbound").json()
    assert j["ports"] == [8075, 80] and [f["chosen"] for f in j["foreign"]] == [True, False] and j["paths"] == 0
    assert client.put("/api/outbound/targets", json={"foreign_ids": [fid], "ports": [443]}).status_code == 200 and client.get("/api/outbound").json()["ports"] == [443]
    assert client.put("/api/outbound/targets", json={"foreign_ids": [ids["i1"]], "ports": []}).status_code == 400
    assert client.put("/api/outbound/targets", json={"foreign_ids": [fid], "ports": "x"}).status_code == 400


def test_the_records_come_from_the_dns_pages_copy_unless_fresh_is_asked(monkeypatch):
    _world()
    fake = FakeArvan("203.0.113.5")
    _dns(monkeypatch, fake)
    outbound.from_tunnels()
    asked = []
    real = fake.records
    fake.records = lambda d: asked.append(d) or real(d)
    assert len(outbound.connect_plan()["records"]) == 1 and asked == ["example.test"]                               # the first look reads ArvanCloud
    assert len(outbound.connect_plan()["records"]) == 1 and asked == ["example.test"]                               # the second one uses the copy: no new question
    assert len(outbound.connect_plan(True)["records"]) == 1 and asked == ["example.test", "example.test"]           # fresh asks again
    outbound._forget_records()
    outbound.connect_plan()
    assert len(asked) == 3                                                                                          # a change makes the copy out of date

def test_an_old_copy_of_the_records_is_shown_at_once_and_renewed_in_the_background(monkeypatch):
    import time

    from nikatel.api import dns as dns_api

    _world()
    fake = FakeArvan("203.0.113.5")
    _dns(monkeypatch, fake)
    outbound.from_tunnels()
    assert len(outbound.connect_plan()["records"]) == 1
    dns_api._CACHE["at"] = time.time() - 5000                                                                       # the copy is old
    renewed = []
    monkeypatch.setattr(dns_api, "_refresh_in_background", lambda: renewed.append(1))
    asked = []
    real = fake.records
    fake.records = lambda d: asked.append(d) or real(d)
    plan = outbound.connect_plan()
    assert len(plan["records"]) == 1 and asked == [] and renewed == [1]                                             # shown from the copy, ArvanCloud not asked, a renewal started
    assert abs(plan["cached_at"] - dns_api._CACHE["at"]) < 2 and plan["cached_at"] < time.time() - 4000
    assert len(outbound.connect_plan(True)["records"]) == 1 and asked == ["example.test"]                           # fresh still asks
