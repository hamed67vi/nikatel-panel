"""Capacity (1.69.52): a tunnel whose users use nearly all that it can carry. The owner, 2026-10-20: "when the Doctor sees the capacity of a server filling, it must bring a tunnel that gives more bandwidth, without
cutting the customers; load balancing or any other idea that raises the capacity" - steps 1-3: the detection, the swap of the type without a cut (through the standby), the move to a lighter Iran server."""
import time
from pathlib import Path

import pytest

from nikatel.config import get_settings
from nikatel.db import session
from nikatel.models import Server, Tunnel, TunnelCheck
from nikatel.services import autopilot, capacity, doctor, failover, knowledge, standby
from nikatel.services import settings as st

WEB = Path(__file__).resolve().parent.parent / "nikatel" / "web"


@pytest.fixture(autouse=True)
def _quiet(monkeypatch):
    capacity._VIEW.update(at=0, tunnels={})
    capacity._PEAKS.update(at=0, rows={})
    monkeypatch.setattr(failover, "_notice", lambda text: None)
    monkeypatch.setattr(autopilot, "storm_now", lambda: False)


# ------------------------------------------------------------------ pure
def test_saturation_is_full_when_most_minutes_use_85_percent_of_the_capacity_and_unknown_when_the_capacity_is_not_what_limits_them():
    cap = 76.0
    assert capacity.saturation([69] * 10, cap)["state"] == "full"
    assert capacity.saturation([69] * 4 + [60] * 6, cap)["state"] == "warm"                   # 40 % of the minutes at 85 %: not yet full (needs 60 %), the typical minute is above 70 %
    assert capacity.saturation([30] * 10, cap)["state"] == "ok"
    assert capacity.saturation([69] * 5, cap)["state"] == "unknown"                           # too few readings
    assert capacity.saturation([69] * 10, None)["state"] == "unknown" and capacity.saturation([69] * 10, 0)["state"] == "unknown"
    assert capacity.saturation([69] * 10, cap, peak24=120)["state"] == "unknown"              # the users carried 120 through it: the test reads low, the capacity is not what limits them
    assert capacity.saturation([69] * 10, cap, peak24=80)["state"] == "full"                  # a peak a little above the test is still the test's capacity
    s = capacity.saturation([None, 69, 70, 68, 69, 70, 69, 68], cap)
    assert s["state"] == "full" and s["n"] == 7 and 0.85 < s["util"] < 1.0


def _tests(k, v, now, n=3):
    """n FULL tests (the last one now) of one kind: three passed tests make its steadiness known (knowledge.STEADY_MIN_TESTS)."""
    e = None

    for i in range(n):
        e = knowledge.merge(e, {"kind": k, "ok": True, "score": v[0], "down_mbps": v[1], "loss_pct": v[2]}, "full", now - 60 * (n - 1 - i))

    return e


def _pair(now, **kinds):
    """The Doctor's memory of a pair: {kind: (score, down_mbps, loss_pct)} as FULL tests made now (three each: steady)."""
    return {"kinds": {k: _tests(k, v, now) for k, v in kinds.items()}}


def test_a_better_type_is_one_that_succeeded_in_a_fresh_full_test_and_carried_clearly_more_with_little_loss():
    now = int(time.time())
    p = _pair(now, tcpmux=(60, 76, 0.5), **{"bp:wssmux": (70, 200, 0.2), "chisel:fwd": (65, 90, 0.1), "paqet": (80, 300, 5.0), "bpd:udp": (50, 400, 0.1)})
    rows = capacity.better_kinds(p, now, "tcpmux")
    assert [r["kind"] for r in rows] == ["bp:wssmux"]                                         # chisel:fwd gave 90 (needs 114), paqet lost 5 %, bpd:udp scored lower than tcpmux
    assert capacity.better_kinds(p, now, "tcpmux", allowed=lambda k: k != "bp:wssmux") == []   # the licence
    assert capacity.better_kinds(_pair(now, **{"bp:wssmux": (70, 200, 0.2)}), now, "tcpmux") == []      # the running type has no full test: nothing to compare with, nothing is guessed
    old = {"kinds": {**p["kinds"], "bp:wssmux": knowledge.merge(None, {"ok": True, "score": 70, "down_mbps": 200, "loss_pct": 0.2}, "full", now - 30 * 86400)}}
    assert capacity.better_kinds(old, now, "tcpmux") == []                                    # a month old is no news
    one = {"kinds": {**p["kinds"], "bp:wssmux": _tests("bp:wssmux", (70, 200, 0.2), now, n=1)}}
    assert capacity.better_kinds(one, now, "tcpmux") == []                                    # one good test proves no steadiness: not by itself (the owner, 2026-10-21)
    shaky = {"kinds": {**p["kinds"], "bp:wssmux": knowledge.merge(_tests("bp:wssmux", (70, 200, 0.2), now, n=2), {"kind": "bp:wssmux", "ok": False, "error": "no data"}, "full", now)}}
    assert capacity.better_kinds(shaky, now, "tcpmux") == []                                  # a type that failed in one of its tests is not steady


