"""The Doctor and a tunnel whose bandwidth dropped and stays dropped (the owner, 2026-10-25, with a picture of a BackPack KCP tunnel at 53 Mb/s "dropped": "what is the problem of this tunnel - why does the Doctor not fix it?").

What the story of its look said: the restart did not help; the other types were tested (Backhaul tcp 726 Mb/s against 28 Mb/s of the tunnel) and the Doctor chose tcp - and then "NOT changed: port 25050 (the shifted port of 5050)
is already used on the Iran server". The port was the one of the tunnel's OWN standby, which the swap removes and builds again. Three things were wrong and are mended:
  1. the plan of a standby counted the tunnel's own standby as "another tunnel" (standby.build_plan);
  2. a drop that lasts was looked at once, at its beginning, and never again (bandwidth.relook_due: again after three hours, without a new restart of the engine and without a new event);
  3. the sentence of the look and the mark "the engine was restarted at ..." vanished at the next measurement (bandwidth.carry_notes)."""
import time

import pytest
from sqlalchemy import select

from nikatel.config import get_settings
from nikatel.db import session
from nikatel.models import Event, Server, Tunnel
from nikatel.services import autopilot, bandwidth, standby

NOW = 1_800_000_000
_N = iter(range(40, 200))


def world(name, forwarded=None, bw=None):
    n = next(_N)

    with session() as db:
        ir, fr = Server(name="ir-" + name, host=f"203.0.113.{n}", role="iran", online=True), Server(name="fr-" + name, host=f"198.51.100.{n}", role="foreign", online=True)
        db.add_all([ir, fr])
        db.flush()
        t = Tunnel(name=name, type="backpack", transport="kcp", port=9900 + n, iran_server_id=ir.id, foreign_server_id=fr.id, state="up", state_since=int(time.time()) - 3600, last_check_at=int(time.time()) - 2,
                   forwarded_ports=forwarded or [5050], record={"port_mapping": ",".join(str(p) for p in (forwarded or [5050])), **({"bw": bw} if bw else {})})
        db.add(t)
        db.flush()
        return {"id": t.id, "name": name, "iran": ir.id, "foreign": fr.id}


def add_standby(w, ports, name=None, kind="chisel"):
    with session() as db:
        t = Tunnel(name=name or w["name"] + "-sb", type=kind, transport="wss", enabled=False, port=9650, iran_server_id=w["iran"], foreign_server_id=w["foreign"], state="up", state_since=int(time.time()) - 600,
                   last_check_at=int(time.time()) - 2, forwarded_ports=ports, record={"standby_of": w["name"], "port_mapping": ",".join(f"{p}:5050" for p in ports), "kind": kind})
        db.add(t)
        db.flush()
        return t.id


# ------------------------------------------------------------------ 1. the plan of a standby
def test_the_standby_that_is_to_be_replaced_does_not_hold_the_port_of_its_replacement():
    w = world("rl1")
    add_standby(w, [25050])                                          # the tunnel's own standby: on the shifted port of 5050
    plan = standby.build_plan("rl1", "tcp")                          # (the swap asks for another type: it removes the old standby first - this used to say "port 25050 ... is already used")
    assert plan["ports"] == "25050:5050" and plan["mapping"] == {5050: 25050} and plan["kind"] == "tcp"


def test_another_tunnel_on_the_shifted_port_still_stops_the_plan():
    w = world("rl2")
    add_standby(w, [25050])
    other = world("rl2-other", forwarded=[25050])

    with session() as db:                                            # (a tunnel of somebody else on the same Iran server listens on 25050)
        db.get(Tunnel, other["id"]).iran_server_id = w["iran"]

    with pytest.raises(ValueError, match="already used"):
        standby.build_plan("rl2", "tcp")


def test_without_a_standby_nothing_changes():
    w = world("rl3")
    assert standby.build_plan("rl3", "tcp")["ports"] == "25050:5050" and w["id"]


