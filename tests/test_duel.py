"""The duel (services/duel.py, 1.69.268, 1.69.269): a tunnel and its warm standby tested one after the other until the better one is proven, then a change of type without a cut.
The owner, 2026-11-14: "what would you improve in the Doctor's speed test?" - "yes, build" the test of the tunnel and its standby back to back, and the confirm round that changes the type by itself; then
"yes" to the evening round (the lines slow down every evening) - the change itself starts in the quiet hours."""
import itertools
import time

import pytest
from sqlalchemy import select

from nikatel.config import get_settings
from nikatel.db import session
from nikatel.models import Server, Tunnel
from nikatel.services import (
    actions,
    autopilot,
    bandwidth,
    doctor,
    duel,
    knowledge,
    license,
    standby,
    testbudget,
)
from nikatel.services import settings as st

NOW = 1_800_000_000                         # 08:00 UTC = 11:30 in Tehran: the middle of the day
EVE = NOW + int(9.5 * 3600)                 # 21:00 in Tehran: the evening peak
QUIET = NOW + int(15.5 * 3600)              # 03:00 in Tehran of the next day: the quiet hours
MIN = 60
HOSTS = itertools.count(60)


def rnd(s, p, at=NOW, s_ms=100.0, p_ms=110.0, s_loss=0.0):
    return {"at": at, "p": p, "s": s, "p_ms": p_ms, "s_ms": s_ms, "p_loss": 0.0, "s_loss": s_loss, "first": "p"}


# ------------------------------------------------------------------ the clock of the panel
def test_the_hours_are_those_of_tehran_and_the_evening_and_the_quiet_hours_are_told_by_them():
    assert duel.hour_of(NOW) == 11 and duel.hour_of(EVE) == 21 and duel.hour_of(QUIET) == 3
    assert duel.hour_of(NOW, 0) == 8                                                               # (the panel's own offset: 0 = UTC)
    assert duel.in_evening(EVE) and not duel.in_evening(NOW) and not duel.in_evening(QUIET)
    assert duel.in_evening(EVE + 2 * 3600 + 59 * 60) and not duel.in_evening(EVE + 3 * 3600 + 1800)           # (20:00 up to, not including, 24:00)
    assert duel.in_quiet_hours(QUIET) and not duel.in_quiet_hours(EVE) and not duel.in_quiet_hours(NOW)


# ------------------------------------------------------------------ the rating of the rounds
def test_one_clearly_faster_round_asks_for_more_and_a_round_that_is_not_ends_the_case():
    assert duel.rate([]) == ("more", "")
    assert duel.rate([rnd(250, 121)])[0] == "more"
    assert duel.rate([rnd(130, 121)])[0] == "no"                                                  # (not 1.2 times as fast: nothing more is tested)
    assert duel.rate([rnd(250, 121), rnd(100, 120)])[0] == "no"                                   # (a second round that is not clearly faster ends it at once: no third test is wasted)


def test_three_clearly_faster_rounds_with_one_in_the_evening_make_an_upgrade():
    verdict, text = duel.rate([rnd(255, 121), rnd(224, 110), rnd(232, 130, at=EVE)])
    assert verdict == "upgrade" and "232" in text and "121" in text                                # (medians: 232 against 121; the rule asks 1.5 times and 50 Mb/s more)
    assert duel.rate([rnd(200, 150), rnd(190, 140), rnd(210, 160, at=EVE)])[0] == "no"            # (200 against 150: faster in every round, but not the 225 that a change is worth)


def test_three_day_rounds_wait_for_the_evening_before_they_make_an_upgrade():
    verdict, text = duel.rate([rnd(255, 121), rnd(224, 110), rnd(232, 130)])
    assert verdict == "evening" and "evening peak" in text and "20:00-24:00" in text               # (the line is slowest then: a type that wins at noon may lose at nine)


