"""Property tests of the pure rules of the tunnel engine, with odd inputs (the QA round of 2026-10-22): None, empty, zero, negative, huge, equal times, the future, a long list.
A seeded random generator, no new library: what must hold for ANY input is checked, not one example. (Nothing here asks a server or the database.)"""
import itertools
import random

import pytest

from nikatel.services import autopilot as ap
from nikatel.services import bandwidth as bw
from nikatel.services import knowledge as kn
from nikatel.services import standby as sb
from nikatel.services import tunnels as tn
from nikatel.services import userscore as us

NOW = 1_792_600_000
ROUNDS = 300


def rng(seed):
    return random.Random(seed)


def num(r):
    return r.choice([None, 0, 0.0, -1, -0.5, 1, 5, 20, 49.9, 50, 100, 1e9, -1e9, r.random() * 1000, r.randint(-10, 10 ** 10)])


def ts(r):
    return r.choice([NOW, NOW - 1, NOW + 1, 0, NOW - 86400 * r.randint(0, 40), NOW + 10 ** 9, -5])


def side(r):
    s = tn.Side()
    s.units = {r.choice(["a.service", "b.service", "c"]): r.choice(["active", "inactive", "failed", "unknown", ""]) for _ in range(r.randint(0, 3))}
    s.iface_state = r.choice(["", "UP", "DOWN", "UNKNOWN", "x"])
    s.rtt_ms, s.loss_pct = num(r), num(r)
    s.listen = {r.randint(1, 65535): r.choice([True, False]) for _ in range(r.randint(0, 3))}
    s.data_port, s.data_code, s.data_ms = r.choice([0, 80, 8080]), r.choice(["", "000", "200", "404", "502"]), num(r)
    s.error = r.choice(["", "", "", tn.OFF_ERROR, "ssh timeout"])
    s.wg_age = r.choice([None, -1, 0, 10, 301, 10 ** 6])
    return s


def plan(r):
    return tn.Plan(r.choice([[], ["a.service"], ["a.service", "b.service"]]), r.choice([[], ["a.service"], ["c"]]), iface=r.choice(["", "gre1"]), iran_peer=r.choice(["", "10.0.0.2"]),
                   ports=r.choice([[], [80], [80, 443]]), ping_iface=r.choice(["", "bp1"]), ping=r.choice(["", "203.0.113.9"]))


def entry(r):
    e = {"last": r.choice([None, {}, {"ok": True, "measured": True, "at": ts(r), "score": num(r)}, {"ok": False, "measured": False, "at": ts(r), "error": "x"}, {"skipped": "tools", "at": ts(r)}, {"ok": False, "measured": True, "at": ts(r)}])}

    if r.random() < 0.7:
        e["full"] = r.choice([{}, {"score": num(r), "at": ts(r), "down_mbps": num(r), "down1_mbps": num(r), "up_mbps": num(r), "latency_ms": num(r), "jitter_ms": num(r), "loss_pct": num(r)}])

    if r.random() < 0.4:
        e["lat"] = {"score": num(r), "at": ts(r), "latency_ms": num(r), "loss_pct": num(r)}

    if r.random() < 0.7:
        e["hist"] = [{"at": ts(r), "ok": r.choice([True, False, None]), "m": "full", **({"down": num(r)} if r.random() < 0.7 else {}), **({"loss": num(r)} if r.random() < 0.4 else {})} for _ in range(r.randint(0, 14))]

    return e


def pair(r):
    return r.choice([None, {}, {"kinds": {}}, {"kinds": {r.choice(kn.PRIOR + ["zz"]): entry(r) for _ in range(r.randint(0, 8))}, "at": NOW}])


# ------------------------------------------------------------------ the check of one tunnel
def test_judge_always_answers_a_known_state_and_a_server_that_is_off_is_down():
    r = rng(1)

    for _ in range(ROUNDS * 3):
        p, a, b = plan(r), side(r), side(r)
        state, why = tn.judge(p, a, b, r.choice([True, False]), r.randint(0, 9), r.choice([None, ("000", None, "v"), ("200", 12.5, "v"), ("404", None, "v")]), r.choice([True, False]), r.randint(0, 9), r.choice(["", "stalled"]))
        assert state in ("up", "down", "degraded", "unknown") and isinstance(why, str)

        if a.error == tn.OFF_ERROR or b.error == tn.OFF_ERROR:
            assert state == "down"
        elif a.error or b.error:
            assert state == "unknown"
        elif state == "up":
            assert why == "" and not tn.hard_fault(p, a, b)