# ------------------------------------------------------------------ 3. the notes of the Doctor
def test_the_sentence_and_the_mark_of_the_engine_survive_the_next_measurement():
    old = {"why": {"code": "this_only", "text": "Only this tunnel is slower", "at": NOW - 900}, "engine_fix_at": NOW - 900, "state": "drop", "down": 60.0}
    new = {"state": "drop", "down": 55.0, "drop": {"since": NOW - 1800}}
    kept = bandwidth.carry_notes(old, new, "")
    assert kept["why"] == old["why"] and kept["engine_fix_at"] == NOW - 900 and kept["down"] == 55.0
    assert "why" not in new                                                                               # (the answer of add_sample is not changed in place)


def test_a_new_episode_or_the_end_of_one_starts_without_the_old_sentence_but_the_engine_mark_stays():
    old = {"why": {"code": "x", "text": "t", "at": 1}, "engine_fix_at": 77}
    assert bandwidth.carry_notes(old, {"state": "drop"}, "drop") == {"state": "drop", "engine_fix_at": 77}            # (a new drop)
    assert bandwidth.carry_notes(old, {"state": "ok"}, "recovered") == {"state": "ok", "engine_fix_at": 77}          # (it ended)
    assert bandwidth.carry_notes(old, {"state": "ok"}, "") == {"state": "ok", "engine_fix_at": 77}                  # (not dropped any more)
    assert bandwidth.carry_notes(None, {"state": "ok"}, "") == {"state": "ok"}
    assert bandwidth.carry_notes({"why": old["why"]}, {"state": "slow"}, "") == {"state": "slow", "why": old["why"]}


def test_a_measurement_in_the_job_keeps_what_the_look_wrote():
    t0 = int(time.time())
    bw = {"m": bandwidth.METHOD, "how": "door", "down": 60.0, "at": t0 - 1200, "ref": 134.0, "state": "drop", "drop": {"since": t0 - 3000, "from": 134.0, "now": 60.0}, "suspect": False, "next_at": t0 - 5,
          "hist": [[t0 - 7200 + i * 600, 130.0, None] for i in range(8)], "why": {"code": "this_only", "text": "Only this tunnel is slower", "at": t0 - 1100}, "engine_fix_at": t0 - 1100}
    w = world("rl4", bw=bw)
    res = bandwidth._store(w["id"], 55.0, 40.0, "door", 41.0)
    assert res is not None and res[1] == ""

    with session() as db:
        stored = db.get(Tunnel, w["id"]).record["bw"]

    assert stored["state"] == "drop" and stored["why"]["code"] == "this_only" and stored["engine_fix_at"] == t0 - 1100


# ------------------------------------------------------------------ 2. a drop that lasts
def test_a_drop_that_lasts_is_due_for_another_look_three_hours_after_the_last():
    bw = {"state": "drop", "drop": {"since": NOW - 4 * 3600}}
    assert bandwidth.relook_due(bw, 0, NOW) is True                                                       # (no look since the panel started: the beginning of the drop counts)
    assert bandwidth.relook_due(bw, NOW - 3600, NOW) is False                                             # (looked at an hour ago)
    assert bandwidth.relook_due(bw, NOW - 3 * 3600, NOW) is True
    assert bandwidth.relook_due({"state": "drop", "drop": {"since": NOW - 3600}}, 0, NOW) is False       # (a young drop is looked at by its first look)
    assert bandwidth.relook_due({"state": "ok", "drop": None}, 0, NOW) is False and bandwidth.relook_due({"state": "slow", "slow": {"since": 1}}, 0, NOW) is False
    assert bandwidth.relook_due(None, 0, NOW) is False and bandwidth.relook_due({"state": "drop"}, 0, NOW) is False


