"""The Doctor watches that every port of every tunnel carries a connection (1.69.209; the owner, 2026-11-04: "the Doctor must solve the problems of the connections by itself and not wait for me - it must keep an eye on all the configs")."""
import time
from pathlib import Path

from sqlalchemy import select
from test_failover import _servers_and_tunnels

from nikatel.db import session
from nikatel.models import Tunnel
from nikatel.services import autopilot, carry, failover
from nikatel.services import settings as st

IRAN = ("203.0.113.5", "203.0.113.9")


# ------------------------------------------------------------------ the pure parts
def test_the_ports_of_a_tunnel_and_the_answer_of_the_server_are_read_as_they_are():
    assert carry.mapping([8072, 1826, 11426], {"port_mapping": "8072,1826:1820,11426:8680"}) == [(8072, 8072), (1826, 1820), (11426, 8680)]
    assert carry.mapping(["x", 443], None) == [(443, 443)]
    assert carry.parse("CARRIES 8072\nCLOSED 11426\nNOCONNECT 1826\nnoise\nCLOSED x\n") == {8072: "CARRIES", 11426: "CLOSED", 1826: "NOCONNECT"}


def test_a_port_is_the_tunnels_fault_only_when_the_service_behind_it_answers():
    maps = [(8072, 8072), (11426, 8680), (1826, 1820)]
    iran = {8072: "CARRIES", 11426: "CLOSED", 1826: "NOCONNECT"}
    assert carry.verdict(maps, iran, {8680: "CARRIES", 1820: "CARRIES"}) == ([11426, 1826], [])
    assert carry.verdict(maps, iran, {8680: "CARRIES", 1820: "CLOSED"}) == ([11426], [1826])            # (the service closes a bare connection by itself: not the tunnel)
    assert carry.verdict(maps, iran, {}) == ([], [])                                                      # (the foreign server was not asked: no verdict)
    assert carry.verdict(maps, {8072: "CARRIES", 11426: "CARRIES"}, {8680: "CLOSED"}) == ([], [])      # (it carries: nothing to say, whatever the service does by itself)


def test_a_fault_is_sure_after_two_looks_over_enough_seconds_and_a_repair_waits_for_its_gap():
    s1 = carry.step({}, [11426], 1000)
    assert s1["fails"] == 1 and s1["confirmed"] is False
    s2 = carry.step(s1, [11426], 1000 + carry.CONFIRM_FOR + 5)
    assert s2["fails"] == 2 and s2["confirmed"] is True and s2["ports"] == [11426]
    assert carry.step(s1, [11426], 1000 + 20)["confirmed"] is False                                      # (two looks, but only twenty seconds: a service that restarts)
    assert carry.step(s2, [], 5000) == {}                                                                 # (it carries again: the state goes)
    assert carry.step({"told": True, "runs": 2, "last_run": 7, "fails": 3, "first": 1}, [1], 9)["runs"] == 2
    assert carry.heal_due(s1, 5000) is False and carry.heal_due(s2, 5000) is True
    assert carry.heal_due({**s2, "runs": 1, "last_run": 5000 - 60}, 5000) is False and carry.heal_due({**s2, "runs": 1, "last_run": 5000 - carry.HEAL_GAP}, 5000) is True
    assert carry.heal_due({**s2, "runs": carry.HEAL_FAST_RUNS, "last_run": 5000 - carry.HEAL_GAP}, 5000) is False and carry.heal_due({**s2, "runs": carry.HEAL_FAST_RUNS, "last_run": 5000 - carry.HEAL_SLOW_GAP}, 5000) is True


# ------------------------------------------------------------------ the job
def _world(monkeypatch, mode="fix", heal=True):
    from nikatel.config import get_settings

    monkeypatch.setattr(get_settings(), "read_only", False)
    monkeypatch.setattr(carry, "HEAL", heal)                                                                # (the repair by itself is off in production: the tests of the repair switch it on)
    monkeypatch.setattr(carry, "_MASS_TOLD", [0.0])
    monkeypatch.setattr(carry.jobs, "stopping", lambda: False)                                              # (an earlier test that shut the app down leaves the flag set)
    st.put("repair", {"mode": mode})
    ids = _servers_and_tunnels()

    with session() as db:
        for t in db.scalars(select(Tunnel)):
            t.state_since = int(time.time()) - 3600

            if t.name == "t2":
                t.record = {"port_mapping": "8075:2096"}

    started, told = [], []
    monkeypatch.setattr(autopilot, "_start", lambda label, fn, *a, **k: started.append((label, fn.__name__, a, k)) or 1)
    monkeypatch.setattr(carry, "_tell", lambda level, name, text: told.append((level, name, text)))
    return ids, started, told


