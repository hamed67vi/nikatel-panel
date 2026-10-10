"""ArvanCloud failover: the pure parts, the plan, the approval flow with a fake Arvan."""
import pytest

from nikatel.services import arvan, failover
from nikatel.services import settings as st

REC = {"id": "r1", "type": "A", "name": "ir", "value": [{"ip": "203.0.113.5", "port": None, "weight": 100, "country": ""}], "ttl": 120, "cloud": False, "upstream_https": "default", "ip_filter_mode": {"count": "single", "order": "none", "geo_filter": "none"}, "created_at": "x"}


def test_swap_ip_changes_only_the_address():
    body = arvan.swap_ip(REC, "203.0.113.5", "203.0.113.9")
    assert body["value"][0]["ip"] == "203.0.113.9" and body["ttl"] == 120 and body["name"] == "ir" and "created_at" not in body and "id" not in body
    assert REC["value"][0]["ip"] == "203.0.113.5"                         # the original is untouched
    assert arvan.swap_ip(REC, "203.0.113.77", "203.0.113.9") is None      # does not point there
    assert arvan.swap_ip({**REC, "type": "CNAME"}, "203.0.113.5", "203.0.113.9") is None
    with pytest.raises(ValueError):
        arvan.swap_ip(REC, "203.0.113.5", "not-an-ip")


def test_pick_target_best_common_server():
    a = {"id": 1, "name": "a"}
    b = {"id": 2, "name": "b"}
    c = {"id": 3, "name": "c"}
    assert failover.pick_target({10: [a, b], 11: [b, a]})["id"] in (1, 2)
    assert failover.pick_target({10: [a, b, c], 11: [b, c]})["id"] == 2
    assert failover.pick_target({10: [a], 11: [b]}) is None
    assert failover.pick_target({}) is None


class FakeArvan:
    def __init__(self):
        self.rec = {**REC, "value": [dict(REC["value"][0])]}
        self.calls = []

    def req(self, method, path, body=None, params=None):
        self.calls.append((method, path))

        if path == "/domains":
            return {"data": [{"domain": "example.test"}], "meta": {"last_page": 1}}

        if path == "/domains/example.test/dns-records":
            return {"data": [self.rec], "meta": {"last_page": 1}}

        if path.endswith("/dns-records/r1"):
            if method == "PUT":
                self.rec = {**self.rec, **body}
            return {"data": self.rec}

        raise AssertionError(path)


def test_find_apply_and_undo_with_fake_arvan(monkeypatch):
    fake = FakeArvan()
    monkeypatch.setattr(arvan, "_req", fake.req)
    found = arvan.find_by_ip("203.0.113.5")
    assert [(f["domain"], f["fqdn"], f["id"]) for f in found] == [("example.test", "ir.example.test", "r1")]
    assert arvan.find_by_ip("203.0.113.99") == []
    arvan.apply("example.test", "r1", "203.0.113.5", "203.0.113.9")
    assert fake.rec["value"][0]["ip"] == "203.0.113.9"
    with pytest.raises(RuntimeError):                                       # it no longer points at the old address: left alone
        arvan.apply("example.test", "r1", "203.0.113.5", "203.0.113.9")


def test_no_key_is_a_clear_error(client):
    with pytest.raises(RuntimeError, match="API key"):
        arvan.domains()


def test_settings_api_hides_the_key(client):
    r = client.put("/api/settings/arvan", json={"api_key": "secret-key-0123456789", "mode": "approve", "confirm_minutes": 5, "domains": ["Example.Test"]})
    assert r.status_code == 200
    j = r.json()
    assert j["api_key"] == "" and j["has_api_key"] is True and j["mode"] == "approve" and j["domains"] == ["example.test"]
    assert client.put("/api/settings/arvan", json={"mode": "banana"}).status_code == 400
    assert client.put("/api/settings/arvan", json={"mode": "off", "confirm_minutes": 1}).status_code == 400
    assert st.get("arvan")["api_key"] == "secret-key-0123456789"            # an empty key in a later save keeps it
    client.put("/api/settings/arvan", json={"mode": "off"})
    assert st.get("arvan")["api_key"] == "secret-key-0123456789"


def test_dismiss_and_state(client):
    st.put("failover", {"pending": [{"id": "1-1", "server_id": 1, "server": "ir1", "ip": "203.0.113.5", "tunnels": [], "dns": [], "target": None}]})
    assert client.get("/api/failover").json()["pending"][0]["id"] == "1-1"
    assert client.post("/api/failover/1-1/dismiss").status_code == 200
    j = client.get("/api/failover").json()
    assert j["pending"] == [] and j["history"][0]["dismissed"] is True
    assert client.post("/api/failover/1-1/dismiss").status_code == 400