def test_loss_and_a_slower_answer_stop_the_upgrade():
    assert duel.rate([rnd(255, 121, s_loss=3.0), rnd(224, 110), rnd(232, 130, at=EVE)])[0] == "no"          # (more than UPGRADE_MAX_LOSS % lost in a round)
    slow = [rnd(255, 121, s_ms=300.0, p_ms=100.0), rnd(224, 110, s_ms=300.0, p_ms=100.0), rnd(232, 130, s_ms=300.0, p_ms=100.0, at=EVE)]
    assert duel.rate(slow)[0] == "no"                                                             # (it answers in 300 ms against 100: the ping is what the user feels first)
    assert duel.rate([rnd(255, 121, s_ms=None, p_ms=None), rnd(224, 110, s_ms=None, p_ms=None), rnd(232, 130, s_ms=None, p_ms=None, at=EVE)])[0] == "upgrade"      # (no ping known on one side: it does not decide)


def test_a_round_that_moved_nothing_on_the_tunnel_is_not_a_division_by_zero():
    assert duel.rate([rnd(250, 0.0), rnd(240, 0.0), rnd(260, 0.0, at=EVE)])[0] == "upgrade"        # (the tunnel carried nothing: the standby is, of course, clearly faster)
    assert duel.rate([rnd(0.0, 100.0)])[0] == "no"


# ------------------------------------------------------------------ the steps of one case
def case(state="run", rounds=(), at=NOW, p="bp:wssmux", s="bp:tcpmux"):
    return {"p_kind": p, "s_kind": s, "rounds": list(rounds), "state": state, "at": at}


def test_next_step_follows_the_state_of_the_case():
    assert duel.next_step(None, "a", "b", NOW) == "reset"
    assert duel.next_step(case(), "bp:wssmux", "bp:chisel", NOW) == "reset"                       # (the standby is another type now: a new case)
    assert duel.next_step(case(), "bp:wssmux", "bp:tcpmux", NOW) == "round"                       # (no round yet)
    assert duel.next_step(case(rounds=[rnd(250, 120, at=NOW - 10 * MIN)]), "bp:wssmux", "bp:tcpmux", NOW) == "wait"      # (the rounds are ROUND_GAP apart)
    assert duel.next_step(case(rounds=[rnd(250, 120, at=NOW - 41 * MIN)]), "bp:wssmux", "bp:tcpmux", NOW) == "round"
    assert duel.next_step(case(rounds=[rnd(250, 120, at=NOW - 5 * 3600)]), "bp:wssmux", "bp:tcpmux", NOW) == "reset"       # (a round of five hours ago says nothing about now)
    assert duel.next_step(case("no", at=NOW - 3600), "bp:wssmux", "bp:tcpmux", NOW) == "wait"
    assert duel.next_step(case("no", at=NOW - 4 * 86400), "bp:wssmux", "bp:tcpmux", NOW) == "reset"                          # (looked at again after three days)
    assert duel.next_step(case("upgrade", at=NOW - 3600), "bp:wssmux", "bp:tcpmux", NOW) == "start"
    assert duel.next_step(case("upgrade", at=NOW - 11 * 3600), "bp:wssmux", "bp:tcpmux", NOW) == "start"                     # (it waits up to 12 hours for the quiet hours)
    assert duel.next_step(case("upgrade", at=NOW - 13 * 3600), "bp:wssmux", "bp:tcpmux", NOW) == "reset"                     # (a verdict that the rails did not let start for that long is old)
    assert duel.next_step(case("started", at=NOW - 3600), "bp:wssmux", "bp:tcpmux", NOW) == "wait"


