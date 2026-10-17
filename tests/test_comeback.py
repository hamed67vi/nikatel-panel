"""Back to the better tunnel (1.69.1; the owner, 2026-10-17: "a disturbance may be temporary - check whether the conditions got better and move to the tunnel that works better").
A cut tunnel is swapped to the first type that connects; the type it had is kept as the one wanted, tested again beside the running tunnel with growing waits, and the tunnel goes back when that type is clearly better."""
import time

import pytest
from sqlalchemy import select

from nikatel.config import get_settings
from nikatel.db import session
from nikatel.models import ActionLog, Event, Server, Tunnel
from nikatel.services import (
    actions,
    analyst,
    comeback,
    failover,
    knowledge,
    tunnel_test,
)
from nikatel.services import autopilot as ap
from nikatel.services import settings as st

NOW = 1_800_000_000


# ------------------------------------------------------------------ pure
def test_the_waits_grow_and_the_last_one_repeats():
    assert [comeback.wait_after(n) for n in range(9)] == [300, 600, 1200, 1800, 3600, 7200, 14400, 14400, 14400]
    assert comeback.wait_after(-1) == 300


def test_the_wish_is_kept_from_the_first_swap_and_dropped_when_the_tunnel_is_back_on_it():
    w = comeback.mark({}, "t1", "tcpmux", "bp:wssmux", "gre_blocked", "1:2", NOW)
    assert w == {"t1": {"kind": "tcpmux", "now": "bp:wssmux", "cause": "gre_blocked", "pair": "1:2", "left": NOW, "tries": 0, "next": NOW + 300}}
    w2 = comeback.mark({"t1": {**w["t1"], "tries": 3, "next": NOW + 9999}}, "t1", "bp:wssmux", "chisel:tls", "route_cut", "1:2", NOW + 500)              # cut again while it runs on the emergency type
    assert w2["t1"]["kind"] == "tcpmux" and w2["t1"]["now"] == "chisel:tls" and w2["t1"]["left"] == NOW and w2["t1"]["tries"] == 0 and w2["t1"]["next"] == NOW + 800
    assert comeback.mark(w2, "t1", "chisel:tls", "tcpmux", "route_cut", "1:2", NOW + 900) == {}                                                                  # swapped to the wanted type by a repair: nothing to wait for
    assert comeback.mark({}, "t2", "tcpmux", "tcpmux", "x", "1:2", NOW) == {}
    assert w == {"t1": w["t1"]} and "t2" not in w                                                                                                                # (the input is not changed)


def test_due_expired_and_the_limit_of_returns():
    e = {"next": NOW + 10, "left": NOW - comeback.GIVE_UP + 5}
    assert not comeback.due(e, NOW) and comeback.due(e, NOW + 10)
    assert not comeback.expired(e, NOW) and comeback.expired(e, NOW + 6)
    assert comeback.may_return([], NOW) and comeback.may_return([NOW - 100, NOW - 200], NOW) and not comeback.may_return([NOW - 100, NOW - 200, NOW - 300], NOW)
    assert comeback.may_return([NOW - 100, NOW - 200, NOW - comeback.RETURNS_WINDOW - 1], NOW)


def test_what_the_test_of_the_wanted_type_says():
    ok = {"kind": "tcpmux", "score": 80, "loss_pct": 0.0}
    assert comeback.outcome(40, [ok])[0] == "swap" and comeback.outcome(40, [ok])[1]["kind"] == "tcpmux"
    assert comeback.outcome(None, [ok])[0] == "swap"                                                      # (the running tunnel could not be measured: a clean connection counts)
    assert comeback.outcome(75, [ok]) == ("same", None)                                                   # only 5 points better: the running type is as good
    assert comeback.outcome(40, [{"kind": "tcpmux", "score": 80, "loss_pct": 20.0}]) == ("not yet", None)      # connects but loses packets: the disturbance is still there
    assert comeback.outcome(40, [{"kind": "tcpmux", "ok": False, "error": "no data passes"}]) == ("not yet", None)
    assert comeback.outcome(40, []) == ("not yet", None)


# ------------------------------------------------------------------ the world
def tunnel(name="cb-1", kind=("backpack", "wssmux"), state="up", since=None):
    with session() as db:
        n = len(db.scalars(select(Server)).all()) + 10
        ir, fr = Server(name="ir-" + name, host=f"203.0.113.{n}", role="iran", online=True), Server(name="fr-" + name, host=f"198.51.100.{n}", role="foreign", online=True)
        db.add_all([ir, fr])
        db.flush()
        t = Tunnel(name=name, type=kind[0], transport=kind[1], port=9906, iran_server_id=ir.id, foreign_server_id=fr.id, state=state, state_since=since if since is not None else int(time.time()) - 3600, forwarded_ports=[8072], record={})
        db.add(t)
        db.flush()
        return t.id, ir.id, fr.id


