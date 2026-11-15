"""Two faults the review of the Doctor found in two weeks of a live panel (2026-11-15): a repair that started on the NEW tunnel of a running move (38 ended "tunnel not found"), and a smart repair that
lost its tunnel after its own successful rebuild (117 ended "tunnel not found" right after "Rebuild: Tunnel ... is ready")."""
import time

from sqlalchemy import select

from nikatel.config import get_settings
from nikatel.db import session
from nikatel.models import Server, Tunnel
from nikatel.services import actions, analyst, autopilot, knowledge, tunnels

HOSTS = iter(range(10, 250))


def servers(name):
    with session() as db:
        i = Server(name=f"ir-{name}", host=f"203.0.113.{next(HOSTS)}", role="iran", online=True)
        f = Server(name=f"fr-{name}", host=f"203.0.113.{next(HOSTS)}", role="foreign", online=True)
        db.add_all([i, f])
        db.flush()
        return i.id, f.id


def tunnel(name, ir, fr, state="down", made=None, port=1000):
    now = int(time.time())

    with session() as db:
        t = Tunnel(name=name, type="backhaul", transport="tcpmux", iran_server_id=ir, foreign_server_id=fr, state=state, state_since=now - 600, forwarded_ports=[port], **({"created_at": made} if made is not None else {}))
        db.add(t)
        db.flush()
        return t.id


def test_only_the_young_new_tunnel_of_a_move_is_the_moves_business():
    ir, fr = servers("m0")
    now = int(time.time())
    tunnel("x1-mv", ir, fr, made=now - 60, port=1001)
    tunnel("x2-mw", ir, fr, made=now - 1700, port=1002)
    tunnel("x3-mv", ir, fr, made=now - 1900, port=1003)
    tunnel("x4", ir, fr, made=now - 60, port=1004)
    assert tunnels.in_move("x1-mv") and tunnels.in_move("x2-mw")
    assert not tunnels.in_move("x3-mv")                    # a move that died half an hour ago: its leftover is repaired like any tunnel
    assert not tunnels.in_move("x4") and not tunnels.in_move("gone-mv") and not tunnels.in_move("x1-mv", now + 3600)


def test_the_plain_restart_and_the_smart_repair_leave_the_new_tunnel_of_a_running_move_alone(monkeypatch):
    ir, fr = servers("m1")
    now = int(time.time())
    young = tunnel("a1-mv", ir, fr, made=now - 120, port=1011)
    old = tunnel("a2-mv", ir, fr, made=now - 7200, port=1012)
    plain = tunnel("a3", ir, fr, port=1013)
    fixed, smart = [], []
    monkeypatch.setattr(get_settings(), "read_only", False)
    monkeypatch.setattr(actions, "doctor_mode", lambda: "move")
    monkeypatch.setattr(actions, "fix", lambda tid, source="manual": fixed.append(tid) or 1)
    actions.auto_repair()
    assert sorted(fixed) == sorted([old, plain]) and young not in fixed
    monkeypatch.setattr(autopilot, "outage_state", lambda: {"storm": False})
    monkeypatch.setattr(autopilot, "escalate_due", lambda *a, **k: True)
    monkeypatch.setattr(knowledge, "cut_before", lambda *a, **k: False)
    monkeypatch.setattr(autopilot, "smart_fix_start", lambda tid, level="switch", source="manual": smart.append(tid) or 1)
    autopilot.auto_escalate()
    assert sorted(smart) == sorted([old, plain]) and young not in smart


def test_the_row_of_a_tunnel_is_found_again_by_its_name_after_a_rebuild():
    ir, fr = servers("m2")
    a = tunnel("rb0", ir, fr, port=1021)
    assert autopilot._id_now("rb0", a) == a                                    # still there: the same row
    b = tunnel("rb0b", ir, fr, port=1022)

    with session() as db:
        db.delete(db.get(Tunnel, a))
        db.flush()
        db.get(Tunnel, b).name = "rb0"

    assert autopilot._id_now("rb0", a) == b                                    # the row is gone: the one that carries the name now
    assert autopilot._id_now("nobody", 987654) == 987654                       # nothing carries the name: the caller says "not found" as before


def test_a_repair_that_rebuilt_the_tunnel_goes_on_with_its_new_row(monkeypatch):
    ir, fr = servers("m3")
    old = tunnel("rb1", ir, fr, port=1031)
    said, held, kept = [], [], []

    def rebuild(aid, tunnel_id, fresh_link=False, check=True):
        with session() as db:
            db.add(Tunnel(name="rb1-new", type="backhaul", transport="tcpmux", iran_server_id=ir, foreign_server_id=fr, state="up", forwarded_ports=[1032]))
            db.flush()
            db.delete(db.get(Tunnel, tunnel_id))
            db.flush()
            db.scalar(select(Tunnel).where(Tunnel.name == "rb1-new")).name = "rb1"

        return True, ["Plan: rb1", "Tunnel rb1 is ready."]

    monkeypatch.setattr(autopilot, "diagnose", lambda tid: {"cause": "units_down", "summary": "its services are stopped", "kind": "tcpmux", "state": "down", "where": None, "ext_blocked": False})
    monkeypatch.setattr(autopilot, "ladder_for", lambda cause, kind, level="switch", ext_blocked=False: ["rebuild"])
    monkeypatch.setattr(autopilot, "_rebuild_safe", rebuild)
    monkeypatch.setattr(autopilot, "_holds", lambda aid, tunnel_id, seconds=25: held.append(tunnel_id) or True)
    monkeypatch.setattr(autopilot, "_say", lambda aid, line, done=None, ok=None: said.append(line))
    monkeypatch.setattr(autopilot, "storm_now", lambda: False)
    monkeypatch.setattr(autopilot, "relapsed", lambda name, now: set())
    monkeypatch.setattr(analyst, "hints_for", lambda tunnel, cause, steps: (list(steps), []))
    monkeypatch.setattr(analyst, "record_case", lambda *a, **k: kept.append(a))
    monkeypatch.setattr(knowledge, "remember", lambda *a, **k: None)
    assert autopilot.smart_fix(7, old, "switch", "auto") is True

    with session() as db:
        new = db.scalar(select(Tunnel.id).where(Tunnel.name == "rb1"))

    assert new != old and held == [new]                                        # the check after the rebuild looks at the NEW row (it used to end the repair with "tunnel not found")
    assert said[-1] == "rb1: works again." and len(kept) == 1                  # ... so the repair is written down as what it was: healed, by a rebuild