def test_a_lighter_iran_server_uses_a_smaller_share_of_what_its_tunnels_can_carry():
    items = [{"iran_id": 1, "use": 69, "cap": 76}, {"iran_id": 2, "use": 20, "cap": 200}, {"iran_id": 2, "use": 10, "cap": 100}, {"iran_id": 3, "use": 80, "cap": 100}, {"iran_id": 4, "use": 5, "cap": None}]
    by = capacity.loads(items)
    assert by == {1: pytest.approx(0.908, abs=0.001), 2: 0.1, 3: 0.8}                         # a tunnel with no capacity is left out
    cands = [{"id": 3, "name": "c"}, {"id": 2, "name": "b"}, {"id": 9, "name": "empty"}]
    assert [c["id"] for c in capacity.lighter(cands, by, by[1])] == [2, 9]                    # 3 uses 80 %: not lighter enough; a server with no tunnel counts as empty
    assert capacity.lighter(cands, by, None) == [cands[1], cands[2]]


# ------------------------------------------------------------------ the job
def _world(rx=69.0, cap=76.0, minutes=10, state="up", peak=None):
    """ir1 (Iran) carries t1 (capacity `cap`, users `rx` Mb/s for `minutes` minutes) to fi1; ir2 is the other Iran server."""
    now = int(time.time())

    with session() as db:
        ir1, ir2, fi1 = Server(name="ir1", host="203.0.113.5", role="iran", online=True), Server(name="ir2", host="203.0.113.9", role="iran", online=True), Server(name="fi1", host="203.0.113.50", role="foreign", online=True)
        db.add_all([ir1, ir2, fi1])
        db.flush()
        t = Tunnel(name="t1", type="backhaul", transport="tcpmux", iran_server_id=ir1.id, foreign_server_id=fi1.id, state=state, forwarded_ports=[8075], record={"bw": {"down": cap, "at": now, "state": "ok"}})
        db.add(t)
        db.flush()

        for k in range(minutes):
            db.add(TunnelCheck(tunnel_id=t.id, ts=now - 60 * k - 5, ok=True, rx_mbps=rx, tx_mbps=1.0))

        if peak:
            db.add(TunnelCheck(tunnel_id=t.id, ts=now - 3 * 3600, ok=True, rx_mbps=peak, tx_mbps=1.0))

        return {"ir1": ir1.id, "ir2": ir2.id, "fi1": fi1.id, "t1": t.id, "now": now}


def test_a_tunnel_that_stays_full_is_told_once_after_three_minutes_and_one_story_starts_in_the_modes_fix_and_move(monkeypatch):
    monkeypatch.setattr(get_settings(), "read_only", False)
    w = _world()
    started = []
    monkeypatch.setattr(autopilot, "_start", lambda label, fn, *a, **kw: started.append((label, a, kw)) or 1)
    st.put("repair", {"mode": "fix"})
    assert capacity.check(w["now"]) == [] and capacity.check(w["now"]) == []                    # the first two minutes: only counted
    told = capacity.check(w["now"])
    assert len(told) == 1 and "t1" in told[0] and "69 of the 76" in told[0] and "91 %" in told[0]
    assert started == [("capacity t1", (w["t1"],), {"action": "move", "source": "auto"})]
    assert capacity.view("t1")["state"] == "full" and capacity.view("t1")["cap"] == 76.0 and capacity.state_of("t1") == "full"
    assert capacity.check(w["now"]) == [] and len(started) == 1                                 # told once; one story in 24 hours
    assert st.get("capacity")["acted"]["t1"] >= w["now"]