def want(name="cb-1", **over):
    with session() as db:
        t = db.scalar(select(Tunnel).where(Tunnel.name == name))
        pair = knowledge.pair_key(t.iran_server_id, t.foreign_server_id) if t is not None else "1:2"

    e = {"kind": "tcpmux", "now": "bp:wssmux", "cause": "gre_blocked", "pair": pair, "left": int(time.time()) - 600, "tries": 0, "next": int(time.time()) - 1, **over}
    st.put("comeback", {"wants": {**comeback.wants(), name: e}})
    return e


@pytest.fixture
def quiet_world(monkeypatch):
    said, spawned = [], []
    monkeypatch.setattr(ap, "_say", lambda aid, line, done=None, ok=None: said.append(line))
    monkeypatch.setattr(ap, "storm_now", lambda: False)
    monkeypatch.setattr(comeback, "_spawn", lambda name, tid: spawned.append(name) or 1)
    monkeypatch.setattr(actions, "doctor_mode", lambda: "fix")
    monkeypatch.setattr(ap, "repairing_names", list)
    monkeypatch.setattr(get_settings(), "read_only", False)
    return said, spawned


# ------------------------------------------------------------------ the job
def test_a_tunnel_whose_look_is_due_and_that_has_been_up_for_a_while_gets_a_look(quiet_world):
    _, spawned = quiet_world
    tunnel()
    want()
    comeback.run()
    assert spawned == ["cb-1"]


def test_nothing_is_looked_at_while_the_tunnel_is_not_ready_or_the_doctor_is_off(quiet_world, monkeypatch):
    _, spawned = quiet_world
    tunnel("cb-down", state="down")
    tunnel("cb-new", since=int(time.time()) - 30)
    tunnel("cb-wait")
    want("cb-down")
    want("cb-new")
    want("cb-wait", next=int(time.time()) + 500)
    comeback.run()
    assert spawned == []                                                                    # down / up for 30 s only / not due yet
    want("cb-new", next=0)
    monkeypatch.setattr(actions, "doctor_mode", lambda: "off")
    comeback.run()
    assert spawned == []
    monkeypatch.setattr(actions, "doctor_mode", lambda: "fix")
    monkeypatch.setattr(get_settings(), "read_only", True)
    comeback.run()
    assert spawned == []                                                                    # a read-only panel changes nothing
    monkeypatch.setattr(get_settings(), "read_only", False)
    monkeypatch.setattr(ap, "repairing_names", lambda: ["cb-wait"])
    want("cb-wait")
    comeback.run()
    assert spawned == []                                                                    # a repair works on it
    monkeypatch.setattr(ap, "repairing_names", list)
    monkeypatch.setattr(ap, "storm_now", lambda: True)
    comeback.run()
    assert spawned == []                                                                    # a storm of cuts: the servers are for the repairs


def test_one_look_at_a_time_and_the_one_that_waited_longest_goes_first(quiet_world):
    _, spawned = quiet_world
    tunnel("cb-a")
    tunnel("cb-b")
    want("cb-a", next=int(time.time()) - 10)
    want("cb-b", next=int(time.time()) - 100)
    comeback.run()
    assert spawned == ["cb-b"]
    comeback._RUNNING.add("cb-b")

    try:
        comeback.run()
        assert spawned == ["cb-b"]                                                          # (a look runs: no second one)
    finally:
        comeback._RUNNING.discard("cb-b")


def test_an_entry_of_a_tunnel_that_is_gone_or_three_days_old_is_dropped_and_a_tunnel_that_went_back_too_often_waits_six_hours(quiet_world):
    _, spawned = quiet_world
    tunnel("cb-old")
    tunnel("cb-often")
    want("cb-gone")                                                                         # (no such tunnel)
    want("cb-old", left=int(time.time()) - comeback.GIVE_UP - 10)
    want("cb-often")
    now = int(time.time())
    st.put("comeback", {"returns": {"cb-often": [now - 100, now - 200, now - 300]}})
    comeback.run()
    assert "cb-gone" not in comeback.wants() and "cb-old" not in comeback.wants()
    assert spawned == [] and comeback.wants()["cb-often"]["next"] >= now + 6 * 3600 - 5