def test_the_job_looks_again_at_a_lasting_drop_once_and_only_when_the_doctor_may_fix(monkeypatch):
    looks = []
    monkeypatch.setattr(bandwidth, "_investigate_later", lambda tid, relook=False: looks.append((tid, relook)))
    monkeypatch.setattr(bandwidth, "actions_mode_allows_fix", lambda: False)
    monkeypatch.setattr(bandwidth.time, "time", lambda: NOW)
    bw = {"state": "drop", "drop": {"since": NOW - 5 * 3600}}
    bandwidth._RELOOKED.clear()
    bandwidth._relook_later(5, bw)
    assert looks == []                                                                                    # (the Doctor is off or only watches: no second look)

    monkeypatch.setattr(bandwidth, "actions_mode_allows_fix", lambda: True)
    bandwidth._relook_later(5, bw)
    assert looks == [(5, True)]

    monkeypatch.setattr(bandwidth, "_investigate_later", lambda tid, relook=False: bandwidth._RELOOKED.__setitem__(tid, NOW) or looks.append((tid, relook)))
    bandwidth._RELOOKED[5] = NOW - 600
    bandwidth._relook_later(5, bw)
    assert looks == [(5, True)]                                                                           # (looked at ten minutes ago: not again)


def test_the_measuring_job_calls_it_only_for_a_tunnel_that_is_still_dropped(monkeypatch):
    called = []
    monkeypatch.setattr(bandwidth, "_relook_later", lambda tid, bw: called.append(tid))
    w = world("rl5", bw={"m": bandwidth.METHOD, "how": "door", "state": "ok", "down": 300.0, "at": NOW - 5400, "ref": 300.0, "drop": None, "suspect": False, "next_at": 1, "hist": [[NOW - 5400 * (8 - i), 300.0, None] for i in range(8)]})
    monkeypatch.setattr(bandwidth, "measure", lambda tid: ({"down": 90.0, "ref": 300.0, "state": "drop", "drop": {"since": 1}}, ""))
    monkeypatch.setattr(bandwidth, "_due", lambda now: [w["id"]])
    monkeypatch.setattr(bandwidth, "_server_busy_for", lambda tid, now: False)
    monkeypatch.setattr(bandwidth.jobs, "stopping", lambda: False)
    monkeypatch.setattr(get_settings(), "read_only", False)
    bandwidth.run()
    assert called == [w["id"]]

    called.clear()
    monkeypatch.setattr(bandwidth, "measure", lambda tid: ({"down": 90.0, "ref": 300.0, "state": "drop", "drop": {"since": 1}}, "drop"))
    monkeypatch.setattr(bandwidth, "_investigate_later", lambda tid: None)
    bandwidth.run()
    assert called == []                                                                                   # (a NEW drop has its first look, not a second one)


def test_the_second_look_does_not_restart_the_engine_again_and_tells_no_new_event(monkeypatch):
    w = world("rl6", bw={"m": bandwidth.METHOD, "how": "door", "state": "drop", "down": 60.0, "at": int(time.time()) - 600, "ref": 134.0, "drop": {"since": int(time.time()) - 5 * 3600, "from": 134.0, "now": 60.0}, "suspect": False,
                         "next_at": 1, "hist": []})
    said, engine, swapped = [], [], []
    monkeypatch.setattr(autopilot, "_say", lambda aid, line, done=None, ok=None: said.append(line))
    monkeypatch.setattr(bandwidth, "explain", lambda f: ("this_only", "Only this tunnel is slower"))
    monkeypatch.setattr(bandwidth, "_engine_fix", lambda tid, say: engine.append(tid) or False)
    monkeypatch.setattr(bandwidth, "_swap_after_drop", lambda aid, tid, say: swapped.append(tid))

    bandwidth.investigate(1, w["id"])                                                                      # the first look: the engine is restarted, the problem is told
    assert engine == [w["id"]] and swapped == [w["id"]]

    with session() as db:
        first_events = [e.text for e in db.scalars(select(Event).where(Event.ref == "rl6"))]

    assert any("the bandwidth dropped - Only this tunnel is slower" in m for m in first_events)
    engine.clear()
    swapped.clear()
    said.clear()
    bandwidth.investigate(2, w["id"], True, True)                                                          # the second look: the types are tested again, nothing is restarted
    assert engine == [] and swapped == [w["id"]]
    assert said[0].startswith("rl6: still dropped - ") and "looks again" in said[0]

    with session() as db:
        events = [e.text for e in db.scalars(select(Event).where(Event.ref == "rl6"))]
        why = db.get(Tunnel, w["id"]).record["bw"]["why"]

    assert len(events) == len(first_events) and why["code"] == "this_only"                                # (no second warning for the same drop; the sentence is renewed)
