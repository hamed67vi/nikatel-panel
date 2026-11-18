"""A tunnel whose users suffer for hours while the tunnel itself is healthy gets a faster known type built BESIDE it (the owner, 2026-11-18, the Austria config: 51-75 % of its tests answered for a whole day while its tunnel
had no failed check and a steady delay; the Doctor said "the problem is beyond the tunnel" three times and did nothing - «بله، بساز»). The decisions are pure (`userq.swap_wanted`) or read the panel (`userq.retype_for_users`)."""
import time

import pytest

from nikatel.config import get_settings
from nikatel.db import session
from nikatel.models import Server, Tunnel
from nikatel.services import (
    actions,
    autopilot,
    bandwidth,
    doctor,
    swap,
    testbudget,
    userq,
)

NOW = 1_800_000_000
H = 3600


# ------------------------------------------------------------------ the pure rule
def _ux(grade="poor", since=NOW - 4 * H):
    return {"grade": grade, "since": since, "known": 1, "bad": [{"config": "c1", "issue": "unstable", "text": "answered only 80 % of its tests in the last 2 hours"}]}


def test_the_swap_for_suffering_users_is_wanted_only_after_hours_and_when_it_is_not_everywhere():
    f = userq.swap_wanted
    assert f(_ux(), NOW, ["poor", "good", "good", "good"]) == ""
    assert f(_ux("bad"), NOW, ["bad", "good", "good"]) == ""
    assert "not suffering" in f(_ux("fair"), NOW, ["fair"])
    assert "not suffering" in f(_ux("good"), NOW, ["good"])
    assert "under 3 hours" in f(_ux(since=NOW - 2 * H), NOW, ["poor"])
    assert "under 3 hours" in f({"grade": "poor"}, NOW, ["poor"])                                  # (no since: not known how long)
    assert "many tunnels" in f(_ux(), NOW, ["poor", "bad", "good", "good"])                        # (half of the tunnels suffer: the probe or the foreign side, not one type)
    assert f(_ux(), NOW, ["poor", "good"]) == ""                                                   # (under three graded tunnels: no share is judged)
    assert f(_ux(), NOW, ["poor", "poor", "good", "good", "good", "good"]) == ""                   # (a third exactly is allowed)


# ------------------------------------------------------------------ the job
def _world(grade="poor", since=NOW - 4 * H, more=()):
    """One Iran, one foreign server, the tunnel `t1` (its users suffer) and `more`: [(name, grade)] other tunnels of the fleet."""
    with session() as db:
        ir, fi = Server(name="ir1", host="203.0.113.10", role="iran", online=True), Server(name="fi1", host="203.0.113.50", role="foreign", online=True)
        db.add_all([ir, fi])
        db.flush()
        ids = {}

        for i, (name, g, s) in enumerate([("t1", grade, since), *[(n, gr, NOW - 5 * H) for n, gr in more]]):
            t = Tunnel(name=name, type="backhaul", transport="tcpmux", iran_server_id=ir.id, foreign_server_id=fi.id, state="up", forwarded_ports=[8000 + i],
                       record={"ux": {"grade": g, "since": s, "known": 1, "bad": [{"config": "c1", "issue": "unstable", "text": "answered only 80 % of its tests in the last 2 hours"}] if g in ("poor", "bad") else []}})
            db.add(t)
            db.flush()
            ids[name] = t.id

        return ids


@pytest.fixture
def calm(monkeypatch):
    """The Doctor is in fix mode, nothing disturbs it, the tunnel is healthy, one faster type is known; `started` collects the stories that would be started."""
    started = []
    monkeypatch.setattr(get_settings(), "read_only", False)
    monkeypatch.setattr(actions, "doctor_mode", lambda: "fix")
    monkeypatch.setattr(doctor, "mode", lambda: "fix")
    monkeypatch.setattr(autopilot, "quiet_now", lambda: False)
    monkeypatch.setattr(swap, "at_once", lambda: 0)
    monkeypatch.setattr(testbudget, "allow", lambda level="routine", now=None: True)
    monkeypatch.setattr(autopilot, "_snapshot", lambda tid: {"id": tid, "name": "t1", "type": "backhaul", "transport": "tcpmux", "record": {}, "iran_id": 1, "foreign_id": 2})
    monkeypatch.setattr(userq, "_facts", lambda tid: {"name": "t1", "state": "up", "now": 3.7, "live": 0, "iran": {"name": "ir1", "cpu": 7}, "foreign": {"name": "fi1", "cpu": 9}, "loss": 0.0, "rtt": 93.0, "rtt_ref": 93.0, "dns_ms": None,
                                                    "no_service": [], "bw_state": None, "bw_text": "3.7 Mb/s", "n_bad": 1, "known": 1, "ux": _ux()})
    monkeypatch.setattr(bandwidth, "_faster_row", lambda snap, cur, f, now: (None, [{"kind": "wssmux", "down_mbps": 120.0, "steady": None, "at": NOW - 3600}]))
    monkeypatch.setattr(autopilot, "_start", lambda label, fn, *a, **k: started.append((label, fn.__name__, a, k)) or 1)
    monkeypatch.setattr(userq, "_UX_TRIED", {})
    return started


def test_a_tunnel_whose_users_suffer_while_it_is_healthy_gets_the_faster_type_beside_it(calm):
    _world()
    assert userq.retype_for_users(NOW) == ["t1"]
    (label, fn, args, kw), = calm
    assert label == "t1" and fn == "_run_retype" and args[1:3] == ("wssmux", "tcpmux") and args[4] is True        # (tunnel id, the new kind, the old kind, why, auto)
    assert "have had a poor connection for 4 hours" in args[3] and "wssmux gave 120 Mb/s" in args[3] and kw == {"action": "autopilot", "source": "auto"}
    assert userq.retype_for_users(NOW + 600) == [] and len(calm) == 1                                          # (not again at the next look: UX_SWAP_GAP)
    assert userq.retype_for_users(NOW + userq.UX_SWAP_GAP + 1) == ["t1"]