def test_in_the_mode_off_the_capacity_is_only_told_and_nothing_starts(monkeypatch):
    monkeypatch.setattr(get_settings(), "read_only", False)
    w = _world()
    started = []
    monkeypatch.setattr(autopilot, "_start", lambda label, fn, *a, **kw: started.append(label) or 1)
    st.put("repair", {"mode": "off"})
    told = [capacity.check(w["now"]) for _ in range(3)]
    assert told[2] and started == []


def test_nothing_starts_in_a_storm_while_read_only_when_the_tunnel_is_busy_or_when_the_capacity_is_not_the_limit(monkeypatch):
    started = []
    monkeypatch.setattr(autopilot, "_start", lambda label, fn, *a, **kw: started.append(label) or 1)
    st.put("repair", {"mode": "move"})
    w = _world()
    monkeypatch.setattr(get_settings(), "read_only", True)
    [capacity.check(w["now"]) for _ in range(3)]
    assert started == []                                                                       # read-only
    monkeypatch.setattr(get_settings(), "read_only", False)
    monkeypatch.setattr(autopilot, "storm_now", lambda: True)
    capacity.check(w["now"])
    assert started == []                                                                       # a mass disruption: the Doctor does not start anything
    monkeypatch.setattr(autopilot, "storm_now", lambda: False)
    monkeypatch.setitem(autopilot._BUSY, "t1", 5)
    capacity.check(w["now"])
    assert started == []                                                                       # a repair works on it
    monkeypatch.delitem(autopilot._BUSY, "t1")
    capacity.check(w["now"])
    assert started == ["capacity t1"]


def test_a_tunnel_whose_users_carried_more_than_the_test_reads_is_not_called_full(monkeypatch):
    monkeypatch.setattr(get_settings(), "read_only", False)
    w = _world(peak=150)
    started = []
    monkeypatch.setattr(autopilot, "_start", lambda label, fn, *a, **kw: started.append(label) or 1)
    st.put("repair", {"mode": "move"})
    assert [capacity.check(w["now"]) for _ in range(4)] == [[], [], [], []] and started == [] and capacity.view("t1") == {}


def test_a_tunnel_that_is_down_or_has_no_measured_capacity_is_not_looked_at(monkeypatch):
    w = _world(state="down")
    assert capacity.snapshot(w["now"]) == []

    with session() as db:
        t = db.get(Tunnel, w["t1"])
        t.state = "up"
        t.record = {}

    assert capacity.snapshot(w["now"]) == []


# ------------------------------------------------------------------ the story: the swap without a cut
def _story_world(monkeypatch, standby_view, quiet=True, replaced=True):
    """The pieces of the swap faked and written into one list, in the order they happen."""
    w = _world()
    events, said = [], []
    now = w["now"]
    monkeypatch.setattr(get_settings(), "read_only", False)
    st.put("repair", {"mode": "fix"})
    monkeypatch.setattr(autopilot, "_say", lambda aid, line, done=None, ok=None: said.append((line, done, ok)))
    monkeypatch.setattr(knowledge, "pair", lambda i, f: _pair(now, tcpmux=(60, 76, 0.5), **{"bp:wssmux": (70, 200, 0.2)}))
    monkeypatch.setattr(standby, "_view", lambda name: {"p": {"id": w["t1"]}, "s": standby_view})
    capacity._VIEW.update(at=now, tunnels={"t1": {"state": "full", "util": 0.91, "cap": 76.0, "since": now, "n": 3}})          # (the suggestions of the page stand while the capacity is filling)
    monkeypatch.setattr(standby, "handover", lambda name, why="", manual=False: events.append(("handover", name, manual)) or {"ok": True})
    monkeypatch.setattr(standby, "give_back", lambda name, why="": events.append(("give_back", name)) or {"ok": True})
    monkeypatch.setattr(autopilot, "_wait_quiet", lambda aid, tid, limit=1800: events.append(("quiet", limit)) or quiet)
    monkeypatch.setattr(autopilot, "_replace", lambda aid, tid, iran_id, foreign_id, kind, why, **kw: events.append(("replace", kind)) or replaced)
    from nikatel.services import bandwidth

    monkeypatch.setattr(bandwidth, "measure", lambda tid: ({"down": 180}, ""))
    return w, events, said