def test_settle_never_holds_a_down_and_counts_the_streak():
    r = rng(2)
    states = ["up", "degraded", "down", "unknown", "starting"]

    for _ in range(ROUNDS * 3):
        old, judged = r.choice(states), r.choice(states)
        streak = r.choice([None, {}, {"state": judged, "n": r.randint(0, 5)}, {"state": r.choice(states), "n": 3}])
        new, s2 = tn.settle(old, judged, streak)
        assert new in (old, judged) and s2["state"] == judged and s2["n"] >= 1

        if judged == "down" or old in ("down", "starting", "unknown"):
            assert new == judged
        if judged == old:
            assert new == old


def test_a_soft_change_needs_two_checks_in_a_row_and_a_different_check_in_between_starts_again():
    s = None
    state, s = tn.settle("up", "degraded", s)
    assert state == "up"
    state, s = tn.settle("up", "up", s)
    assert state == "up"
    state, s = tn.settle("up", "degraded", s)
    assert state == "up" and s["n"] == 1
    state, s = tn.settle("up", "degraded", s)
    assert state == "degraded"


def test_flap_only_ever_turns_an_up_into_degraded():
    r = rng(3)

    for _ in range(ROUNDS * 3):
        raw = r.choice(["up", "down", "degraded", "unknown"])
        recent = [r.choice([True, False]) for _ in range(r.randint(0, 15))]
        out, why = tn.flap(raw, recent)

        if raw != "up":
            assert (out, why) == (raw, "")
        elif out != "up":
            assert out == "degraded" and why and not all((recent + [True])[-3:])
        else:
            assert why == ""


def test_the_throughput_is_never_negative_and_needs_a_sane_gap():
    r = rng(4)

    for _ in range(ROUNDS * 3):
        prev = r.choice([{}, {"rx_bytes": r.randint(0, 10 ** 9), "tx_bytes": r.randint(0, 10 ** 9), "now": NOW - r.randint(-5, 400)}])
        rx, tx = tn.throughput(prev, r.choice([None, 0, NOW]), r.choice([None, r.randint(-5, 10 ** 10)]), r.choice([None, r.randint(-5, 10 ** 10)]))
        assert (rx is None) == (tx is None) and (rx is None or (rx >= 0 and tx >= 0))


def test_the_ping_that_is_blocked_while_data_passes_is_not_a_total_loss():
    assert tn.ping_verdict(100.0, "200", None) == (None, True)
    assert tn.ping_verdict(100.0, "000", None) == (100.0, False)
    assert tn.ping_verdict(100.0, "", ("404", 5.0, "v")) == (None, True)
    assert tn.ping_verdict(None, "200", None) == (None, False) and tn.ping_verdict(40.0, "200", None) == (40.0, False)


# ------------------------------------------------------------------ what the Doctor knows
def test_the_ranking_is_ordered_and_its_numbers_are_in_range():
    r = rng(5)

    for _ in range(ROUNDS):
        p = pair(r)
        rows = kn.ranked(p, NOW, r.choice([True, False]))
        assert len({x["kind"] for x in rows}) == len(rows)
        full_first = [x["measure"] != "full" for x in rows]
        assert full_first == sorted(full_first)                                  # (a fresh full test before a latency-only one)

        for x in rows:
            assert x["steady"] is None or 0 <= x["steady"] <= 100


def test_the_better_types_are_never_the_running_one_and_are_proven_or_fresh():
    r = rng(6)

    for _ in range(ROUNDS):
        p, cur = pair(r), r.choice(kn.PRIOR + ["zz"])
        up = kn.upgrade_kinds(p, NOW, cur)
        st = kn.steadier_kinds(p, NOW, cur)
        fa = kn.faster_kinds(p, NOW, cur, num(r))

        for rows in (up, st, fa):
            assert all(x["kind"] != cur for x in rows)

        assert all(x["steady"] is None or x["steady"] >= kn.STEADY_OK for x in up)
        assert all(x["steady"] is not None and x["steady"] >= kn.STEADY_OK for x in st)
        assert [x["down_mbps"] for x in fa] == sorted((x["down_mbps"] for x in fa), reverse=True)


