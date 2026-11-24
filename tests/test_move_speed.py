"""A move keeps the type that the Doctor's speed tests know to be fast (the owner, 2026-11-24: Germany-2 was put on a fast type by hand; when the Iran server it had left came back, the Doctor's return tested six types by
delay and loss alone, built the best score - a Backhaul type of 120 Mb/s against bp:wss of 1000 - and re-pointed the DNS; the return then failed every 10 minutes for hours). Tested: `move_objection` with the numbers of that
day, the choice of `failover._best_kind`, the wait before a failed return is tried again, the ports another tunnel holds on the server it would return to, the loop of `doctor.returns`, a tunnel whose counters cannot be read
being waited for by its open connections, and the script that counts them."""
import re
import time

import pytest

from nikatel.services import autopilot, doctor, failover
from nikatel.services import knowledge as kn

_REAL_OPEN_CONNS = failover._open_conns          # (the conftest fixture of the moves puts a stub in its place: the tests of the real one call it from here)

NOW = 1_800_000_000
DAY = 86400


def _test(pair, kind, ok, down, at=NOW - 3600, measure="full", lat=85.0, loss=0.0, score="auto"):
    score = (70 if ok else 30) if score == "auto" else score
    pair["kinds"][kind] = kn.merge(pair["kinds"].get(kind), {"kind": kind, "ok": ok, "score": score, "down_mbps": down, "latency_ms": lat, "jitter_ms": 1.0, "loss_pct": loss}, measure, at)


def _origin():
    """The pair the tunnel runs on: bp:wss gave 977-1086 in four full tests."""
    p = {"kinds": {}}

    for i, d in enumerate((1082.0, 1056.0, 1086.0, 977.0)):
        _test(p, "bp:wss", True, d, at=NOW - (4 - i) * 3600)

    return p


def _new():
    """The pair the tunnel would move to: Backhaul over links gave 83-120, no BackPack type has a speed test (only delay tests that called them poor)."""
    p = {"kinds": {}}
    _test(p, "tcpmux", True, 119.4, at=NOW - 25 * 3600)
    _test(p, "gretap:tcpmux", True, 89.8, at=NOW - 3600)
    _test(p, "ipsec:tcpmux", True, 99.6, at=NOW - 25 * 3600)

    for k in ("bp:wss", "wssmux"):
        _test(p, k, False, None, measure="latency", at=NOW - 5 * DAY, score=44, lat=83.0)

    return p


COUSIN = {"bp:wss": {"worst": 910.0, "passes": 4, "iran_id": 44, "best": 1028.0}}


def test_a_type_with_no_speed_test_or_a_far_lower_one_is_left_out_of_a_move_when_the_running_type_is_known_fast():
    o, n = _origin(), _new()
    why = kn.move_objection("bp:wss", "tcpmux", o, n, COUSIN, NOW)
    assert "119 Mb/s at its best" in why and "977 Mb/s at its slowest" in why
    assert "89 Mb/s" in kn.move_objection("bp:wss", "gretap:tcpmux", o, n, COUSIN, NOW) or "90 Mb/s" in kn.move_objection("bp:wss", "gretap:tcpmux", o, n, COUSIN, NOW)
    assert "no speed test" in kn.move_objection("bp:wss", "wssmux", o, n, COUSIN, NOW) and "977" in kn.move_objection("bp:wss", "wssmux", o, n, COUSIN, NOW)
    assert "no speed test" in kn.move_objection("bp:wss", "bp:stealth", o, n, COUSIN, NOW)


def test_a_type_that_carried_nearly_as_much_or_that_other_iran_servers_proved_is_not_objected_to():
    o, n = _origin(), _new()
    _test(n, "bp:tcpmux", True, 700.0)
    assert kn.move_objection("bp:wss", "bp:tcpmux", o, n, COUSIN, NOW) == ""                       # (700 is not under half of 977)
    z = {**COUSIN, "bp:stealth": {"worst": 800.0, "passes": 5, "iran_id": 11, "best": 900.0}}
    assert kn.move_objection("bp:wss", "bp:stealth", o, n, z, NOW) == ""                           # (other Iran servers carried it at 900)
    _test(n, "chisel:fwd", True, 500.0)
    assert kn.move_objection("bp:wss", "chisel:fwd", o, n, COUSIN, NOW) == ""                      # (500 against 977: not twice as slow)