# ------------------------------------------------------------------ the look
@pytest.fixture
def look_world(monkeypatch, quiet_world):
    said, _ = quiet_world
    calls = {"replace": [], "links": [], "tests": [], "busy_during_replace": None}
    ctl = {"results": [{"kind": "tcpmux", "score": 80, "loss_pct": 0.0, "latency_ms": 90.0}], "current": 40.0, "quiet": True, "replace_ok": True, "links_ok": True, "test_error": None}

    monkeypatch.setattr(comeback, "_score_of", lambda tid: ctl["current"])
    monkeypatch.setattr(ap, "_probe_links", lambda aid, snap, kinds: calls["links"].append(list(kinds)) or (list(kinds) if ctl["links_ok"] else []))

    def recommend(aid, iran_id, foreign_id, kinds, patience=1200, first_ok=False):
        calls["tests"].append((list(kinds), patience))

        if ctl["test_error"]:
            raise RuntimeError(ctl["test_error"])

        return ctl["results"]

    def replace(aid, tid, iran_id, foreign_id, kind, why, skip_offline=False, snap=None, first=True, restore=True, out=None):
        calls["replace"].append((kind, why))
        calls["busy_during_replace"] = ap._BUSY.get("cb-1") == aid
        return ctl["replace_ok"]

    monkeypatch.setattr(ap, "_recommend_now", recommend)
    monkeypatch.setattr(ap, "_replace", replace)
    monkeypatch.setattr(ap, "_wait_quiet", lambda aid, tid, limit=1800: ctl["quiet"])
    return said, calls, ctl


def test_the_wanted_type_is_back_and_clearly_better_so_the_tunnel_goes_back_at_a_quiet_moment(look_world):
    said, calls, _ = look_world
    tid, _, _ = tunnel()
    want()
    comeback.look(5, tid, "cb-1")
    assert calls["tests"] == [(["tcpmux"], 120)] and [c[0] for c in calls["replace"]] == ["tcpmux"] and calls["busy_during_replace"] is True      # (the tunnel is the look's own while it is swapped)
    assert "cb-1" not in ap._BUSY and comeback.wants() == {} and len(st.get("comeback")["returns"]["cb-1"]) == 1
    assert any("tcpmux is back: score 80 against 40.0 for bp:wssmux" in ln for ln in said) and said[-1] == "cb-1 is back on tcpmux."
    assert any(ln == "The test of tcpmux: score 80, delay 90.0 ms, loss 0.0 %; the running tunnel scores 40.0." for ln in said)         # (the numbers are in the story, not only the verdict)

    with session() as db:
        assert any(e.text.startswith("Tunnel cb-1 is back on tcpmux:") for e in db.scalars(select(Event)))


def test_a_wanted_type_that_does_not_connect_yet_is_looked_for_again_later_with_a_longer_wait(look_world):
    said, calls, ctl = look_world
    tid, _, _ = tunnel()
    want()
    ctl["results"] = [{"kind": "tcpmux", "ok": False, "error": "no data passes"}]
    t0 = int(time.time())
    comeback.look(5, tid, "cb-1")
    e = comeback.wants()["cb-1"]
    assert calls["replace"] == [] and e["tries"] == 1 and t0 + 600 - 3 <= e["next"] <= t0 + 600 + 3 and any("the disturbance is not over. Next look in 10 min" in ln for ln in said)
    ctl["results"] = [{"kind": "tcpmux", "score": 80, "loss_pct": 25.0}]                                    # connects but loses a quarter of the packets: still the disturbance
    comeback.look(6, tid, "cb-1")
    assert calls["replace"] == [] and comeback.wants()["cb-1"]["tries"] == 2


def test_a_wanted_type_that_is_only_as_good_ends_the_looking_and_nothing_is_swapped(look_world):
    said, calls, ctl = look_world
    tid, _, _ = tunnel()
    want()
    ctl["results"] = [{"kind": "tcpmux", "score": 46, "loss_pct": 0.0}]
    comeback.look(5, tid, "cb-1")
    assert calls["replace"] == [] and comeback.wants() == {} and any("not clearly better than bp:wssmux" in ln and "the looking stops" in ln for ln in said)


def test_a_tunnel_somebody_changed_or_that_is_back_is_not_touched(look_world):
    said, calls, _ = look_world
    tid, _, _ = tunnel()
    want(now="chisel:tls")                                                                                # the Doctor left it on chisel:tls; it runs bp:wssmux: a swap by hand or a later repair
    comeback.look(5, tid, "cb-1")
    assert calls["tests"] == [] and calls["replace"] == [] and comeback.wants() == {} and any("is not touched" in ln for ln in said)
    tid2, _, _ = tunnel("cb-2", kind=("backhaul", "tcpmux"))
    want("cb-2")
    comeback.look(6, tid2, "cb-2")
    assert calls["tests"] == [] and comeback.wants() == {} and any("runs as tcpmux again" in ln for ln in said)


def test_a_link_that_still_does_not_pass_costs_no_test(look_world):
    said, calls, ctl = look_world
    tid, _, _ = tunnel()
    want(kind="wg:tcpmux")
    ctl["links_ok"] = False
    comeback.look(5, tid, "cb-1")
    assert calls["links"] == [["wg:tcpmux"]] and calls["tests"] == [] and comeback.wants()["cb-1"]["tries"] == 1 and any("link of wg:tcpmux still does not pass" in ln for ln in said)