def _answers(monkeypatch, iran, foreign):
    calls = []

    def look(server, ports):
        calls.append((server[1], sorted(ports)))
        table = iran if server[1] in IRAN else foreign
        return None if table is None else {p: table[p] for p in ports if p in table}

    monkeypatch.setattr(carry, "_look", look)
    return calls


def _state(name="t2"):
    with session() as db:
        return dict((db.scalar(select(Tunnel).where(Tunnel.name == name)).record or {}).get("carry") or {})


def _age(name, seconds):
    with session() as db:
        t = db.scalar(select(Tunnel).where(Tunnel.name == name))
        rec = dict(t.record)
        rec["carry"] = {**rec["carry"], "first": rec["carry"]["first"] - seconds}
        t.record = rec


def test_a_port_that_carries_nothing_is_seen_twice_told_once_and_repaired_by_itself(monkeypatch):
    ids, started, told = _world(monkeypatch)
    calls = _answers(monkeypatch, {8075: "CLOSED", 443: "CARRIES"}, {2096: "CARRIES"})
    carry.run()
    assert _state()["fails"] == 1 and _state()["confirmed"] is False and not told and not started
    assert ("203.0.113.50", [2096]) in calls                                                                # (the service is asked where the tunnel delivers to, not on the listen port)
    assert _state("t3") == {}                                                                               # (the other tunnel carries)
    _age("t2", 300)
    carry.run()
    assert _state()["confirmed"] is True and _state()["told"] is True and _state()["runs"] == 1
    assert [(lvl, n) for lvl, n, _ in told] == [("warn", "t2")] and "8075" in told[0][2] and "carries nothing" in told[0][2]
    assert [(s[0], s[1], s[2]) for s in started] == [("t2", "heal", (ids["t2"],))] and started[0][3] == {"action": "autopilot", "source": "auto"}
    carry.run()
    assert len(told) == 1 and len(started) == 1                                                             # (told once; the next repair waits for its gap)
    _answers(monkeypatch, {8075: "CARRIES", 443: "CARRIES"}, {2096: "CARRIES"})
    carry.run()
    assert _state() == {} and [(lvl, n) for lvl, n, _ in told] == [("warn", "t2"), ("ok", "t2")]


def test_the_repair_by_itself_is_off_the_watch_only_looks_and_tells_once(monkeypatch):      # (2026-11-04: it restarted, then rebuilt, ten healthy tunnels at once)
    assert carry.HEAL is False                                                                              # (what production has)
    _ids, started, told = _world(monkeypatch, heal=False)
    _answers(monkeypatch, {8075: "CLOSED", 443: "CARRIES"}, {2096: "CARRIES"})
    carry.run()
    _age("t2", 300)
    carry.run()
    carry.run()
    assert _state()["confirmed"] is True and _state()["told"] is True and "runs" not in _state()           # (confirmed and told, never started)
    assert not started and len(told) == 1 and "Only reported" in told[0][2] and "repairs it by itself" not in told[0][2]


def test_many_tunnels_flagged_in_one_look_are_the_test_or_the_network_nothing_is_kept_told_or_repaired(monkeypatch):
    _ids, started, told = _world(monkeypatch)
    monkeypatch.setattr(carry, "MASS_FLAGGED", 2)
    _answers(monkeypatch, {8075: "CLOSED", 443: "CLOSED"}, {2096: "CARRIES", 443: "CARRIES"})
    carry.run()
    carry.run()
    assert _state() == {} and _state("t3") == {} and not started                                           # (both flagged at once: no state)
    assert [(lvl, n) for lvl, n, _ in told] == [("info", "")] and "2 tunnels were flagged in one look" in told[0][2]      # (one event, not one per look)
    _answers(monkeypatch, {8075: "CLOSED", 443: "CARRIES"}, {2096: "CARRIES"})
    carry.run()
    assert _state()["fails"] == 1                                                                           # (one tunnel alone is still a fault to watch)


def test_a_service_that_does_not_answer_either_and_a_server_that_does_not_answer_are_not_a_fault_of_the_tunnel(monkeypatch):
    _ids, started, told = _world(monkeypatch)
    _answers(monkeypatch, {8075: "CLOSED"}, {2096: "CLOSED"})
    carry.run()
    assert _state() == {} and not started and not told
    _answers(monkeypatch, None, {2096: "CARRIES"})                                                          # (the Iran server did not answer)
    carry.run()
    assert _state() == {} and not started