def test_the_swap_without_a_cut_hands_the_new_connections_over_waits_for_the_old_ones_builds_the_better_type_and_takes_them_back(monkeypatch):
    w, events, said = _story_world(monkeypatch, {"kind": "bp:wssmux", "state": "up", "stale": "", "use": None})
    capacity._story(1, w["t1"])
    assert events == [("handover", "t1", True), ("quiet", capacity.DRAIN_LIMIT), ("replace", "bp:wssmux"), ("give_back", "t1")]          # nobody stands on a tunnel that does not work, at any step
    assert said[-1][1:] == (True, True) and "runs as bp:wssmux now" in said[-1][0] and "180" in said[-1][0]
    assert any("NEW connections" in x[0] for x in said) and doctor._state()["retype_done"].get("t1")                                    # (counts as the type change of the day)
    assert knowledge.pair  # (the memory of the pair got today's number)


def test_when_the_old_connections_do_not_end_nothing_is_cut_and_the_new_ones_go_back(monkeypatch):
    w, events, said = _story_world(monkeypatch, {"kind": "bp:wssmux", "state": "up", "stale": "", "use": None}, quiet=False)
    capacity._story(1, w["t1"])
    assert events == [("handover", "t1", True), ("quiet", capacity.DRAIN_LIMIT), ("give_back", "t1")] and said[-1][1:] == (True, False) and "NOT touched" in said[-1][0]


def test_when_the_new_type_does_not_build_the_old_one_is_back_and_the_users_go_back_to_the_tunnel(monkeypatch):
    w, events, said = _story_world(monkeypatch, {"kind": "bp:wssmux", "state": "up", "stale": "", "use": None}, replaced=False)
    capacity._story(1, w["t1"])
    assert events[-2:] == [("replace", "bp:wssmux"), ("give_back", "t1")] and said[-1][1:] == (True, False) and "Nobody was cut" in said[-1][0]


def test_a_standby_that_is_not_the_better_type_or_not_ready_is_never_swapped_and_nothing_is_suggested(monkeypatch):
    for view in ({"kind": "chisel:fwd", "state": "up", "stale": "", "use": None}, {"kind": "bp:wssmux", "state": "building", "stale": "", "use": None}, {"kind": "bp:wssmux", "state": "up", "stale": "", "use": {"since": 1}}, None):
        capacity._VIEW.update(at=0, tunnels={})
        st.put("doctor", {"suggestions": []})
        w, events, _said = _story_world(monkeypatch, view)
        monkeypatch.setattr(failover, "move_plan", lambda *a, **k: {"candidates": [], "problems": ["nobody has room"]})
        capacity._story(1, w["t1"])
        assert not any(e[0] in ("handover", "replace") for e in events)
        assert not [x for x in doctor.state()["suggestions"] if x["kind"] == "type"]                                      # (no suggestion any more, 1.69.83)
        assert any("the Doctor does not cut the users of a nearly full tunnel" in str(ln) for ln in _said)          # (it changes nothing by itself either: that change would cut the users; it looks for a lighter Iran server)
        with session() as db:
            for t in list(db.query(Tunnel)):
                db.delete(t)

            for s in list(db.query(Server)):
                db.delete(s)


# ------------------------------------------------------------------ the story: the move to a lighter Iran server
def test_a_lighter_iran_server_takes_the_tunnel_by_the_doctors_move_in_the_mode_move_and_as_a_suggestion_otherwise(monkeypatch):
    w, _events, said = _story_world(monkeypatch, None)
    monkeypatch.setattr(knowledge, "pair", lambda i, f: {})                                    # no better type is known: step 3
    plan = {"candidates": [{"id": w["ir2"], "name": "ir2", "ip": "203.0.113.9", "reason": "12 ms, 0% loss, 0 tunnels"}], "problems": []}
    monkeypatch.setattr(failover, "move_plan", lambda sid, target=None, tunnels=None: plan)
    moved = []
    monkeypatch.setattr(doctor, "move_now", lambda tid, to, reason="manual", why="", rails=True: moved.append((tid, to, reason, why)) or 9)
    st.put("repair", {"mode": "fix"})
    capacity._story(1, w["t1"])
    sug = doctor.state()["suggestions"]
    assert moved == [] and sug[0]["kind"] == "capacity" and sug[0]["to_id"] == w["ir2"] and sug[0]["to"] == "ir2" and "fills its 76 Mb/s" in sug[0]["why"] and said[-1][1:] == (True, True)       # fix: suggested ("Move now" on the page)
    st.put("doctor", {"suggestions": []})
    st.put("repair", {"mode": "move"})
    monkeypatch.setattr(doctor, "can_move", lambda: True)
    monkeypatch.setattr(failover, "move_blocked", lambda: "")
    capacity._story(1, w["t1"])
    assert len(moved) == 1 and moved[0][:3] == (w["t1"], w["ir2"], "capacity") and "ir2" in moved[0][3] and doctor.state()["suggestions"] == [] and "moves t1 to ir2" in said[-1][0]
    monkeypatch.setattr(failover, "move_blocked", lambda: "no DNS account is saved")           # the licence / the DNS account: suggested instead
    capacity._story(1, w["t1"])
    assert len(moved) == 1 and doctor.state()["suggestions"][0]["kind"] == "capacity"