def test_a_case_with_its_three_rounds_waits_for_the_evening_and_is_kept_for_twenty_hours():
    day = case(rounds=[rnd(250, 120, at=NOW - 90 * MIN), rnd(240, 115, at=NOW - 50 * MIN), rnd(260, 125, at=NOW - 10 * MIN)])
    assert duel.next_step(day, "bp:wssmux", "bp:tcpmux", NOW) == "wait"                           # (the rounds are in, it is the middle of the day: wait)
    eve = case(rounds=[rnd(250, 120, at=EVE - 90 * MIN), rnd(240, 115, at=EVE - 50 * MIN), rnd(260, 125, at=EVE - 41 * MIN)])
    assert duel.next_step(eve, "bp:wssmux", "bp:tcpmux", EVE) == "round"                          # (the evening: the round it waited for)
    old = case(rounds=[rnd(250, 120, at=NOW - 10 * 3600), rnd(240, 115, at=NOW - 9 * 3600), rnd(260, 125, at=NOW - 8 * 3600)])
    assert duel.next_step(old, "bp:wssmux", "bp:tcpmux", NOW) == "wait"                           # (eight hours: still kept)
    older = case(rounds=[rnd(250, 120, at=NOW - 30 * 3600), rnd(240, 115, at=NOW - 29 * 3600), rnd(260, 125, at=NOW - 28 * 3600)])
    assert duel.next_step(older, "bp:wssmux", "bp:tcpmux", NOW) == "reset"                        # (a day and more: void)


def test_settled_adds_the_round_keeps_three_and_counts_the_changes_of_the_day():
    c = case()

    for i, (s, p) in enumerate(((250, 120), (240, 115), (260, 125))):
        c = duel.settled(c, rnd(s, p, at=EVE + i * 41 * MIN), EVE + i * 41 * MIN)

    assert c["state"] == "upgrade" and len(c["rounds"]) == 3 and c["at"] == EVE + 82 * MIN and "median" in c["text"]
    c = duel.settled(c, rnd(255, 118, at=EVE + 123 * MIN), EVE + 123 * MIN)
    assert len(c["rounds"]) == 3                                                                   # (only the last three)
    assert duel.settled(case(), rnd(100, 120), NOW)["state"] == "no"
    day = case()

    for i, (s, p) in enumerate(((250, 120), (240, 115), (260, 125))):
        day = duel.settled(day, rnd(s, p, at=NOW + i * 41 * MIN), NOW + i * 41 * MIN)

    assert day["state"] == "run" and "evening peak" in day["text"]                                 # (three day rounds: the case runs on, it waits for the evening)
    day = duel.settled(day, rnd(255, 118, at=EVE), EVE)
    assert day["state"] == "upgrade" and len(day["rounds"]) == 3                                   # (the evening round is in, and clearly faster: the verdict)
    cases = {"a": case("started", at=NOW - 3600), "b": case("started", at=NOW - 30 * 3600), "c": case("no", at=NOW - 60)}
    assert duel.started_today(cases, NOW) == 1                                                     # (one inside the last 24 hours)


# ------------------------------------------------------------------ the job
@pytest.fixture
def calm(monkeypatch):
    monkeypatch.setattr(duel.jobs, "stopping", lambda: False)
    monkeypatch.setattr(get_settings(), "read_only", False)
    monkeypatch.setattr(autopilot, "quiet_now", lambda: False)
    monkeypatch.setattr(actions, "doctor_mode", lambda: "move")
    monkeypatch.setattr(testbudget, "allow", lambda level, now=None: True)
    monkeypatch.setattr(knowledge, "not_now", lambda *a, **k: "")
    monkeypatch.setattr(bandwidth, "_server_busy_for", lambda tid, now: False)
    st.put("duel", {"on": True, "cases": {}})
    yield


def cand(name="x-tun", tid=1, sid=2):
    return {"name": name, "id": tid, "sid": sid, "iran_id": 1, "foreign_id": 2, "p_kind": "bp:wssmux", "s_kind": "bp:tcpmux"}


def drive(monkeypatch, rounds, times, started=None, why=""):
    """Run the job at each of `times`, with the next of `rounds` as the result of a round; `started` collects the changes that were asked for."""
    got = iter(rounds)
    started = [] if started is None else started
    monkeypatch.setattr(duel, "_candidates", lambda now: [cand()])
    monkeypatch.setattr(duel, "_measure_round", lambda c, parity: next(got))
    monkeypatch.setattr(duel, "_note", lambda c, case_: None)
    monkeypatch.setattr(duel, "_start_change", lambda c, case_, now: (started.append(c["name"]), why)[1])

    for t in times:
        duel.run(t)

    return started


