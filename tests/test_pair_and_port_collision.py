"""Two tunnels of one mass outage collided on the Iran server they all moved to (2026-11-17 05:24, four Iran servers cut from abroad at once; two of the four failovers ended "not ok"): a build said "a tunnel between these
two servers is already being built" (another plan built the same pair at that moment) and another said "port 8072 is already in use on the Iran server" (the target already carried a tunnel on that port, or a plan of the same minute
was taking it). The target is now picked with the ports in mind, and a build that finds its pair busy waits for it."""
import pytest

from nikatel.db import session
from nikatel.models import Server, Tunnel
from nikatel.services import autopilot, failover

BUSY = "a tunnel between these two servers is already being built"


def _row(sid, name, quality=90, resources=80):
    return {"id": sid, "name": name, "quality": quality, "resources": resources, "tunnels": 1, "unused": 87.5, "score": 80, "rtt_ms": 40, "loss_pct": 0}


# ------------------------------------------------------------------ the pure choice
def test_a_server_whose_port_is_taken_is_passed_over_when_another_one_has_it_free():
    rankings = {1: [_row(101, "A", 95), _row(102, "B", 85)]}
    taken = {101: {8072}, 102: set()}
    target, passed, free = failover.pick_free_target(rankings, {8072}, lambda sid: taken[sid])
    assert target["id"] == 102 and passed == [("A", [8072])] and free is True
    assert failover.pick_free_target(rankings, {8075}, lambda sid: taken[sid])[0]["id"] == 101          # (another port: the best one, as before)
    assert failover.pick_free_target(rankings, set(), lambda sid: 1 / 0)[0]["id"] == 101                # (no ports to look at: nothing is read)


def test_when_no_server_has_the_port_free_the_best_one_stays_the_target():
    rankings = {1: [_row(101, "A", 95), _row(102, "B", 85)]}
    target, passed, free = failover.pick_free_target(rankings, {8072}, lambda sid: {8072})
    assert target["id"] == 101 and free is False and [n for n, _ in passed] == ["A", "B"]
    assert failover.pick_free_target({}, {8072}, lambda sid: set()) == (None, [], True)


# ------------------------------------------------------------------ with the panel's own tunnels and the plans of the same minute
def _world():
    with session() as db:
        servers = [Server(name=n, host=f"203.0.113.{10 + i}", role="iran", online=True) for i, n in enumerate(("dead1", "dead2", "dead3", "tgt-a", "tgt-b"))]
        fi = Server(name="fi1", host="203.0.113.50", role="foreign", online=True)
        db.add_all([*servers, fi])
        db.flush()
        ids = {s.name: s.id for s in servers}

        def add(name, iran, ports):
            t = Tunnel(name=name, type="backhaul", transport="tcpmux", iran_server_id=ids[iran], foreign_server_id=fi.id, state="up", forwarded_ports=ports)
            db.add(t)
            db.flush()
            return t.id

        out = {"ids": ids, "t1": add("t1", "dead1", [8072]), "t2": add("t2", "dead2", [8072]), "t3": add("t3", "dead3", [8075]), "x": add("x", "tgt-a", [8075])}
        return out


def _plan(w, server, tid):
    rows = [_row(w["ids"]["tgt-a"], "tgt-a", 95), _row(w["ids"]["tgt-b"], "tgt-b", 85)]
    return {"server_id": w["ids"][server], "server": server, "tunnels": [{"id": tid, "name": "t"}], "problems": [], "target": None, "_rankings": {tid: [dict(r) for r in rows]}}


def test_plans_of_one_minute_do_not_take_the_same_port_on_one_server():
    w = _world()
    book: dict = {}
    p1 = failover.assign_target(_plan(w, "dead1", w["t1"]), book)
    assert p1["target"]["name"] == "tgt-a" and "ports_note" not in p1                          # (the best server, the port is free there)
    p2 = failover.assign_target(_plan(w, "dead2", w["t2"]), book)
    assert p2["target"]["name"] == "tgt-b"                                                      # (t1 is on its way to tgt-a with port 8072: t2 goes to the other one)
    assert "tgt-b" in p2["ports_note"] and "tgt-a" in p2["ports_note"] and "8072" in p2["ports_note"]
    assert failover.coming_ports(book) == {w["ids"]["tgt-a"]: {8072}, w["ids"]["tgt-b"]: {8072}}


