"""A move reads the REAL listening ports of the target Iran server before it builds anything (1.69.307; the owner, 2026-11-25: the Doctor's own move for quality was built on a server and refused at the build -
"port 8075 is already in use on the Iran server" - because a program of another tunnel listened on it and the panel's own record did not say so)."""
from test_failover import _quality_world, _row

from nikatel.db import session
from nikatel.models import Server
from nikatel.services import autopilot, doctor, failover


def _servers(*names):
    """More Iran servers that are on: {name: id}."""
    out = {}

    with session() as db:
        for i, n in enumerate(names):
            s = Server(name=n, host=f"203.0.113.{20 + i}", role="iran", online=True)
            db.add(s)
            db.flush()
            out[n] = s.id

    return out


def test_a_server_where_a_program_listens_on_the_port_is_passed_over_before_the_speed_test(monkeypatch):
    ids, said, started, tested = _quality_world(monkeypatch)
    more = _servers("ir3")
    monkeypatch.setattr(autopilot, "ranking", lambda tid: [_row(id=ids["ir2"], name="ir2"), _row(id=more["ir3"], name="ir3")])
    monkeypatch.setattr(failover, "_live_busy", lambda sid, ports: sorted(ports) if sid == ids["ir2"] else [])
    assert failover.quality_move(1, ids["t2"], "quality", 30.0, 120.0, said.append) is True
    assert [t[0] for t in tested] == [more["ir3"]]                                   # the speed test is made on the server whose ports are free, never on the one that would refuse the build
    assert started[0][1] == more["ir3"]
    assert any("ir2" in s and "8075" in s and "listening" in s and "next Iran server" in s for s in said)


def test_when_every_server_has_the_port_taken_nothing_is_tested_and_nothing_moves(monkeypatch):
    ids, said, started, tested = _quality_world(monkeypatch)
    more = _servers("ir3")
    monkeypatch.setattr(autopilot, "ranking", lambda tid: [_row(id=ids["ir2"], name="ir2"), _row(id=more["ir3"], name="ir3")])
    monkeypatch.setattr(failover, "_live_busy", lambda sid, ports: sorted(ports))
    assert failover.quality_move(1, ids["t2"], "quality", 30.0, 120.0, said.append) is False
    assert not tested and not started
    assert "ir2" in said[-1] and "ir3" in said[-1] and "8075" in said[-1]


def test_a_server_that_cannot_be_looked_at_is_not_a_target(monkeypatch):
    ids, said, started, tested = _quality_world(monkeypatch)

    def boom(sid, ports):
        raise RuntimeError("ssh: connection refused")

    monkeypatch.setattr(failover, "_live_busy", boom)
    assert failover.quality_move(1, ids["t2"], "quality", 30.0, 120.0, said.append) is False
    assert not tested and not started and any("could not be read" in s for s in said)


def test_only_a_few_servers_are_looked_at_in_one_turn(monkeypatch):
    ids, said, started, tested = _quality_world(monkeypatch)
    more = _servers("ir3", "ir4", "ir5")
    rows = [_row(id=ids["ir2"], name="ir2")] + [_row(id=v, name=k) for k, v in more.items()]
    asked = []
    monkeypatch.setattr(autopilot, "ranking", lambda tid: [dict(r) for r in rows])
    monkeypatch.setattr(failover, "_live_busy", lambda sid, ports: asked.append(sid) or sorted(ports))
    assert failover.quality_move(1, ids["t2"], "quality", 30.0, 120.0, said.append) is False
    assert len(asked) == failover.QUALITY_PORT_LOOKS == 3 and not tested and not started


def test_the_ports_a_cut_tunnel_leaves_behind_are_not_asked_for(monkeypatch):
    ids = _quality_world(monkeypatch)[0]
    asked = []
    monkeypatch.setattr(failover, "_live_busy", lambda sid, ports: asked.append(set(ports)) or sorted(ports))
    assert failover.plan_ports_problem({"tunnels": [{"id": ids["t2"]}], "drop": {ids["t2"]: [8075]}}, ids["ir2"]) == "" and asked == []          # nothing is left to ask for: no SSH look
    assert failover.plan_ports_problem({"tunnels": [{"id": ids["t2"]}], "drop": {str(ids["t2"]): [8075]}}, ids["ir2"]) == ""                     # (the keys are text after the plan went through JSON)
    assert "8075" in failover.plan_ports_problem({"tunnels": [{"id": ids["t2"]}]}, ids["ir2"]) and asked == [{8075}]


def test_a_move_whose_target_has_the_port_taken_builds_nothing(monkeypatch):
    ids = _quality_world(monkeypatch)[0]
    worked, lines = [], []
    monkeypatch.setattr(failover, "_work", lambda aid, p, finish=True: worked.append(p) or True)
    monkeypatch.setattr(autopilot, "_say", lambda aid, line, done=None, ok=None: lines.append((line, done, ok)))
    monkeypatch.setattr(doctor, "_purge_leftovers", lambda aid, target, name: None)
    monkeypatch.setattr(failover, "_live_busy", lambda sid, ports: [8075])
    doctor._run_move(1, ids["t2"], ids["ir2"], "quality", "why")
    assert worked == [] and lines[-1][1] is True and lines[-1][2] is False                      # the story ends, not ok
    assert "Not moved" in lines[-1][0] and "8075" in lines[-1][0] and "Nothing was built" in lines[-1][0]
    doctor._run_move(1, ids["t2"], ids["ir2"], "return", "")                                    # the Return puts a tunnel back where it was: the build tells the truth
    assert len(worked) == 1
    monkeypatch.setattr(failover, "_live_busy", lambda sid, ports: [])
    doctor._run_move(1, ids["t2"], ids["ir2"], "quality", "")
    assert len(worked) == 2