def evening_times(n=4):
    return [EVE + i * 41 * MIN for i in range(n)]


def test_three_clearly_faster_evening_rounds_start_the_change_once(monkeypatch, calm):
    started = drive(monkeypatch, [rnd(250, 120, at=EVE), rnd(240, 115, at=EVE + 41 * MIN), rnd(260, 125, at=EVE + 82 * MIN)], evening_times())
    assert started == ["x-tun"]                                                                    # (the change starts after the third round, once)
    c = st.get("duel")["cases"]["x-tun"]
    assert c["state"] == "started" and len(c["rounds"]) == 3


def test_three_day_rounds_do_not_start_the_change_it_waits_for_a_round_in_the_evening(monkeypatch, calm):
    times = [NOW, NOW + 41 * MIN, NOW + 82 * MIN, NOW + 2 * 3600, EVE]
    rounds = [rnd(250, 120, at=NOW), rnd(240, 115, at=NOW + 41 * MIN), rnd(260, 125, at=NOW + 82 * MIN), rnd(255, 118, at=EVE)]
    started = drive(monkeypatch, rounds[:3], times[:4])
    assert started == [] and st.get("duel")["cases"]["x-tun"]["state"] == "run"                    # (three rounds at noon: no verdict yet, and no fourth round before the evening)
    started = drive(monkeypatch, rounds[3:], [EVE], started)
    assert started == ["x-tun"] and st.get("duel")["cases"]["x-tun"]["state"] == "started"         # (the evening round is taken, and the verdict is the upgrade)


def test_a_standby_that_is_not_clearly_faster_is_tested_once_and_left_alone(monkeypatch, calm):
    started = drive(monkeypatch, [rnd(130, 120, at=NOW)], [NOW, NOW + 41 * MIN])
    assert started == [] and st.get("duel")["cases"]["x-tun"]["state"] == "no"
    got = []
    monkeypatch.setattr(duel, "_measure_round", lambda c, parity: got.append(1) or rnd(300, 100))
    duel.run(NOW + 2 * 3600)
    assert got == []                                                                               # (nothing more is tested for three days)


def test_a_verdict_the_rails_do_not_let_start_waits_and_tries_again_without_holding_others_back(monkeypatch, calm):
    started = drive(monkeypatch, [rnd(250, 120, at=EVE), rnd(240, 115, at=EVE + 41 * MIN), rnd(260, 125, at=EVE + 82 * MIN)], evening_times(), why="the change waits for the quiet hours (01:00-07:00 Tehran)")
    assert st.get("duel")["cases"]["x-tun"]["state"] == "upgrade"                                  # (it stays an "upgrade" verdict)
    assert len(started) >= 2                                                                       # (and every later turn asks again until the rails open or the verdict is twelve hours old)
    got = []
    other = cand("y-tun", 7, 8)
    monkeypatch.setattr(duel, "_candidates", lambda now: [cand(), other])
    monkeypatch.setattr(duel, "_measure_round", lambda c, parity: got.append(c["name"]) or rnd(250, 120, at=EVE + 3 * 3600))
    duel.run(EVE + 3 * 3600)
    assert got == ["y-tun"]                                                                        # (the verdict of x-tun waits for the quiet hours; the round of y-tun is taken meanwhile)


def test_the_switch_the_mode_off_and_a_failover_stop_the_job(monkeypatch, calm):
    asked = []
    monkeypatch.setattr(duel, "_candidates", lambda now: asked.append(1) or [])
    st.put("duel", {"on": False})
    duel.run(NOW)
    st.put("duel", {"on": True})
    monkeypatch.setattr(actions, "doctor_mode", lambda: "off")
    duel.run(NOW)
    monkeypatch.setattr(actions, "doctor_mode", lambda: "move")
    monkeypatch.setattr(autopilot, "quiet_now", lambda: True)
    duel.run(NOW)
    assert asked == []
    monkeypatch.setattr(autopilot, "quiet_now", lambda: False)
    duel.run(NOW)
    assert asked == [1]