def test_nothing_is_objected_to_when_nothing_is_known_about_the_running_type_or_its_tests_are_old():
    n = _new()
    assert kn.move_objection("bp:wss", "tcpmux", None, n, None, NOW) == "" and kn.move_objection("bp:wss", "tcpmux", {"kinds": {}}, n, {}, NOW) == ""
    old = {"kinds": {}}

    for d in (1000.0, 1010.0, 990.0):
        _test(old, "bp:wss", True, d, at=NOW - 40 * DAY)

    assert kn.move_objection("bp:wss", "tcpmux", old, n, {}, NOW) == ""                            # (a month old is only a hint)
    assert kn.move_objection("bp:wss", "tcpmux", _origin(), None, None, NOW) != ""                  # (the running type is known, the other has no number: left out)


def test_the_best_test_of_the_other_iran_servers_pairs_is_reported_too():
    a, b = {"kinds": {}}, {"kinds": {}}

    for d in (900.0, 905.0, 910.0):
        _test(a, "bp:wss", True, d, at=NOW - 3 * DAY)

    for d in (990.0, 1050.0, 970.0):
        _test(b, "bp:wss", True, d, at=NOW - 3 * DAY)

    z = kn.cousin_worsts({"44:73": a, "11:73": b}, 8, 73, NOW)
    assert z["bp:wss"]["worst"] == 970.0 and z["bp:wss"]["best"] == 1050.0 and z["bp:wss"]["iran_id"] == 11
    z2 = kn.cousin_worsts({"11:73": b, "44:73": a}, 8, 73, NOW)
    assert z2["bp:wss"]["best"] == 1050.0 and z2["bp:wss"]["worst"] == 970.0


# ------------------------------------------------------------------ the choice of a move
def _choose(monkeypatch, results, origin, new, cousin, running="bp:wss"):
    said = []
    monkeypatch.setattr(failover, "PICK_BEST", True)
    monkeypatch.setattr(failover, "_test_pair", lambda *a, **k: results)
    monkeypatch.setattr(autopilot, "_say", lambda aid, line, done=None, ok=None: said.append(line))
    monkeypatch.setattr(autopilot, "kind_of", lambda *a: running)
    monkeypatch.setattr(kn, "pair", lambda i, f: origin if i == 8 else new)
    monkeypatch.setattr(kn, "cousins", lambda i, f, now: cousin)
    snap = {"name": "t-now", "type": "backpack", "transport": "wss", "record": {}, "iran_id": 8, "foreign_id": 73, "foreign_name": "FRN"}
    got = failover._best_kind(1, snap, {"id": 7, "name": "irhome"}, {}, False)
    return got, said


def _row(kind, score):
    return {"kind": kind, "score": score, "loss_pct": 0.0, "latency_ms": 85.0}


def test_a_move_keeps_the_fast_type_when_the_delay_test_prefers_a_slow_one(monkeypatch):
    results = [_row("bp:wss", 44), _row("tcpmux", 62), _row("gretap:tcpmux", 62), _row("ipsec:tcpmux", 59), _row("wssmux", 60)]
    got, said = _choose(monkeypatch, results, _origin(), _new(), COUSIN)
    assert got == "bp:wss"                                                                          # (the day it was tcpmux, 62 against 44)
    assert any("left out by what the Doctor's speed tests know" in s and "tcpmux" in s and "wssmux" in s for s in said)


def test_a_move_without_speed_knowledge_chooses_as_it_did(monkeypatch):
    results = [_row("bp:wss", 44), _row("tcpmux", 62)]
    got, said = _choose(monkeypatch, results, {"kinds": {}}, {"kinds": {}}, {})
    assert got == "tcpmux" and not any("speed tests know" in s for s in said)


def test_a_move_takes_a_type_that_the_speed_tests_do_not_object_to_when_it_scores_clearly_better(monkeypatch):
    n = _new()
    _test(n, "bp:tcpmux", True, 800.0)
    results = [_row("bp:wss", 44), _row("tcpmux", 62), _row("bp:tcpmux", 58)]
    got, _said = _choose(monkeypatch, results, _origin(), n, COUSIN)
    assert got == "bp:tcpmux"                                                                       # (800 is not under half of 977; tcpmux is left out)


def test_when_the_running_type_does_not_connect_there_something_that_does_is_still_built(monkeypatch):
    results = [_row("tcpmux", 62), _row("gretap:tcpmux", 60)]
    got, _said = _choose(monkeypatch, results, _origin(), _new(), COUSIN)
    assert got == "tcpmux"                                                                          # (every candidate is objected to: a tunnel that carries nothing is worse)