def test_nothing_is_started_when_a_rail_says_no(calm, monkeypatch):
    _world()
    monkeypatch.setattr(actions, "doctor_mode", lambda: "off")
    assert userq.retype_for_users(NOW) == []
    monkeypatch.setattr(actions, "doctor_mode", lambda: "fix")
    monkeypatch.setattr(get_settings(), "read_only", True)
    assert userq.retype_for_users(NOW) == []
    monkeypatch.setattr(get_settings(), "read_only", False)
    monkeypatch.setattr(autopilot, "quiet_now", lambda: True)
    assert userq.retype_for_users(NOW) == []
    monkeypatch.setattr(autopilot, "quiet_now", lambda: False)
    monkeypatch.setattr(swap, "at_once", lambda: swap.MAX_AT_ONCE)
    assert userq.retype_for_users(NOW) == []
    monkeypatch.setattr(swap, "at_once", lambda: 0)
    monkeypatch.setattr(testbudget, "allow", lambda level="routine", now=None: False)
    assert userq.retype_for_users(NOW) == []
    monkeypatch.setattr(testbudget, "allow", lambda level="routine", now=None: True)
    monkeypatch.setattr(doctor, "_state", lambda: {"retype_done": {"t1": NOW - H}})                          # (changed by the Doctor an hour ago: once in 24 hours)
    assert userq.retype_for_users(NOW) == [] and calm == []


def test_a_young_grade_a_wide_trouble_a_protected_tunnel_and_an_unhealthy_tunnel_are_left_alone(calm, monkeypatch):
    ids = _world(since=NOW - H, more=[("t2", "poor"), ("t3", "poor"), ("t4", "good")])
    assert userq.retype_for_users(NOW) == []                                                                # (the grade is an hour old; t2 and t3 are old but half of the fleet suffers)

    with session() as db:
        db.get(Tunnel, ids["t1"]).record = {"ux": _ux(since=NOW - 5 * H)}

    assert userq.retype_for_users(NOW) == []                                                                # (now t1 is old enough but three of four tunnels suffer)

    with session() as db:
        for n in ("t2", "t3"):
            db.get(Tunnel, ids[n]).record = {"ux": _ux("good")}

        db.get(Tunnel, ids["t1"]).record = {"ux": _ux(since=NOW - 5 * H), "protected": True}

    assert userq.retype_for_users(NOW) == []                                                                # (a protected tunnel is never changed)

    with session() as db:
        db.get(Tunnel, ids["t1"]).record = {"ux": _ux(since=NOW - 5 * H)}

    f = userq._facts(0)
    monkeypatch.setattr(userq, "_facts", lambda tid: {**f, "state": "down"})
    assert userq.retype_for_users(NOW) == []                                                                # (the tunnel itself is down: that is a repair, not a retype)
    monkeypatch.setattr(userq, "_facts", lambda tid: {**f, "loss": 12.0})
    assert userq.retype_for_users(NOW) == [] and calm == []                                                 # (it loses packets: the lossy swap's business)


def test_only_a_type_that_is_not_known_to_be_unsteady_is_taken(calm, monkeypatch):
    _world()
    monkeypatch.setattr(bandwidth, "_faster_row", lambda snap, cur, f, now: (None, [{"kind": "tcp", "down_mbps": 300.0, "steady": 40}]))
    assert userq.retype_for_users(NOW) == []                                                                # (the memory calls it UNSTEADY)
    monkeypatch.setattr(bandwidth, "_faster_row", lambda snap, cur, f, now: (None, [{"kind": "tcp", "down_mbps": 300.0, "steady": 40}, {"kind": "paqet", "down_mbps": 90.0, "steady": 80}]))
    assert userq.retype_for_users(NOW) == ["t1"] and calm[0][2][1] == "paqet"                               # (the next one, proven steady)
    monkeypatch.setattr(userq, "_UX_TRIED", {})
    calm.clear()
    monkeypatch.setattr(bandwidth, "_faster_row", lambda snap, cur, f, now: (None, []))
    assert userq.retype_for_users(NOW) == [] and calm == []                                                 # (no faster type is known on this pair: the problem may well be beyond the tunnel)


def test_a_story_that_cannot_start_leaves_no_mark(calm, monkeypatch):
    _world()
    seen = []

    def refuse(*a, **k):
        seen.append(1)
        raise ValueError("a smart repair of this tunnel is already running")

    monkeypatch.setattr(autopilot, "_start", refuse)
    assert userq.retype_for_users(NOW) == [] and userq.retype_for_users(NOW + 60) == [] and len(seen) == 2    # (tried again at the next look: nothing was started, nothing is marked)


def test_the_grade_job_goes_on_when_the_look_fails(monkeypatch):
    def boom(now=None):
        raise RuntimeError("boom")

    monkeypatch.setattr(userq, "retype_for_users", boom)
    monkeypatch.setattr(userq, "read_all", lambda now=None: {})
    monkeypatch.setattr(userq, "_forget", lambda keep: 0)
    monkeypatch.setattr(userq.dnscache, "CACHE", {"at": time.time(), "rows": []})
    from nikatel.services import dnsheal

    monkeypatch.setattr(dnsheal, "look", lambda grades: None)
    userq.run()                                                                                              # (no exception: a failed look at the type must never stop the grade job)
    assert userq._RUNNING[0] is False
