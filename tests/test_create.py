"""Create tunnel with the smart choices: the port precheck, the smart Iran choice, replacing a dead tunnel, the DNS follow-up."""
import time

import pytest

from nikatel.db import session
from nikatel.models import ActionLog, Config, Server, Tunnel
from nikatel.services import arvan, builder, create, detect, failover
from nikatel.services import settings as st


@pytest.fixture(autouse=True)
def _failover_on(monkeypatch):
    monkeypatch.setattr(failover, "ENABLED", True)          # the feature is switched off in production for now; these tests are about how it works



def _world():
    with session() as db:
        ir1, ir2, fi1 = Server(name="ir1", host="203.0.113.5", role="iran", online=True), Server(name="ir2", host="203.0.113.9", role="iran", online=True), Server(name="fi1", host="203.0.113.50", role="foreign", online=True)
        db.add_all([ir1, ir2, fi1])
        db.flush()
        old = Tunnel(name="fi1-ir1", type="backhaul", transport="tcpmux", iran_server_id=ir1.id, foreign_server_id=fi1.id, state="down", state_since=int(time.time()) - 2400, forwarded_ports=[8075])
        other = Tunnel(name="fi1-ir1-b", type="backhaul", transport="tcpmux", iran_server_id=ir1.id, foreign_server_id=fi1.id, state="up", forwarded_ports=[443])
        db.add_all([old, other])
        db.flush()
        db.add_all([Config(name="cA", status="down", meta={"address": "a.example.test", "port": 8075}), Config(name="cB", status="up", meta={"address": "b.example.test", "port": 443})])
        return {"ir1": ir1.id, "ir2": ir2.id, "fi1": fi1.id, "old": old.id, "other": other.id}


def test_precheck_sees_busy_ports_and_replaceable_tunnels(client):
    w = _world()
    r = client.post("/api/tunnels/precheck", json={"iran_id": w["ir1"], "foreign_id": w["fi1"], "ports": "8075"}).json()
    assert r["busy"][0]["by"] == "tunnel fi1-ir1" and r["busy"][0]["state"] == "down" and r["can_build"] is False and r["iran"] == "ir1"
    assert r["busy"][0]["id"] == w["old"] and r["busy"][0]["all_ports"]         # a tunnel of the panel that holds the port: the form offers to replace it
    assert [c["name"] for c in r["conflicts"]] == ["fi1-ir1"] and r["conflicts"][0]["replaceable"] is True and r["conflicts"][0]["iran"] == "ir1" and r["conflicts"][0]["ports"] == [8075]
    r = client.post("/api/tunnels/precheck", json={"iran_id": w["ir2"], "foreign_id": w["fi1"], "ports": "8075"}).json()
    assert r["busy"] == [] and r["can_build"] is True and r["conflicts"][0]["id"] == w["old"]          # free on ir2; the dead tunnel on fi1 can be replaced
    r = client.post("/api/tunnels/precheck", json={"iran_id": 0, "foreign_id": w["fi1"], "ports": "443"}).json()
    assert r["busy"] == [] and r["conflicts"][0]["replaceable"] is False                                 # smart choice: no Iran side yet; an up tunnel is not ticked by default
    assert client.post("/api/tunnels/precheck", json={"iran_id": 0, "foreign_id": w["fi1"], "ports": "nope"}).status_code == 400
    assert client.post("/api/tunnels/precheck", json={"iran_id": w["fi1"], "foreign_id": w["fi1"], "ports": "80"}).status_code == 400