def test_restore_puts_one_record_back_and_marks_the_failover_undone(client, monkeypatch):
    from nikatel.config import get_settings

    monkeypatch.setattr(get_settings(), "read_only", False)
    fake = FakeArvan()
    fake.rec["value"][0]["ip"] = "203.0.113.9"                              # the failover already pointed it at the new server
    monkeypatch.setattr(arvan, "_req", fake.req)
    monkeypatch.setattr(failover, "_notice", lambda text: None)
    entry = {"id": "1-1:r1", "at": 1, "plan_id": "1-1", "server": "ir1", "target": "ir2", "domain": "example.test", "record_id": "r1", "fqdn": "ir.example.test",
             "old": "203.0.113.5", "new": "203.0.113.9", "configs": ["c1"], "restored": False, "restored_at": None}
    hist = {"id": "1-1", "at": 1, "server_id": 1, "server": "ir1", "ip": "203.0.113.5", "target": {"id": 2, "name": "ir2", "ip": "203.0.113.9"}, "moved": 1, "tunnels": 1, "dns": [], "errors": [], "ok": True, "undone": False}
    st.put("failover", {"dns_log": [entry], "history": [hist]})
    j = client.get("/api/failover").json()
    assert j["dns_log"][0]["fqdn"] == "ir.example.test" and j["dns_log"][0]["restored"] is False and j["history"][0]["undone"] is False
    r = client.post("/api/failover/dns/1-1%3Ar1/restore")
    assert r.status_code == 200, r.text
    assert r.json()["old"] == "203.0.113.5" and fake.rec["value"][0]["ip"] == "203.0.113.5"
    j = client.get("/api/failover").json()
    assert j["dns_log"][0]["restored"] is True and j["dns_log"][0]["restored_at"] and j["history"][0]["undone"] is True
    assert client.post("/api/failover/dns/1-1%3Ar1/restore").status_code == 400          # already back
    assert client.post("/api/failover/1-1/undo").status_code == 400                       # nothing left to undo
    assert client.post("/api/failover/dns/nope/restore").status_code == 400


def test_undo_restores_every_record_of_one_failover(monkeypatch):
    fake = FakeArvan()
    fake.rec["value"][0]["ip"] = "203.0.113.9"
    monkeypatch.setattr(arvan, "_req", fake.req)
    monkeypatch.setattr(failover, "_notice", lambda text: None)
    e = {"id": "1-1:r1", "at": 1, "plan_id": "1-1", "server": "ir1", "target": "ir2", "domain": "example.test", "record_id": "r1", "fqdn": "ir.example.test", "old": "203.0.113.5", "new": "203.0.113.9", "configs": [], "restored": False, "restored_at": None}
    st.put("failover", {"dns_log": [e], "history": []})
    back = failover.undo("1-1")
    assert len(back) == 1 and back[0]["restored"] is True and fake.rec["value"][0]["ip"] == "203.0.113.5"
    with pytest.raises(ValueError):
        failover.undo("1-1")


def _fake_records(ip, only=None):
    mk = lambda n: {"domain": "example.test", "id": "r-" + n, "name": n, "fqdn": f"{n}.example.test", "ip": ip, "ttl": 120, "cloud": True}
    return [mk("a"), mk("b"), mk("c")]


def _servers_and_tunnels():
    """ir1 carries t1 (port 80, down), t2 (port 8075, up), t3 (port 443, up); ir2 is the other Iran server; fi1 is abroad.
    Config cA uses a.example.test:80 (t1), cB b.example.test:8075 (t2), cC a.example.test:443 (t3): record a serves t1 AND t3."""
    from nikatel.db import session
    from nikatel.models import Config, Server, Tunnel

    with session() as db:
        ir1, ir2, fi1 = Server(name="ir1", host="203.0.113.5", role="iran", online=True), Server(name="ir2", host="203.0.113.9", role="iran", online=True), Server(name="fi1", host="203.0.113.50", role="foreign", online=True)
        db.add_all([ir1, ir2, fi1])
        db.flush()
        t1 = Tunnel(name="t1", type="backhaul", transport="tcpmux", iran_server_id=ir1.id, foreign_server_id=fi1.id, state="down", forwarded_ports=[80])
        t2 = Tunnel(name="t2", type="backhaul", transport="tcpmux", iran_server_id=ir1.id, foreign_server_id=fi1.id, state="up", forwarded_ports=[8075])
        t3 = Tunnel(name="t3", type="backhaul", transport="tcpmux", iran_server_id=ir1.id, foreign_server_id=fi1.id, state="up", forwarded_ports=[443])
        db.add_all([t1, t2, t3])
        db.flush()
        db.add_all([Config(name="cA", status="down", meta={"address": "a.example.test", "port": 80}),
                    Config(name="cB", status="up", meta={"address": "b.example.test", "port": 8075}),
                    Config(name="cC", status="up", meta={"address": "a.example.test", "port": 443})])
        return {"ir1": ir1.id, "ir2": ir2.id, "fi1": fi1.id, "t1": t1.id, "t2": t2.id, "t3": t3.id}