def test_no_server_that_is_lighter_means_the_load_balancing_is_the_next_step(monkeypatch):
    w, _events, said = _story_world(monkeypatch, None)
    monkeypatch.setattr(knowledge, "pair", lambda i, f: {})
    heavy = Tunnel(name="t2", type="backhaul", transport="tcpmux", iran_server_id=w["ir2"], foreign_server_id=w["fi1"], state="up", forwarded_ports=[80], record={"bw": {"down": 100.0, "at": w["now"], "state": "ok"}})

    with session() as db:
        db.add(heavy)
        db.flush()

        for k in range(10):
            db.add(TunnelCheck(tunnel_id=heavy.id, ts=w["now"] - 60 * k - 5, ok=True, rx_mbps=80.0, tx_mbps=1.0))

    monkeypatch.setattr(failover, "move_plan", lambda *a, **k: {"candidates": [{"id": w["ir2"], "name": "ir2", "reason": "x"}], "problems": []})      # ir2 is a good target for the move's rules, but its tunnels use 80 %
    capacity._story(1, w["t1"])
    assert doctor.state()["suggestions"] == [] and said[-1][1:] == (True, False) and "No other Iran server is lighter" in said[-1][0] and "load balancing" in said[-1][0]


# ------------------------------------------------------------------ the page and the API
def test_the_api_the_suggestion_and_the_page_know_the_capacity(client):
    w = _world()
    capacity._VIEW.update(at=w["now"], tunnels={"t1": {"state": "full", "util": 0.91, "cap": 76.0, "since": w["now"], "n": 3}})
    row = next(t for t in client.get("/api/tunnels").json() if t["name"] == "t1")
    assert row["cap"] == {"state": "full", "util": 0.91, "cap": 76.0, "since": w["now"]}
    st.put("doctor", {"suggestions": [{"id": "s-1", "at": w["now"], "kind": "capacity", "tunnel_id": w["t1"], "tunnel": "t1", "from_id": w["ir1"], "from": "ir1", "to_id": w["ir2"], "to": "ir2", "why": "x"}]})
    assert doctor.refresh_suggestions(w["now"]) == []                                           # it stands while the capacity is filling
    capacity._VIEW.update(tunnels={"t1": {"state": "ok", "util": 0.4, "cap": 76.0, "since": w["now"], "n": 0}})
    assert doctor.refresh_suggestions(w["now"]) == ["t1: t1 no longer fills its capacity"] and doctor.state()["suggestions"] == []


def test_the_list_shows_an_amber_chip_for_a_tunnel_that_is_nearly_full_and_the_window_says_why():
    app, css, fa = (WEB / "app.js").read_text(encoding="utf-8"), (WEB / "lab.css").read_text(encoding="utf-8"), (WEB / "i18n.fa.ui2.js").read_text(encoding="utf-8")
    cell = app.split("const bwCell = t => {", 1)[1].split("const row = t => {", 1)[0]
    assert 'full = !prob && t.cap && t.cap.state === "full"' in app and 'full ? "nearly full" : idle ? "no users" : ""' in app and "bwMeterFor(t) + why" in cell and "const bwMeter = (t, tone, word, tip) =>" in app      # (1.69.55: the meter is drawn for every tunnel; the nearly full one keeps its word)
    assert "const capTip = t =>" in app and "${t.cap && t.cap.state === \"full\" ? `<div class=\"bw-why\">${esc(capTip(t))}</div>` : \"\"}" in app and 's.kind === "capacity" ? "capacity"' in app
    assert ".bwm.full .bwm-bar u" in css and ".bwbox .bwbig-track" in css and '"nearly full": "' in fa