def test_create_with_smart_choice_replaces_the_dead_tunnel_and_queues_the_dns(client, monkeypatch):
    from nikatel.config import get_settings

    monkeypatch.setattr(get_settings(), "read_only", False)
    w = _world()
    st.put("arvan", {"api_key": "k", "mode": "approve"})
    monkeypatch.setattr(arvan, "find_by_ip", lambda ip, only=None: [{"domain": "example.test", "id": "ra", "name": "a", "fqdn": "a.example.test", "ip": ip, "ttl": 120, "cloud": True},
                                                                     {"domain": "example.test", "id": "rb", "name": "b", "fqdn": "b.example.test", "ip": ip, "ttl": 120, "cloud": True}])
    monkeypatch.setattr(detect, "auto_iran", lambda foreign_id, ports="": [{"id": w["ir1"], "name": "ir1", "score": 0, "reason": "port 8075 already used here"},
                                                                           {"id": w["ir2"], "name": "ir2", "score": 81, "reason": "60 ms, 0% loss, 1 tunnels, cpu 5%"}])
    gone, built = [], []
    monkeypatch.setattr(builder, "delete_tunnel", lambda tid, keep_record=False, trash=False: gone.append(tid) or ["Iran: removed", "foreign: removed"])

    def fake_build(iran_id, foreign_id, ports, transport, mtu=0, keepalive=75, forced_name="", nodelay=True, opts=None):
        built.append((iran_id, foreign_id, ports, transport))

        with session() as db:
            e = ActionLog(ref="new", action="build", source="manual", story=["Built."], ok=True, finished_at=int(time.time()))
            db.add(e)
            db.flush()
            return e.id

    monkeypatch.setattr(builder, "start_build", fake_build)
    monkeypatch.setattr(failover, "_notice", lambda text: None)
    monkeypatch.setattr(create, "_POLL", 0.05)
    applied = []
    monkeypatch.setattr(arvan, "apply", lambda domain, rid, old, new: applied.append((domain, rid, old, new)) or {"domain": domain, "id": rid, "name": rid, "old": old, "new": new})
    r = client.post("/api/tunnels", json={"iran_id": 0, "foreign_id": w["fi1"], "ports": "8075", "kind": "backhaul", "transport": "tcpmux", "replace_ids": [w["old"]], "dns_follow": True})
    assert r.status_code == 202, r.text
    aid = r.json()["id"]

    for _ in range(100):
        s = client.get(f"/api/actions/{aid}").json()
        if s["done"]:
            break
        time.sleep(0.1)

    text = "\n".join(s["story"])
    assert s["done"] and s["ok"] is True and s["action"] == "create", text
    assert "Order (GRE not OK last; quality first" in text and "ir2" in text and gone == [w["old"]] and built == [(w["ir2"], w["fi1"], "8075", "tcpmux")] and "Built." in text
    assert applied == [("example.test", "ra", "203.0.113.5", "203.0.113.9")] and "DNS (as ticked)" in text        # the tick is the approval: applied at once
    hist, log = st.get("failover")["history"], st.get("failover")["dns_log"]
    assert hist[-1]["reason"] == "replace" and hist[-1]["ok"] is True and hist[-1]["target"]["id"] == w["ir2"] and st.get("failover")["pending"] == []
    assert log[-1]["fqdn"] == "a.example.test" and log[-1]["new"] == "203.0.113.9" and log[-1]["restored"] is False
    assert client.post("/api/tunnels", json={"iran_id": 0, "foreign_id": w["fi1"], "ports": "80", "replace_ids": [99999]}).status_code == 400   # a tunnel that is gone


def test_build_accepts_an_empty_transport_for_backhaul(client):
    w = _world()
    r = client.post("/api/tunnels", json={"iran_id": w["ir2"], "foreign_id": w["fi1"], "ports": "9075", "kind": "backhaul", "transport": ""})
    assert r.status_code == 400 and "transport must be" not in r.json()["detail"]        # refused only because the tests run read-only
    r = client.post("/api/tunnels", json={"iran_id": w["ir2"], "foreign_id": w["fi1"], "ports": "9075", "kind": "backhaul", "transport": "nope"})
    assert r.status_code == 400 and "transport must be" in r.json()["detail"]


class _Arvan:
    def __init__(self):
        self.rec = {"id": "r1", "type": "A", "name": "ca", "value": [{"ip": "203.0.113.5", "port": None, "weight": 100, "country": ""}], "ttl": 120, "cloud": True}

    def req(self, method, path, body=None, params=None):
        if path == "/domains":
            return {"data": [{"domain": "example.test"}], "meta": {"last_page": 1}}

        if path == "/domains/example.test/dns-records":
            return {"data": [self.rec], "meta": {"last_page": 1}}

        if path.endswith("/dns-records/r1"):
            if method == "PUT":
                self.rec = {**self.rec, **body}

            return {"data": self.rec}

        raise AssertionError(path)