def test_a_port_that_a_tunnel_of_the_panel_already_forwards_there_is_not_taken():
    w = _world()
    p3 = failover.assign_target(_plan(w, "dead3", w["t3"]), {})
    assert p3["target"]["name"] == "tgt-b" and "8075" in p3["ports_note"]                       # (tgt-a carries `x` on 8075 already)


def test_when_every_server_clashes_the_best_one_is_kept_and_the_story_says_so():
    w = _world()

    with session() as db:
        db.add(Tunnel(name="y", type="backhaul", transport="tcpmux", iran_server_id=w["ids"]["tgt-b"], foreign_server_id=db.query(Server).filter_by(name="fi1").one().id, state="up", forwarded_ports=[8075]))

    p3 = failover.assign_target(_plan(w, "dead3", w["t3"]), {})
    assert p3["target"]["name"] == "tgt-a" and p3["ports_note"].startswith("no Iran server has these ports free")


def test_the_ports_are_given_back_with_the_reservation_and_an_old_one_expires():
    w = _world()
    p = failover.assign_target(_plan(w, "dead1", w["t1"]))
    assert failover.coming_ports() == {w["ids"]["tgt-a"]: {8072}}
    failover.release(p)
    assert failover.coming_ports() == {} and failover.held() == {}
    failover._RESERVED["old"] = (101, 1, 0.0, frozenset({8072}))
    assert failover.coming_ports() == {} and failover.held() == {}                              # (a plan that died in the middle holds nothing for ever)
    failover._RESERVED["short"] = (101, 3, 1e12)                                                # (the old shape of a reservation, no ports: still counted for its room)
    assert failover.held() == {101: 3} and failover.coming_ports() == {}


# ------------------------------------------------------------------ the build that finds its pair busy
def _snap():
    return {"ports": [], "name": "t1"}


def test_a_build_that_finds_the_pair_busy_waits_and_starts_when_it_is_free(monkeypatch):
    calls, pauses = [], []

    def fake(*a, **k):
        calls.append(1)

        if len(calls) < 3:
            raise ValueError(BUSY)

        return 42

    monkeypatch.setattr(autopilot.builder, "start_build", fake)
    monkeypatch.setattr(autopilot, "_pause", pauses.append)
    assert autopilot._build_kind(_snap(), 1, 2, "tcpmux") == 42
    assert len(calls) == 3 and pauses == [autopilot.PAIR_WAIT_SECS] * 2


def test_the_wait_has_an_end_and_other_refusals_are_not_waited_for(monkeypatch):
    calls, pauses = [], []

    def busy(*a, **k):
        calls.append(1)
        raise ValueError(BUSY)

    monkeypatch.setattr(autopilot.builder, "start_build", busy)
    monkeypatch.setattr(autopilot, "_pause", pauses.append)
    monkeypatch.setattr(autopilot, "PAIR_WAIT_TRIES", 3)

    with pytest.raises(ValueError, match="already being built"):
        autopilot._build_kind(_snap(), 1, 2, "tcpmux")

    assert len(calls) == 4 and len(pauses) == 3                                                 # (the first try and three more, then the refusal is the answer as before)

    def other(*a, **k):
        raise ValueError("both servers must be enabled")

    monkeypatch.setattr(autopilot.builder, "start_build", other)
    pauses.clear()

    with pytest.raises(ValueError, match="both servers must be enabled"):
        autopilot._build_kind(_snap(), 1, 2, "tcpmux")

    assert pauses == []


def test_the_try_build_of_a_repair_now_ends_in_a_started_build(monkeypatch):
    calls = []

    def fake(*a, **k):
        calls.append(1)

        if len(calls) == 1:
            raise ValueError(BUSY)

        return 7

    monkeypatch.setattr(autopilot.builder, "start_build", fake)
    monkeypatch.setattr(autopilot, "_pause", lambda s: None)
    monkeypatch.setattr(autopilot, "_wait", lambda aid, limit=900: (True, ["done"]))
    assert autopilot._try_build(_snap(), 1, 2, "tcpmux") == (True, ["done"])