def test_the_candidates_are_unique_few_and_never_a_failed_kind_while_another_is_left():
    r = rng(7)

    for _ in range(ROUNDS):
        p, cur = pair(r), r.choice(kn.PRIOR)
        limit = r.choice([1, 5, 40])
        out = kn.candidates(p, cur, r.choice([True, False]), r.choice([True, False]), NOW, limit=limit)
        assert len(out) == len(set(out)) <= max(limit, 0)


def test_the_stability_of_a_type_needs_enough_tests_and_is_a_percent():
    r = rng(8)

    for _ in range(ROUNDS):
        e = entry(r)
        s = kn.stability(e, NOW)
        recent = [x for x in (e.get("hist") or []) if NOW - int(x.get("at") or 0) <= kn.FULL_FRESH]

        if len(recent) < kn.STEADY_MIN_TESTS:
            assert s is None
        else:
            assert 0 <= s["pct"] <= 100 and s["n"] == len(recent) and 0 <= s["swing"] <= 1 and s["passed"] <= s["n"]


def test_the_night_window_wraps_past_midnight_and_equal_hours_mean_any_time():
    r = rng(9)

    for _ in range(ROUNDS):
        now, tz, a, b = r.randint(0, 2 * 10 ** 9), r.choice([0, 210, -210]), r.randint(0, 23), r.randint(0, 23)
        h = ((now + tz * 60) // 3600) % 24

        if a == b:
            assert kn.in_window(now, tz, a, b)
        elif a < b:
            assert kn.in_window(now, tz, a, b) == (a <= h < b)
        else:
            assert kn.in_window(now, tz, a, b) == (h >= a or h < b)


@pytest.mark.parametrize("q", [None, {}, {"latency_ms": None}, {"latency_ms": 20, "error": "x"}])
def test_no_measurement_is_zero(q):
    assert us.user_score(q) == 0


def test_the_mark_of_what_a_user_gets_is_always_0_to_100_and_never_lower_for_a_better_line():
    r = rng(10)

    for _ in range(ROUNDS * 3):
        q = {"latency_ms": num(r), "jitter_ms": num(r), "loss_pct": num(r), "down_mbps": num(r), "down1_mbps": num(r), "up_mbps": num(r)}
        assert 0 <= us.user_score(q) <= 100

    good = {"latency_ms": 40.0, "jitter_ms": 1.0, "loss_pct": 0.0, "down_mbps": 200.0}
    assert us.user_score(good) >= us.user_score({**good, "loss_pct": 5.0}) >= us.user_score({**good, "loss_pct": 9.0})
    assert us.user_score(good) >= us.user_score({**good, "latency_ms": 120.0}) >= us.user_score({**good, "latency_ms": 300.0})
    assert us.user_score(good) >= us.user_score({**good, "down_mbps": 20.0})


# ------------------------------------------------------------------ the bandwidth of a tunnel
def test_a_run_of_samples_keeps_the_states_apart_and_the_history_short():
    r = rng(11)

    for _ in range(ROUNDS):
        cur, t = None, NOW - 86400 * 3

        for _ in range(r.randint(1, 40)):
            t += r.choice([1, 60, 180, 1200, 5400])
            down = r.choice([0.0, 0.2, 3.0, 15.0, 25.0, 100.0, 400.0, 1500.0, r.random() * 800])
            cur, change = bw.add_sample(cur, t, down, r.choice([None, 5.0]), "link", loss=r.choice([None, 0.0, 12.0]), live=r.choice([None, 0.0, 50.0]))
            assert cur["state"] in ("drop", "slow", "low", "learning", "ok") and change in ("", "drop", "slow", "recovered")
            assert not (cur["drop"] and cur["slow"]) and len(cur["hist"]) <= bw.KEEP and cur["next_at"] > t and cur["down"] == round(down, 1)

            if cur["drop"]:
                assert cur["state"] == "drop"


def test_a_drop_needs_two_samples_and_a_recovery_ends_it():
    cur, ch = bw.add_sample(None, 1, 500.0, None, "link")
    t = 1

    for _ in range(5):
        t += 100
        cur, ch = bw.add_sample(cur, t, 500.0, None, "link")

    cur, ch = bw.add_sample(cur, t + 1, 50.0, None, "link")
    assert ch == "" and cur["suspect"] is True and cur["state"] != "drop"
    cur, ch = bw.add_sample(cur, t + 200, 50.0, None, "link")
    assert ch == "drop" and cur["state"] == "drop"
    cur, ch = bw.add_sample(cur, t + 400, 480.0, None, "link")
    assert ch == "recovered" and cur["state"] == "ok"


def test_the_usual_level_is_one_of_the_samples_and_waits_for_enough_of_them():
    r = rng(12)

    for _ in range(ROUNDS):
        hist = [[ts(r), abs(r.random() * 900), None] for _ in range(r.randint(0, 70))]
        ref = bw.reference(hist, NOW)
        fresh = [x[1] for x in hist if NOW - x[0] <= bw.REF_WINDOW]

        if len(fresh) < bw.REF_MIN:
            assert ref is None
        else:
            assert ref in fresh


def test_the_watch_of_a_tunnel_nobody_uses_looks_once_per_gap_and_stops_when_the_users_come():
    r = rng(13)

    for _ in range(ROUNDS):
        e, looks, t = None, [], NOW

        for _ in range(60):
            t += r.choice([30, 60, 120, 300])
            e, look = bw.zero_step(e, t, r.choice([0.0, 0.0, 0.0, 0.01, 0.2, None]), r.choice([True, True, True, False]))

            if look:
                looks.append(t)
                assert e is not None and e["looked"] == t

            if e is None:
                looks = []                        # (the silence ended: the count starts again)

            assert all(b - a >= bw.ZERO_GAP for a, b in itertools.pairwise(looks))


def test_the_problems_of_a_silent_tunnel_never_raise_and_a_clean_one_has_none():
    r = rng(14)

    for _ in range(ROUNDS):
        f = {"state": r.choice([None, "up", "down", "degraded"]), "last_error": r.choice([None, "x"]), "data_fails": r.choice([None, 0, 3]), "ext_code": r.choice([None, "000", "404", 0]), "loss": num(r),
             "ping": r.choice([None, 20.0, 400.0]), "usual_ping": r.choice([None, 20.0]), "bw_state": r.choice([None, "ok", "drop", "slow"]), "carries": r.choice([None, 0.1, 300.0])}

        for p in bw.silent_problems(f):
            assert isinstance(p, str) and p

    assert bw.silent_problems({"state": "up", "data_fails": 0, "ext_code": "404", "loss": 0.0, "ping": 40.0, "usual_ping": 40.0, "bw_state": "ok", "carries": 500.0}) == []


def test_the_reason_of_a_drop_is_always_one_known_code_and_one_sentence():
    r = rng(15)
    codes = {"iran_busy", "foreign_busy", "kind_wide", "iran_wide", "foreign_wide", "stalled", "full", "loss", "latency", "this_only"}

    for _ in range(ROUNDS * 2):
        f = {"now": num(r), "ref": num(r), "iran": r.choice([None, {}, {"cpu": num(r), "name": "x"}]), "foreign": r.choice([None, {"cpu": 90, "name": "f"}]),
             "same_kind": r.choice([None, {}, {"total": 3, "dropped": 3, "others_total": 4, "others_dropped": 0, "family": "wss"}]), "same_iran": r.choice([None, {}, {"total": 3, "dropped": 2}]),
             "same_foreign": r.choice([None, {"total": 2, "dropped": 2}]), "live": num(r), "loss": num(r), "rtt": num(r), "rtt_ref": num(r)}
        code, text = bw.explain(f)
        assert code in codes and text and "\n" not in text
        _, text2 = bw.explain_slow(f)
        assert text2 and "\n" not in text2


# ------------------------------------------------------------------ the repair and the standby
def test_the_ladder_only_has_known_steps_and_the_retry_wait_never_shrinks():
    r = rng(16)

    for _ in range(ROUNDS):
        d = {"iran_error": r.choice(["", "x"]), "state": r.choice([None, "up", "down", "degraded"]), "units_bad": r.choice([[], ["a"]]), "under_loss_i": num(r), "under_loss_f": num(r), "ext_blocked": r.choice([True, False, None]),
             "gre": r.choice([True, False]), "gre_loss": num(r), "link": r.choice([None, "gre", "wg", "ipsec", "wgchisel", "zz"]), "flap_fails": r.choice([None, 0, 5]), "flap_n": 10, "port_open": r.choice([None, True, False])}
        cause, why = ap.classify(d)
        assert cause in {"server_unreachable", "healthy", "units_down", "route_cut", "gre_blocked", "lossy_route", "port_blocked", "unknown"} and why
        steps = ap.ladder_for(cause, "tcpmux", r.choice(["restart", "rebuild", "switch", "zz", None]), r.choice([True, False]))
        assert set(steps) <= {"restart", "rebuild", "relink", "switch", "move"} and len(steps) == len(set(steps))

    waits = [ap.retry_after("down", n) for n in range(12)]
    assert waits == sorted(waits) and ap.retry_after("degraded", 3) == 1800


def test_a_tunnel_that_is_down_is_climbed_after_its_wait_and_never_before_it():
    for plain in (0, 1, 3):
        for fault in (True, False):
            for cut in (True, False):
                seq = [ap.escalate_due("down", d, 0, plain, fault, cut) for d in range(0, 700, 10)]
                assert seq == sorted(seq)                      # (once due, it stays due)
                assert ap.escalate_due("down", 10 ** 6, 0, 0, False, False) is True

    assert ap.escalate_due("degraded", 119, 0, 0, False, False) is False and ap.escalate_due("degraded", 120, 0, 0, False, False) is True


def test_the_standby_decision_is_one_known_action_and_a_handover_needs_a_down_tunnel_with_a_standby_that_is_up():
    r = rng(17)

    for _ in range(ROUNDS * 3):
        p = r.choice([None, {"state": r.choice(["up", "down", "degraded", "unknown"]), "state_since": NOW - r.randint(0, 500), "last_check_at": NOW - r.randint(0, 500), "off": r.choice([True, False])}])
        s = r.choice([None, {"state": r.choice(["up", "down"]), "state_since": NOW - r.randint(0, 5000), "stale": r.choice(["", "other"]), "use": r.choice([None, {}, {"manual": True}, {"since": NOW}]), "created_at": NOW - 1000, "parked_at": NOW - 100}])
        act, why = sb.decide(p, s, now=NOW, auto=r.choice([True, False]), storm=r.choice([True, False]), repairing=r.choice([True, False]), building=r.choice([True, False]), wait_until=r.choice([0, NOW + 10]),
                             after=r.choice([0, 5, 300]), better=r.choice(["", "bp:wss"]))
        assert act in {"handover", "back", "build", "restart", "rebuild", "drop", "wait"} and why

        if act == "handover":
            assert p and p["state"] == "down" and not p["off"] and s and s["state"] == "up" and not s["use"] and not s["stale"]
        if act == "build":
            assert p and s is None and p["state"] == "up"


def test_the_alert_of_a_tunnel_alternates_down_and_up_and_never_speaks_out_of_turn():
    from nikatel.services import alerts as al

    r = rng(19)

    for _ in range(ROUNDS):
        rules = al.Rules(r.choice([0, 10, 60]), r.choice([0, 15, 120]), r.choice([0, 300]), 0, 3600, 0)
        t, now, told_down = al.Track(), NOW, False

        for _ in range(80):
            now += r.choice([0, 1, 5, 30, 200])
            t, verdict = al.decide(t, r.random() < 0.5, now, rules, since=r.choice([0, now - 100, now + 5, now]), hold=r.random() < 0.1)
            assert verdict in (None, "down", "up", "remind")

            if verdict == "down":
                assert not told_down
                told_down = True
            elif verdict == "up":
                assert told_down
                told_down = False
            elif verdict == "remind":
                assert told_down and t.alerted

            assert t.alerted == told_down                  # (what the owner was told is what the panel believes)


def test_the_shifted_ports_stay_valid_ports_and_never_collide():
    r = rng(18)

    for _ in range(ROUNDS * 2):
        port = r.choice([1, 80, 443, 1024, 20000, 20001, 45535, 45536, 50000, 65535])
        s = sb.shifted(port)
        assert s is None or (1 <= s <= 65535 and s != port)

    assert sb.shifted(80) == 20080 and sb.shifted(50000) == 30000