def test_the_day_s_volume_and_busy_servers_hold_a_round_back_and_the_evening_allows_more_traffic(monkeypatch, calm):
    got, asked = [], []
    monkeypatch.setattr(duel, "_candidates", lambda now: [cand()])
    monkeypatch.setattr(duel, "_measure_round", lambda c, parity: got.append(1) or rnd(250, 120))
    monkeypatch.setattr(duel, "_note", lambda c, case_: None)
    monkeypatch.setattr(testbudget, "allow", lambda level, now=None: False)
    duel.run(NOW)
    monkeypatch.setattr(testbudget, "allow", lambda level, now=None: True)
    monkeypatch.setattr(knowledge, "not_now", lambda *a, **k: "its servers carry 400 Mb/s")
    duel.run(NOW)
    assert got == []
    monkeypatch.setattr(knowledge, "not_now", lambda iran, foreign, now, quiet=0: asked.append(quiet) or "")
    st.put("duel", {"cases": {}})
    duel.run(NOW)
    st.put("duel", {"cases": {}})
    duel.run(EVE)
    assert got == [1, 1] and asked == [knowledge.VERIFY_QUIET_MBPS, duel.EVENING_QUIET_MBPS]      # (a round of the day waits under 150 Mb/s of users, one of the evening under 450: the line is what is measured)


# ------------------------------------------------------------------ the rails of the change
def test_the_change_starts_through_the_doctors_own_no_cut_path_and_obeys_its_rails(monkeypatch, calm):
    calls = []
    monkeypatch.setattr(autopilot, "_start", lambda label, fn, *args, **kw: calls.append((label, fn, args, kw)) or 1)
    monkeypatch.setattr(license, "tunnel_kind_allowed", lambda k: True)
    c = cand()
    k = case("upgrade", rounds=[rnd(250, 120, at=EVE - 2 * 3600), rnd(240, 115, at=EVE - 80 * MIN), rnd(260, 125, at=EVE - 40 * MIN)])
    k["text"] = "3 rounds: the standby gave 250 Mb/s (median), the tunnel 120"
    assert "quiet hours" in duel._start_change(c, k, NOW) and calls == []                          # (the middle of the day: the change waits for the quiet hours)
    assert "quiet hours" in duel._start_change(c, k, EVE) and calls == []                          # (the evening peak: the same)
    monkeypatch.setattr(doctor, "may_retype", lambda name, retyped, now, gap=doctor.RETYPE_GAP: "x-tun was changed by the Doctor 3 hours ago")
    assert "3 hours ago" in duel._start_change(c, k, QUIET) and calls == []                        # (once in 24 hours per tunnel)
    monkeypatch.setattr(doctor, "may_retype", lambda name, retyped, now, gap=doctor.RETYPE_GAP: "")
    st.put("doctor", {"retype_done": {"other": QUIET - 600}})
    assert "less than an hour" in duel._start_change(c, k, QUIET) and calls == []                  # (one change an hour for the whole panel)
    st.put("doctor", {"retype_done": {}})
    st.put("duel", {"cases": {"a": case("started", at=QUIET - 3600), "b": case("started", at=QUIET - 7200)}})
    assert "duels in the last 24 hours" in duel._start_change(c, k, QUIET) and calls == []         # (at most two a day)
    st.put("duel", {"cases": {}})
    monkeypatch.setattr(actions, "doctor_mode", lambda: "off")
    assert duel._start_change(c, k, QUIET) and calls == []                                         # (the Doctor is off)
    monkeypatch.setattr(actions, "doctor_mode", lambda: "move")
    assert duel._start_change(c, k, QUIET) == ""
    label, fn, args, kw = calls[0]
    assert label == "x-tun" and fn is doctor._run_retype and args[:3] == (1, "bp:tcpmux", "bp:wssmux") and args[4] is True      # (the type to change to, the type it was, the sentence, auto = the no-cut change)
    assert kw == {"action": "autopilot", "source": "auto"} and "cuts nobody" in args[3] and "3 rounds" in args[3]


