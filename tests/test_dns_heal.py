"""The Doctor points the DNS name of a config that is DOWN at the Iran server that really carries its port (the owner, 2026-11-03: the Turkey TLS config was down for hours while its tunnel was up; the
tunnel had been moved and the record of the name still pointed at the old Iran server; the Doctor judged only the tunnel)."""
import time

import pytest

from nikatel.db import session
from nikatel.models import Config, Server, Tunnel
from nikatel.services import autopilot, dnscache, dnsheal, doctor, failover

OLD, NEW = "203.0.113.5", "203.0.113.9"


@pytest.fixture(autouse=True)
def _world(monkeypatch):
    dnsheal._LOOKED.clear()
    monkeypatch.setattr(doctor, "mode", lambda: "move")
    monkeypatch.setattr(doctor, "can_move", lambda: True)
    monkeypatch.setattr(dnsheal, "_dialled", dict)
    monkeypatch.setitem(dnscache.CACHE, "at", time.time())
    monkeypatch.setitem(dnscache.CACHE, "rows", [_rec("tr2ss", [OLD])])
    s = doctor._state()
    s["dns_heal"] = {}
    doctor._save(s)


def _rec(name, ips, cloud=False, port=None, domain="example.test"):
    return {"account": "cf1", "domain": domain, "id": "r-" + name, "type": "A", "name": name, "fqdn": f"{name}.{domain}", "ips": ips, "ttl": 120, "cloud": cloud, "port": port}


def _t(tid, name, iran_id, ports=(11422,), listen=True, state="up"):
    return {"id": tid, "name": name, "iran_id": iran_id, "host": {1: OLD, 2: NEW}[iran_id], "state": state, "ports": set(ports), "listen": {str(p): listen for p in ports}}


HOSTS = {1: OLD, 2: NEW}
DOWN = [{"name": "Turkey-2", "address": "tr2ss.example.test", "port": 11422}]


def test_a_name_that_points_at_a_server_without_the_port_is_pointed_at_the_tunnels_server():
    me = _t(10, "tr3", 2)
    fixes, notes = dnsheal.stale_names(DOWN, me, [me], HOSTS, [_rec("tr2ss", [OLD])], lambda: {"tr2ss.example.test": {11422}})
    assert notes == [] and fixes == [{"fqdn": "tr2ss.example.test", "port": 11422, "config": "Turkey-2", "old": [OLD], "new": NEW, "server_id": 2}]


def test_a_name_that_already_points_at_a_server_that_carries_the_port_is_left_alone_even_if_it_is_another_tunnel():
    me, other = _t(10, "tr3", 2), _t(11, "tr3-twin", 1)
    assert dnsheal.stale_names(DOWN, me, [me, other], HOSTS, [_rec("tr2ss", [OLD])], dict) == ([], [])


def test_a_server_whose_tunnel_is_down_or_does_not_listen_does_not_count_as_carrying_the_port():
    me, twin = _t(10, "tr3", 2), _t(11, "tr3-twin", 1, listen=False)
    fixes, _ = dnsheal.stale_names(DOWN, me, [me, twin], HOSTS, [_rec("tr2ss", [OLD])], dict)
    assert [f["new"] for f in fixes] == [NEW]
    twin = _t(11, "tr3-twin", 1, state="down")
    assert [f["new"] for f in dnsheal.stale_names(DOWN, me, [me, twin], HOSTS, [_rec("tr2ss", [OLD])], dict)[0]] == [NEW]


def test_nothing_is_changed_when_the_tunnel_is_not_up_or_does_not_listen_on_the_port():
    me = _t(10, "tr3", 2, state="down")
    assert dnsheal.stale_names(DOWN, me, [me], HOSTS, [_rec("tr2ss", [OLD])], dict)[0] == []
    me = _t(10, "tr3", 2, listen=False)
    fixes, notes = dnsheal.stale_names(DOWN, me, [me], HOSTS, [_rec("tr2ss", [OLD])], dict)
    assert fixes == [] and "does not listen on 11422" in notes[0]


@pytest.mark.parametrize("rec,address,word", [
    (_rec("tr2ss", [OLD], cloud=True), "tr2ss.example.test", "CDN"),
    (_rec("tr2ss", [OLD], port=8080), "tr2ss.example.test", "CDN"),
    (_rec("*", [OLD]), "tr2ss.example.test", "exactly this name"),
    (_rec("tr2ss", [OLD]), "198.51.100.7", "number"),
])
def test_names_the_doctor_must_not_touch_are_listed_with_the_reason(rec, address, word):
    me = _t(10, "tr3", 2)
    fixes, notes = dnsheal.stale_names([{"name": "c", "address": address, "port": 11422}], me, [me], HOSTS, [rec], dict)
    assert fixes == [] and word in notes[0]


def test_a_name_that_another_host_dials_on_a_port_the_tunnel_does_not_carry_is_left_alone():
    me = _t(10, "tr3", 2)
    fixes, notes = dnsheal.stale_names(DOWN, me, [me], HOSTS, [_rec("tr2ss", [OLD])], lambda: {"tr2ss.example.test": {11422, 9999}})
    assert fixes == [] and "9999" in notes[0]