def test_point_names_changes_the_record_and_logs_it(client, monkeypatch):
    from nikatel.api import failover as fapi

    w = _world()
    st.put("arvan", {"api_key": "k", "mode": "approve"})
    fake = _Arvan()
    monkeypatch.setattr(arvan, "_req", fake.req)
    monkeypatch.setattr(failover, "_notice", lambda text: None)
    said = []
    monkeypatch.setattr(failover.autopilot, "_say", lambda aid, line, done=None, ok=None: said.append(line))
    assert failover._domain_of("ca.example.test", ["example.test", "other.test"]) == "example.test" and failover._domain_of("x.nope.test", ["example.test"]) == ""
    done = failover.point_names(7, ["CA.example.test", "nope.example.test", "x.other.test"], w["ir2"])
    assert len(done) == 1 and fake.rec["value"][0]["ip"] == "203.0.113.9"
    entry = st.get("failover")["dns_log"][-1]
    assert entry["fqdn"] == "ca.example.test" and entry["old"] == "203.0.113.5" and entry["new"] == "203.0.113.9" and entry["server"] == "ir1" and entry["target"] == "ir2"
    assert any("no A record" in x for x in said) and any("not in any domain" in x for x in said)
    fapi._RECORDS["at"] = 0
    r = client.get("/api/arvan/records").json()
    assert r["records"][0]["fqdn"] == "ca.example.test" and r["records"][0]["ips"] == ["203.0.113.9"]
    assert client.post("/api/tunnels", json={"iran_id": w["ir2"], "foreign_id": w["fi1"], "ports": "9075", "dns_names": ["bad name!"]}).status_code == 400


def test_the_smart_choice_order_is_quality_first_then_resources():
    rows = [{"name": "a", "score": 70, "quality": 92, "resources": 40, "unused": 80}, {"name": "b", "score": 66, "quality": 95, "resources": 85, "unused": 40},
            {"name": "c", "score": 60, "quality": 78, "resources": 99, "unused": 99}, {"name": "d", "score": 0, "quality": 99, "resources": 99, "unused": 99}, {"name": "e", "score": 50, "quality": 91, "resources": 20, "unused": 90}]
    assert [r["name"] for r in sorted(rows, key=detect.rank_key)] == ["b", "a", "e", "c", "d"]        # b and a: same quality step -> b has more resources; c: lower quality step; d: unusable last
    assert detect.rank_key(rows[0]) > detect.rank_key(rows[1])


def test_the_smart_choice_goes_on_to_the_next_server_when_a_build_fails(client, monkeypatch):
    from nikatel.config import get_settings

    monkeypatch.setattr(get_settings(), "read_only", False)
    w = _world()
    monkeypatch.setattr(detect, "auto_iran", lambda foreign_id, ports="": [{"id": w["ir1"], "name": "ir1", "score": 90, "quality": 95, "resources": 50, "unused": 50, "reason": "ok"},
                                                                           {"id": w["ir2"], "name": "ir2", "score": 80, "quality": 90, "resources": 60, "unused": 60, "reason": "ok"}])
    tried = []

    def fake_build(iran_id, foreign_id, ports, transport, mtu=0, keepalive=75, forced_name="", nodelay=True, opts=None):
        tried.append(iran_id)

        with session() as db:
            ok = iran_id == w["ir2"]                                            # the first server fails, the second works
            e = ActionLog(ref="b", action="build", source="manual", story=["Built." if ok else "Stopped: no GRE on this route"], ok=ok, finished_at=int(time.time()))
            db.add(e)
            db.flush()
            return e.id

    monkeypatch.setattr(builder, "start_build", fake_build)
    monkeypatch.setattr(create, "_POLL", 0.05)
    r = client.post("/api/tunnels", json={"iran_id": 0, "foreign_id": w["fi1"], "ports": "9075", "kind": "backhaul", "transport": "tcpmux"})
    s = None

    for _ in range(100):
        s = client.get(f"/api/actions/{r.json()['id']}").json()

        if s["done"]:
            break

        time.sleep(0.1)

    text = "\n".join(s["story"])
    assert s["ok"] is True and tried == [w["ir1"], w["ir2"]] and "ir1: did not work - trying the next Iran server" in text and "Order (GRE not OK last; quality first" in text