def test_closure_pulls_tunnels_that_share_a_record():
    recs = [{"tunnels": [1, 3]}, {"tunnels": [2]}, {"tunnels": []}, {"tunnels": [3, 4]}]
    assert failover.closure({1}, recs) == {1, 3, 4}
    assert failover.closure({2}, recs) == {2}
    assert failover.closure(set(), recs) == set()


def test_route_plan_moves_the_broken_tunnels_and_the_records_that_serve_only_them(monkeypatch):
    from nikatel.services import autopilot

    ids = _servers_and_tunnels()
    st.put("arvan", {"api_key": "k", "mode": "approve"})
    monkeypatch.setattr(arvan, "find_by_ip", _fake_records)
    monkeypatch.setattr(autopilot, "ranking", lambda tid: [{"id": ids["ir2"], "name": "ir2", "score": 80}])
    p = failover.route_plan(ids["t1"], "gre_blocked")
    assert sorted(t["name"] for t in p["tunnels"]) == ["t1", "t3"]            # t3 shares record a with t1, so it moves too; t2 stays
    assert p["pulled"] == ["t3"] and p["whole"] is False and p["reason"] == "gre_blocked" and "GRE" in p["why"]
    assert [d["fqdn"] for d in p["dns"]] == ["a.example.test"] and p["dns"][0]["configs"] == ["cA", "cC"] and "tunnels" not in p["dns"][0]
    assert sorted(d["fqdn"] for d in p["left_alone"]) == ["b.example.test", "c.example.test"]
    assert p["target"] == {"id": ids["ir2"], "name": "ir2", "ip": "203.0.113.9"} and p["problems"] == []
    whole = failover.plan_for(ids["ir1"])
    assert whole["whole"] is True and len(whole["tunnels"]) == 3 and whole["pulled"] == [] and whole["left_alone"] == []
    assert [d["fqdn"] for d in whole["dns"]] == ["a.example.test", "b.example.test", "c.example.test"]


def test_after_ladder_queues_a_plan_for_approval(monkeypatch):
    from nikatel.services import autopilot

    ids = _servers_and_tunnels()
    monkeypatch.setattr(arvan, "find_by_ip", _fake_records)
    monkeypatch.setattr(autopilot, "ranking", lambda tid: [{"id": ids["ir2"], "name": "ir2", "score": 80}])
    said = []
    monkeypatch.setattr(autopilot, "_say", lambda aid, line, done=None, ok=None: said.append(line))
    monkeypatch.setattr(failover, "_notice", lambda text: said.append("TG " + text))
    st.put("arvan", {"api_key": "k", "mode": "off"})
    assert failover.after_ladder(1, ids["t1"], "gre_blocked") is False and st.get("failover")["pending"] == [] and "off" in said[-1]
    st.put("arvan", {"mode": "approve"})
    assert failover.after_ladder(1, ids["t1"], "gre_blocked") is False
    pend = st.get("failover")["pending"]
    assert len(pend) == 1 and pend[0]["reason"] == "gre_blocked" and sorted(t["name"] for t in pend[0]["tunnels"]) == ["t1", "t3"] and pend[0]["move"]
    assert any(x.startswith("TG") and "approve" in x for x in said) and any("waiting for your approval" in x for x in said)
    assert failover.after_ladder(1, ids["t1"], "gre_blocked") is False and len(st.get("failover")["pending"]) == 1     # not queued twice


def test_check_forgets_a_server_that_answers_again(monkeypatch):
    from nikatel.config import get_settings
    from nikatel.db import session
    from nikatel.models import Server

    monkeypatch.setattr(get_settings(), "read_only", False)
    monkeypatch.setattr(failover, "_notice", lambda text: None)
    st.put("arvan", {"api_key": "k", "mode": "approve", "confirm_minutes": 5})

    with session() as db:
        s = Server(name="ir9", host="203.0.113.77", role="iran", online=False)
        db.add(s)
        db.flush()
        sid = s.id

    failover.check()
    assert str(sid) in st.get("failover")["seen"] and st.get("failover")["pending"] == []     # seen, but the waiting time has not passed

    with session() as db:
        db.get(Server, sid).online = True

    failover.check()
    assert st.get("failover")["seen"] == {}                                                   # forgotten - and saved


def test_replace_plan_follows_only_the_records_of_the_replaced_tunnel(monkeypatch):
    ids = _servers_and_tunnels()
    st.put("arvan", {"api_key": "k", "mode": "approve"})
    monkeypatch.setattr(arvan, "find_by_ip", _fake_records)
    p = failover.replace_plan(ids["t2"], ids["ir2"])          # t2 is served by b.example.test alone
    assert [d["fqdn"] for d in p["dns"]] == ["b.example.test"] and p["left_alone"] == [] and p["tunnels"] == [] and p["target"]["id"] == ids["ir2"] and p["reason"] == "replace" and p["old_tunnel"] == "t2"
    p = failover.replace_plan(ids["t1"], ids["ir2"])          # a.example.test also serves t3, which stays: left alone
    assert p["dns"] == [] and [d["fqdn"] for d in p["left_alone"]] == ["a.example.test"]