def test_hosts_that_cannot_be_read_stop_the_repair_and_they_are_asked_only_when_a_name_is_a_candidate():
    me, asked = _t(10, "tr3", 2), []
    fixes, notes = dnsheal.stale_names(DOWN, me, [me], HOSTS, [_rec("tr2ss", [OLD])], lambda: asked.append(1))
    assert fixes == [] and "cannot be read" in notes[0] and asked == [1]
    asked.clear()
    dnsheal.stale_names(DOWN, me, [me], HOSTS, [_rec("tr2ss", [NEW])], lambda: asked.append(1))          # (the name is right: nobody is asked)
    assert asked == []


def _tunnel(state_since=None):
    with session() as db:
        ir1, ir2, fi1 = Server(name="ir1", host=OLD, role="iran", online=True), Server(name="ir2", host=NEW, role="iran", online=True), Server(name="fi1", host="203.0.113.50", role="foreign", online=True)
        db.add_all([ir1, ir2, fi1])
        db.flush()
        t = Tunnel(name="tr3", type="backpack", transport="tcpmux", iran_server_id=ir2.id, foreign_server_id=fi1.id, state="up", state_since=state_since or int(time.time()) - 3600,
                   forwarded_ports=[11422], detail={"listen": {"11422": True}})
        db.add_all([t, Config(name="Turkey-2", enabled=True, status="down", meta={"address": "tr2ss.example.test", "port": 11422})])
        db.flush()
        return t.id, ir2.id


UX = {"grade": "bad", "bad": [{"config": "Turkey-2", "issue": "down", "text": "does not connect"}]}


def _started(monkeypatch):
    started = []
    monkeypatch.setattr(autopilot, "_start", lambda label, fn, *a, **kw: started.append((label, fn, a, kw)) or 5)
    return started


def test_the_look_starts_the_repair_story_in_the_mode_move_and_remembers_the_name(monkeypatch):
    tid, sid = _tunnel()
    started = _started(monkeypatch)
    dnsheal.look({tid: UX})
    assert len(started) == 1 and started[0][0] == "tr3" and started[0][1] is dnsheal._fix and started[0][3] == {"action": "fix", "source": "auto"}
    assert started[0][2][0] == tid and [(f["fqdn"], f["new"], f["server_id"]) for f in started[0][2][1]] == [("tr2ss.example.test", NEW, sid)]
    assert "tr2ss.example.test" in doctor._state()["dns_heal"]
    dnsheal.look({tid: UX})                                                       # (the same name again: once in six hours)
    assert len(started) == 1


def test_a_config_that_is_only_slow_or_a_tunnel_with_no_down_config_is_not_looked_at(monkeypatch):
    tid, _ = _tunnel()
    started = _started(monkeypatch)
    dnsheal.look({tid: {"grade": "poor", "bad": [{"config": "Turkey-2", "issue": "slow", "text": "slow"}]}, 999: {"grade": "good", "bad": []}})
    assert started == []


def test_in_the_mode_fix_nothing_is_changed_and_the_owner_is_told_once_an_hour(monkeypatch):
    tid, _ = _tunnel()
    started, said = _started(monkeypatch), []
    monkeypatch.setattr(doctor, "mode", lambda: "fix")
    monkeypatch.setattr(dnsheal, "emit", lambda db, level, source, text, ref=None: said.append(text))
    dnsheal.look({tid: UX})
    dnsheal.look({tid: UX})
    assert started == [] and len(said) == 1 and "tr2ss.example.test" in said[0] and "mode 'fix'" in said[0]


@pytest.mark.parametrize("when", ["stale_copy", "just_came_up"])
def test_the_look_waits_for_a_fresh_copy_of_the_records_and_for_a_tunnel_that_has_been_up_a_while(monkeypatch, when):
    tid, _ = _tunnel(state_since=int(time.time()) - 60 if when == "just_came_up" else None)
    started = _started(monkeypatch)

    if when == "stale_copy":
        monkeypatch.setitem(dnscache.CACHE, "at", time.time() - 3600)

    dnsheal.look({tid: UX})
    assert started == []


def test_the_panel_wide_limit_of_repairs_in_24_hours_holds(monkeypatch):
    tid, _ = _tunnel()
    started = _started(monkeypatch)
    s = doctor._state()
    s["dns_heal"] = {f"n{i}.example.test": int(time.time()) - 600 for i in range(dnsheal.PER_DAY)}
    doctor._save(s)
    monkeypatch.setattr(dnsheal, "emit", lambda *a, **k: None)
    dnsheal.look({tid: UX})
    assert started == []


def test_the_story_points_the_names_at_the_tunnels_server_and_checks_the_port(monkeypatch):
    said, calls = [], []
    monkeypatch.setattr(autopilot, "_say", lambda aid, text, **kw: said.append((text, kw)))
    monkeypatch.setattr(failover, "point_names", lambda aid, names, iran_id, tag="create": calls.append((names, iran_id, tag)) or [{"before": {}}])
    monkeypatch.setattr(dnsheal, "_reach", lambda ip, port: f"reach {ip}:{port}")
    dnsheal._fix(3, 10, [{"fqdn": "tr2ss.example.test", "port": 11422, "config": "Turkey-2", "old": [OLD], "new": NEW, "server_id": 2}])
    assert calls == [(["tr2ss.example.test"], 2, "heal")]
    assert "Turkey-2" in said[1][0] and OLD in said[1][0] and NEW in said[1][0] and said[2][0] == f"reach {NEW}:11422"
    assert said[-1][1] == {"done": True, "ok": True} and "1 record(s) changed" in said[-1][0]