def test_nothing_is_started_in_the_mode_off_while_busy_for_a_muted_tunnel_or_in_a_storm(monkeypatch):
    ids, started, _told = _world(monkeypatch, "off")
    calls = _answers(monkeypatch, {8075: "CLOSED"}, {2096: "CARRIES"})
    carry.run()
    assert not calls and _state() == {}                                                                     # (off: the servers are not even asked)
    st.put("repair", {"mode": "fix"})
    carry.run()
    _age("t2", 300)
    monkeypatch.setitem(autopilot._BUSY, "t2", 9)
    carry.run()
    assert _state()["confirmed"] is True and not started                                                    # (a repair of this tunnel runs: told, not started)
    monkeypatch.delitem(autopilot._BUSY, "t2")
    st.put("alerts", {"muted": ["t2"]})
    carry.run()
    assert not started
    st.put("alerts", {"muted": []})
    monkeypatch.setattr(autopilot, "storm_now", lambda: True)
    carry.run()
    assert not started
    monkeypatch.setattr(autopilot, "storm_now", lambda: False)
    carry.run()
    assert len(started) == 1 and started[0][2] == (ids["t2"],)


def test_a_tunnel_that_is_not_up_for_a_while_or_is_a_test_tunnel_is_not_asked(monkeypatch):
    _ids, _started, _told = _world(monkeypatch)

    with session() as db:
        db.scalar(select(Tunnel).where(Tunnel.name == "t2")).state_since = int(time.time()) - 30

    calls = _answers(monkeypatch, {8075: "CLOSED", 443: "CARRIES"}, {2096: "CARRIES"})
    carry.run()
    assert all(8075 not in ports for _h, ports in calls) and any(443 in ports for _h, ports in calls)       # (up for thirty seconds only: its services are still starting; t1 is down: not asked)
    assert all(80 not in ports for _h, ports in calls)


# ------------------------------------------------------------------ the repair
def _heal_world(monkeypatch, mode, ids=None):
    if ids is None:
        ids, _started, _told = _world(monkeypatch, mode)

    st.put("repair", {"mode": mode})
    said, steps = [], []
    monkeypatch.setattr(autopilot, "_say", lambda aid, line, done=None, ok=None: said.append((line, done, ok)))
    monkeypatch.setattr(carry.time, "sleep", lambda s: None)
    monkeypatch.setattr(carry, "_restart", lambda tid: steps.append("restart") or "2 unit(s) restarted")
    monkeypatch.setattr(autopilot, "_rebuild_safe", lambda aid, tid, **k: steps.append("rebuild") or (True, ["Built."]))
    monkeypatch.setattr(failover, "quality_move", lambda aid, tid, reason, now, rtt, say: steps.append("move") or True)
    return ids, said, steps


def test_the_repair_stops_at_the_first_step_after_which_the_ports_carry(monkeypatch):
    ids, said, steps = _heal_world(monkeypatch, "move")
    monkeypatch.setattr(carry, "_carries_now", lambda tid: True)
    carry.heal(5, ids["t2"])
    assert steps == ["restart"] and said[-1][1:] == (True, True) and "after the restart" in said[-1][0]
    steps.clear()
    answers = iter([False, True])
    monkeypatch.setattr(carry, "_carries_now", lambda tid: next(answers))
    carry.heal(5, ids["t2"])
    assert steps == ["restart", "rebuild"] and said[-1][1:] == (True, True) and "after the rebuild" in said[-1][0]


def test_the_repair_asks_for_another_iran_server_only_in_the_mode_move_and_says_when_nothing_helped(monkeypatch):
    ids, said, steps = _heal_world(monkeypatch, "move")
    monkeypatch.setattr(carry, "_carries_now", lambda tid: False)
    carry.heal(5, ids["t2"])
    assert steps == ["restart", "rebuild", "move"] and said[-1][1:] == (True, True) and "move to another Iran server was started" in said[-1][0]
    _ids, said, steps = _heal_world(monkeypatch, "fix", ids)
    monkeypatch.setattr(carry, "_carries_now", lambda tid: False)
    carry.heal(5, ids["t2"])
    assert steps == ["restart", "rebuild"] and said[-1][1:] == (True, False) and "A human should look at it" in said[-1][0] and any("only in the Doctor's mode" in s[0] for s in said)
    _ids, said, steps = _heal_world(monkeypatch, "move", ids)
    monkeypatch.setattr(autopilot, "_rebuild_safe", lambda aid, tid, **k: steps.append("rebuild") or (False, ["the build failed"]))
    monkeypatch.setattr(failover, "quality_move", lambda aid, tid, reason, now, rtt, say: steps.append("move") or False)
    monkeypatch.setattr(carry, "_carries_now", lambda tid: False)
    carry.heal(5, ids["t2"])
    assert steps == ["restart", "rebuild", "move"] and said[-1][1:] == (True, False)                      # (a failed rebuild goes on to the move; no move found: a human)


def test_the_job_and_the_action_are_wired():
    root = Path(__file__).resolve().parents[1]
    main = (root / "nikatel" / "main.py").read_text(encoding="utf-8")
    assert 'jobs.register("carry", carry.EVERY, carry.run)' in main
    arch = (root / "ARCHITECTURE.md").read_text(encoding="utf-8")
    assert "carry.py" in arch