# ------------------------------------------------------------------ the candidates
def make(name, typ, transport, standby_of=None, enabled=True, state_since=0, iran=None, foreign=None, extra=None):
    n = next(HOSTS)

    with session() as db:
        if iran is None:
            ir = Server(name=f"ir-{n}", host=f"203.0.113.{n}", role="iran", online=True)
            fr = Server(name=f"fr-{n}", host=f"198.51.100.{n}", role="foreign", online=True)
            db.add_all([ir, fr])
            db.flush()
            iran, foreign = ir.id, fr.id

        rec = {**({"standby_of": standby_of} if standby_of else {}), **(extra or {})}
        t = Tunnel(name=name, type=typ, transport=transport, port=9910 if standby_of else 9901, iran_server_id=iran, foreign_server_id=foreign, state="up", state_since=state_since, forwarded_ports=[8076], foreign_tunnel_ip=f"10.2.{n}.2",
                   record=rec, secrets={}, enabled=enabled)
        db.add(t)
        db.flush()
        return iran, foreign


def test_a_tunnel_with_an_old_enough_standby_of_another_type_is_a_candidate(monkeypatch, calm):
    monkeypatch.setattr(license, "tunnel_kind_allowed", lambda k: True)
    monkeypatch.setattr(standby, "_stale", lambda t, p: "")                                         # (the ports of the standby are the standby job's own business)
    now = int(time.time())
    old = now - 10 * 3600
    ir, fr = make("duel-a", "backpack", "wssmux", state_since=old)
    make("duel-a-sb", "backpack", "tcpmux", standby_of="duel-a", enabled=False, state_since=old, iran=ir, foreign=fr)
    got = [c for c in duel._candidates(now) if c["name"] == "duel-a"]
    assert len(got) == 1 and got[0]["p_kind"] != got[0]["s_kind"] and got[0]["iran_id"] == ir and got[0]["foreign_id"] == fr


def test_a_young_standby_a_standby_of_the_same_type_and_a_protected_tunnel_are_not(monkeypatch, calm):
    monkeypatch.setattr(license, "tunnel_kind_allowed", lambda k: True)
    monkeypatch.setattr(standby, "_stale", lambda t, p: "")
    now = int(time.time())
    old = now - 10 * 3600
    ir, fr = make("duel-b", "backpack", "wssmux", state_since=old)
    make("duel-b-sb", "backpack", "tcpmux", standby_of="duel-b", enabled=False, state_since=now - 600, iran=ir, foreign=fr)          # (built ten minutes ago: nothing is known of its steadiness)
    ir, fr = make("duel-c", "backpack", "wssmux", state_since=old)
    make("duel-c-sb", "backpack", "wssmux", standby_of="duel-c", enabled=False, state_since=old, iran=ir, foreign=fr)                # (the same type: nothing to compare)
    ir, fr = make("duel-d", "backpack", "wssmux", state_since=old)
    make("duel-d-sb", "backpack", "tcpmux", standby_of="duel-d", enabled=False, state_since=old, iran=ir, foreign=fr)
    with session() as db:
        t = db.scalar(select(Tunnel).where(Tunnel.name == "duel-d"))
        t.record = {"protected": True}                                                              # (a protected tunnel keeps its type)
    ir, fr = make("duel-e", "backpack", "wssmux", state_since=old)
    make("duel-e-sb", "backpack", "tcpmux", standby_of="duel-e", enabled=False, state_since=old, iran=ir, foreign=fr, extra={"lane": {"on": True}})      # (it carries users in a lane now: not a free laboratory)
    names = {c["name"] for c in duel._candidates(now)}
    assert not names & {"duel-b", "duel-c", "duel-d", "duel-e"}


def test_the_switch_the_job_and_the_tooltip_are_wired():
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent
    assert 'jobs.register("duel", duel.EVERY, duel.run)' in (root / "nikatel" / "main.py").read_text(encoding="utf-8")
    assert "DUEL against its standby" in (root / "nikatel" / "web" / "app.js").read_text(encoding="utf-8")
    assert st.get("duel").get("on") is True