def test_a_busy_test_place_is_not_a_look_and_a_busy_tunnel_waits_for_a_quiet_moment(look_world):
    said, calls, ctl = look_world
    tid, _, _ = tunnel()
    want()
    ctl["test_error"] = "another tunnel test is still running"
    t0 = int(time.time())
    comeback.look(5, tid, "cb-1")
    e = comeback.wants()["cb-1"]
    assert e["tries"] == 0 and t0 + comeback.BUSY_RETRY - 3 <= e["next"] <= t0 + comeback.BUSY_RETRY + 3
    ctl["test_error"] = None
    ctl["quiet"] = False
    comeback.look(6, tid, "cb-1")
    e = comeback.wants()["cb-1"]
    assert calls["replace"] == [] and e["tries"] == 0 and e["next"] >= t0 + 590 and "cb-1" not in ap._BUSY and any("waits for a quiet moment" in ln for ln in said)
    ctl["test_error"] = "no wireguard here"

    with pytest.raises(RuntimeError):                                                                     # (any other error is a stop, not a wait)
        comeback.look(7, tid, "cb-1")


def test_a_build_that_fails_puts_the_running_type_back_and_the_looking_goes_on(look_world):
    said, _, ctl = look_world
    tid, _, _ = tunnel()
    want()
    ctl["replace_ok"] = False
    comeback.look(5, tid, "cb-1")
    assert comeback.wants()["cb-1"]["tries"] == 1 and "cb-1" not in ap._BUSY and not st.get("comeback")["returns"] and any("The build of tcpmux failed" in ln for ln in said)


# ------------------------------------------------------------------ the smart repair keeps the wish
def test_a_cut_tunnel_that_the_smart_repair_swaps_to_another_type_keeps_the_wish_to_go_back(monkeypatch):
    said = []
    d = {"cause": "gre_blocked", "summary": "GRE (protocol 47) is filtered on this route", "kind": "tcpmux", "state": "down", "where": None, "ext_blocked": False, "dial_i": True, "dial_f": True}
    monkeypatch.setattr(ap, "diagnose", lambda tid: d)
    monkeypatch.setattr(ap, "_say", lambda aid, line, done=None, ok=None: said.append(line))
    monkeypatch.setattr(ap, "storm_now", lambda: False)
    monkeypatch.setattr(analyst, "hints_for", lambda tunnel, cause, steps: (list(steps), []))
    monkeypatch.setattr(ap.time, "sleep", lambda s: None)
    monkeypatch.setattr(ap, "_replace", lambda aid, tid, iran, foreign, kind, why, skip_offline=False, snap=None, first=True, restore=True, out=None: kind == "bp:wssmux")
    monkeypatch.setattr(ap, "_probe_links", lambda aid, snap, kinds: kinds)
    monkeypatch.setattr(ap, "_wait", lambda build_aid, limit=900: (True, ["Tunnel is ready."]))
    monkeypatch.setattr(ap, "_build_kind", lambda *a, **k: 1)
    monkeypatch.setattr(failover, "after_ladder", lambda *a, **k: False)
    monkeypatch.setattr(ap, "probe_other_iran", lambda *a, **k: None)
    monkeypatch.setattr(tunnel_test, "quality", lambda tid: {})
    tid, _, _ = tunnel("cb-cut", kind=("backhaul", "tcpmux"), state="down")
    assert ap.smart_fix(9, tid, "switch", "auto") is True
    e = comeback.wants()["cb-cut"]
    assert e["kind"] == "tcpmux" and e["now"] == "bp:wssmux" and e["cause"] == "gre_blocked" and e["tries"] == 0 and e["next"] > int(time.time()) + 250
    assert any("tcpmux is remembered as the type this tunnel should go back to" in ln for ln in said)


def test_the_look_is_an_action_of_its_own_and_does_not_take_the_repair_place(monkeypatch):
    tid, _, _ = tunnel()
    want()
    ran = []
    monkeypatch.setattr(comeback, "look", lambda aid, tunnel_id, name: ran.append((aid, tunnel_id, name)))
    aid = comeback._spawn("cb-1", tid)
    end = time.time() + 5

    while time.time() < end and "cb-1" in comeback._RUNNING:
        time.sleep(0.02)

    assert ran == [(aid, tid, "cb-1")] and "cb-1" not in ap._BUSY and "comeback cb-1" not in ap._BUSY and not comeback._RUNNING

    with session() as db:
        e = db.get(ActionLog, aid)
        assert e.ref == "cb-1" and e.action == "comeback" and e.source == "auto"