# ------------------------------------------------------------------ the loop of the return
def _fails(n, now, gap=600, **kw):
    return [{"id": f"r{i}", "at": now - 100 - i * gap, "reason": "return", "ok": False, "from_id": 8, "to_id": 7, **kw} for i in range(n)]


def test_a_failed_return_waits_three_hours_and_not_more_than_three_a_day():
    entry = {"id": "d", "reason": "down", "from_id": 7, "to_id": 8, "ok": True}
    assert doctor.return_backoff([], entry, NOW) == ""
    assert "failed 1 minutes ago" in doctor.return_backoff(_fails(1, NOW), entry, NOW) and "3 hours" in doctor.return_backoff(_fails(1, NOW), entry, NOW)
    old = [{**f, "at": f["at"] - 4 * 3600} for f in _fails(1, NOW)]
    assert doctor.return_backoff(old, entry, NOW) == ""                                             # (one failure four hours ago: tried again)
    many = [{**f, "at": NOW - 4 * 3600 - i * 3600} for i, f in enumerate(_fails(3, NOW))]
    assert "3 returns failed in the last day" in doctor.return_backoff(many, entry, NOW)
    other = _fails(2, NOW, from_id=9, to_id=7)
    assert doctor.return_backoff(other, entry, NOW) == ""                                           # (a failed return of another server's tunnels is no reason)
    assert doctor.return_backoff([{**_fails(1, NOW)[0], "ok": True}], entry, NOW) == ""


def _world():
    """ir1 (irhome, the server that came back), ir2 (irnow, where the tunnel is now), one foreign server; the tunnel `x` on ir2, another tunnel `held` on ir1 holding one of its ports."""
    from nikatel.db import session
    from nikatel.models import Server, Tunnel

    with session() as db:
        ir1, ir2 = Server(name="irhome", host="203.0.113.5", role="iran", enabled=True, online=True), Server(name="irnow", host="203.0.113.8", role="iran", enabled=True, online=True)
        fo = Server(name="FRN", host="198.51.100.3", role="foreign", enabled=True, online=True)
        db.add_all([ir1, ir2, fo])
        db.flush()
        x = Tunnel(name="x", type="backpack", transport="wss", iran_server_id=ir2.id, foreign_server_id=fo.id, state="up", forwarded_ports=[8075, 11123])
        held = Tunnel(name="held", type="backhaul", transport="tcpmux", iran_server_id=ir1.id, foreign_server_id=fo.id, state="up", forwarded_ports=[8075, 1824])
        other = Tunnel(name="other", type="backhaul", transport="tcpmux", iran_server_id=ir1.id, foreign_server_id=fo.id, state="up", forwarded_ports=[9999])
        db.add_all([x, held, other])
        db.flush()
        return {"ir1": ir1.id, "ir2": ir2.id, "x": x.id, "held": held.id, "other": other.id}


def test_a_tunnel_is_not_returned_while_another_one_holds_its_port_on_that_server():
    ids = _world()
    why = doctor.return_clash(ids["x"], ids["ir1"])
    assert "port 8075 is held on that server by the tunnel held" in why and "11123" not in why
    assert doctor.return_clash(ids["other"], ids["ir2"]) == ""                                       # (nothing of irnow forwards 9999)
    assert doctor.return_clash(ids["x"], ids["ir2"]) == ""                                           # (a tunnel does not clash with itself)


def _returns_world(monkeypatch, with_failure=False):
    from nikatel.services import netwatch

    ids = _world()
    now = int(time.time())
    started = []
    monkeypatch.setattr(doctor, "can_move", lambda: True)
    monkeypatch.setattr(autopilot, "quiet_now", lambda: False)
    monkeypatch.setattr(netwatch, "report", lambda: {"iran": []})
    monkeypatch.setattr(doctor, "move_now", lambda tid, target, reason, why="", rails=True, prefer="": started.append((tid, target, reason)) or 1)
    s = doctor._state()
    s["moves"] = [{"id": "d1", "at": now - 3 * DAY, "reason": "down", "ok": True, "returned": False, "tunnels": ["x"], "from_id": ids["ir1"], "from": "irhome", "to_id": ids["ir2"], "to": "irnow"}]

    if with_failure:
        s["moves"].append({"id": "r1", "at": now - 1800, "reason": "return", "ok": False, "tunnels": [], "from_id": ids["ir2"], "to_id": ids["ir1"]})

    s["back"] = {str(ids["ir1"]): now - 2 * 3600}
    doctor._save(s)
    return ids, started