def test_the_smart_test_leaves_a_poor_server_and_takes_the_next(client, monkeypatch):
    from nikatel.config import get_settings
    from nikatel.services import tunnel_test

    monkeypatch.setattr(get_settings(), "read_only", False)
    w = _world()
    monkeypatch.setattr(detect, "auto_iran", lambda foreign_id, ports="": [{"id": w["ir1"], "name": "ir1", "score": 90, "quality": 95, "resources": 50, "unused": 50, "reason": "ok"},
                                                                           {"id": w["ir2"], "name": "ir2", "score": 80, "quality": 90, "resources": 60, "unused": 60, "reason": "ok"}])
    tested = []
    scores = {w["ir1"]: 47, w["ir2"]: 71}

    def fake_start(iran_id, foreign_id, kinds, measure="latency"):
        tested.append(iran_id)
        return f"job{iran_id}"

    monkeypatch.setattr(tunnel_test, "compare_start", fake_start)
    monkeypatch.setattr(tunnel_test, "compare_status", lambda jid: {"done": True, "results": [{"kind": "tcpmux", "ok": True, "score": scores[int(jid[3:])], "latency_ms": 100, "loss_pct": 0}]})
    monkeypatch.setattr(create.time, "sleep", lambda s: None)
    r = client.post("/api/tunnels/smart-test", json={"foreign_id": w["fi1"], "ports": "9075", "kind": "tcpmux"})
    assert r.status_code == 202, r.text
    s = None

    for _ in range(100):
        s = client.get(f"/api/actions/{r.json()['id']}").json()

        if s["done"]:
            break

        time.sleep(0.1)

    text = "\n".join(s["story"])
    assert tested == [w["ir1"], w["ir2"]] and "ir1: 100 ms, 0 % loss, score 47/100 - poor, trying the next Iran server" in text and s["story"][-1] == "Best server: ir2" and s["ok"] is True


def test_precheck_lists_every_tunnel_that_already_ends_on_the_foreign_server(client):
    w = _world()
    r = client.post("/api/tunnels/precheck", json={"iran_id": w["ir2"], "foreign_id": w["fi1"], "ports": "9000"}).json()
    assert r["conflicts"] == [] and r["foreign"] == "fi1"                       # no port in common, but a second tunnel to fi1 is still asked about
    assert sorted(x["name"] for x in r["same_foreign"]) == ["fi1-ir1", "fi1-ir1-b"]

def test_a_dead_backhaul_tunnel_holding_a_port_is_recognised_and_a_live_one_is_not():
    from nikatel.services.create import is_dead_tunnel, parse_backhaul_unit, parse_owner

    assert parse_owner("OWNER 4242 backhaul backhaul-iran-tcpmux-9921.service\n") == {"pid": "4242", "process": "backhaul", "unit": "backhaul-iran-tcpmux-9921.service"}
    assert parse_owner("OWNER - - -") == {"pid": "", "process": "", "unit": ""} and parse_owner("") == {}
    assert parse_backhaul_unit("backhaul-iran-tcpmux-9921.service") == ("tcpmux", 9921) and parse_backhaul_unit("nginx.service") is None and parse_backhaul_unit("backhaul-kharej-tcp-9900.service") is None
    assert is_dead_tunnel(False, "") and is_dead_tunnel(False, "down") and is_dead_tunnel(False, "unknown")      # nobody connected, not an up tunnel
    assert not is_dead_tunnel(True, "down") and not is_dead_tunnel(False, "up") and not is_dead_tunnel(False, "degraded")


def test_precheck_offers_to_replace_a_dead_tunnel_that_holds_the_port(client, monkeypatch):
    from nikatel.services import build_other, create

    w = _world()
    monkeypatch.setattr(build_other, "parse_simple_facts", lambda out: {"tcp": {8076}})
    monkeypatch.setattr(create, "run_script", lambda node, name, params=None, timeout=30: "OUT")
    monkeypatch.setattr(create, "identify_owner", lambda node, sid, port: {"process": "backhaul", "unit": "backhaul-iran-tcpmux-9930.service", "transport": "tcpmux", "ctl": 9930, "connected": False, "tunnel": "", "state": "", "dead": True})
    r = client.post("/api/tunnels/precheck", json={"iran_id": w["ir2"], "foreign_id": w["fi1"], "ports": "8076", "live": True}).json()
    assert r["busy"] == [] and r["can_build"] is True and r["stale"][0]["unit"] == "backhaul-iran-tcpmux-9930.service" and "nobody is connected" in r["stale"][0]["why"]
    monkeypatch.setattr(create, "identify_owner", lambda node, sid, port: {"process": "nginx", "unit": "nginx.service"})
    r = client.post("/api/tunnels/precheck", json={"iran_id": w["ir2"], "foreign_id": w["fi1"], "ports": "8076", "live": True}).json()
    assert r["stale"] == [] and r["busy"][0]["by"] == "nginx (nginx.service)" and r["can_build"] is False