def test_the_return_loop_does_not_try_a_tunnel_whose_port_is_held_and_says_why_on_the_ledger(monkeypatch):
    ids, started = _returns_world(monkeypatch)
    doctor.returns()
    assert started == []
    entry = next(m for m in doctor._state()["moves"] if m["id"] == "d1")
    assert "port 8075 is held on that server" in entry["return_note"]
    from nikatel.db import session
    from nikatel.models import Tunnel

    with session() as db:
        db.delete(db.get(Tunnel, ids["held"]))

    doctor.returns()
    assert [x[:3] for x in started] == [(ids["x"], ids["ir1"], "return")]                           # (the port is free: the return is tried)


def test_the_return_loop_waits_after_a_failed_return(monkeypatch):
    ids, started = _returns_world(monkeypatch, with_failure=True)
    from nikatel.db import session
    from nikatel.models import Tunnel

    with session() as db:
        db.delete(db.get(Tunnel, ids["held"]))

    doctor.returns()
    assert started == []                                                                             # (a return failed half an hour ago)


# ------------------------------------------------------------------ the old tunnel of a move is not removed under its users
def _quiet_env(monkeypatch):
    from nikatel.db import session
    from nikatel.models import Server, Tunnel

    with session() as db:
        ir, fo = Server(name="i", host="203.0.113.5", role="iran"), Server(name="f", host="198.51.100.3", role="foreign")
        db.add_all([ir, fo])
        db.flush()
        t = Tunnel(name="bp", type="backpack", transport="wss", iran_server_id=ir.id, foreign_server_id=fo.id, state="up", forwarded_ports=[8075, 11123], rx_mbps=None, tx_mbps=None)
        db.add(t)
        db.flush()
        tid = t.id

    clock = [1000.0]
    monkeypatch.setattr(autopilot.time, "time", lambda: clock.__setitem__(0, clock[0] + 400) or clock[0])
    monkeypatch.setattr(autopilot.time, "sleep", lambda s: None)
    said = []
    monkeypatch.setattr(autopilot, "_say", lambda aid, line, done=None, ok=None: said.append(line))
    return tid, said


def test_a_tunnel_whose_counters_cannot_be_read_is_waited_for_by_its_open_connections(monkeypatch):
    tid, said = _quiet_env(monkeypatch)
    assert autopilot._wait_quiet(1, tid, 600) is True                                                # (as it was: no way to count, unreadable counters = quiet)
    assert autopilot._wait_quiet(1, tid, 600, conns=lambda: 1304) is False and any("1304 connection(s) are still open" in s for s in said)
    assert autopilot._wait_quiet(1, tid, 600, conns=lambda: None) is False and any("could not be counted" in s for s in said)
    assert autopilot._wait_quiet(1, tid, 600, conns=lambda: autopilot.QUIET_CONNS) is True


def test_a_tunnel_whose_counters_can_be_read_is_not_asked_for_its_connections(monkeypatch):
    from nikatel.db import session
    from nikatel.models import Tunnel

    tid, _said = _quiet_env(monkeypatch)

    with session() as db:
        db.get(Tunnel, tid).rx_mbps = 0.2

    assert autopilot._wait_quiet(1, tid, 600, conns=lambda: pytest.fail("counted though the counters are readable")) is True


def test_the_open_connections_of_a_tunnel_are_counted_on_its_iran_server(monkeypatch):
    from nikatel import ssh
    tid, _said = _quiet_env(monkeypatch)
    asked = []
    monkeypatch.setattr(ssh, "run_script", lambda node, name, params=None, timeout=0: asked.append((node.host, name, dict(params))) or "S 8075 30\nS 11123 323\n")
    assert _REAL_OPEN_CONNS(tid) == 353 and asked == [("203.0.113.5", "port_conns", {"PORTS": "8075 11123"})]
    monkeypatch.setattr(ssh, "run_script", lambda *a, **k: (_ for _ in ()).throw(ssh.SSHError("no route")))
    assert _REAL_OPEN_CONNS(tid) is None
    monkeypatch.setattr(ssh, "run_script", lambda *a, **k: "")
    assert _REAL_OPEN_CONNS(tid) is None and _REAL_OPEN_CONNS(987654) is None


def test_the_script_that_counts_connections_takes_ports_only():
    from nikatel import ssh

    script, rules = ssh.SCRIPTS["port_conns"]
    assert re.fullmatch(rules["PORTS"], "8075 1824 11123") and not re.fullmatch(rules["PORTS"], "8075; reboot") and not re.fullmatch(rules["PORTS"], "")
    assert "ss -Htn state established" in script and "reboot" not in script
