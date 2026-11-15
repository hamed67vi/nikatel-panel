"""One speed test every ten days (1.69.280; the owner, 2026-11-15, after the volume of the Doctor's tests was told: «تست سرعت هر 10 روز یک بار انجام بشه / از تستها کم نکن / فقط عاملی که باعث میشه همچین حجمی بره رو
بهینه میکنیم»). The speed tests were the volume: a kind of a live pair got three of them a week (`VERIFY_TESTS`, one a day) and the bandwidth job measured every tunnel every four hours. NO test is dropped - every kind is still
tested, the pings and the latency tests of the night survey run as before; only the SPEED part of the routine is asked once in `testbudget.speed_every()` (the setting `speed_days`, 10 by default) per kind and per tunnel. The tests
that something asks for stay: a tunnel never measured, a drop, a poor score, the repair's candidates, the owner's own."""
from sqlalchemy import select

from nikatel.db import session
from nikatel.models import Server, Tunnel
from nikatel.services import bandwidth, knowledge, testbudget
from nikatel.services import settings as st

NOW = 1_800_000_000
D = 86400
_N = iter(range(10, 250))


def entry(days_ago, score=70, down=500.0, ok=True, measure="full"):
    """One kind of a pair after ONE test `days_ago` days ago (the real merge: its history, its last test, its full test)."""
    return knowledge.merge(None, {"kind": "x", "ok": ok, "score": score, "down_mbps": down, "latency_ms": 90.0, "loss_pct": 0.0, "jitter_ms": 2.0}, measure, NOW - int(days_ago * D))


def pair(**kinds):
    return {"at": NOW, "kinds": kinds}


# ------------------------------------------------------------------ the setting
def test_the_interval_of_a_speed_test_is_ten_days_and_the_owner_can_change_it():
    assert testbudget.speed_every() == 10 * D and st.get("testbudget")["speed_days"] == 10.0
    st.put("testbudget", {"speed_days": 3})
    assert testbudget.speed_every() == 3 * D
    st.put("testbudget", {"speed_days": 0.2})
    assert testbudget.speed_every() == D                                                                      # (never more often than once a day)
    st.put("testbudget", {"speed_days": 500})
    assert testbudget.speed_every() == 60 * D                                                                 # (never rarer than two months: the numbers would be too old to judge by)
    st.put("testbudget", {"speed_days": "x"})
    assert testbudget.speed_every() == 10 * D                                                                 # (garbage: the default)


# ------------------------------------------------------------------ the survey of the live pairs (the biggest volume)
def test_a_kind_is_asked_for_a_speed_test_when_its_last_one_is_ten_days_old_or_it_never_had_one():
    p = pair(running=entry(11), a=entry(2.9, score=90), b=entry(9.9, score=80), c=entry(11, score=70))
    assert knowledge.verify_kinds(p, NOW, "running") == ["running", "c"]                                      # (2.9 and 9.9 days: not yet; 11 days: due)
    assert knowledge.verify_kinds(pair(running=entry(0.1)), NOW, "running") == []                             # (a test an hour or two ago: nothing)
    assert knowledge.verify_kinds(None, NOW, "running") == ["running"]                                        # (nothing known: the running type is the first thing to know)
    st.put("testbudget", {"speed_days": 3})
    assert knowledge.verify_kinds(p, NOW, "running") == ["running", "b", "c"]                                 # (the owner asks every three days: 9.9 days is old now, 2.9 days is still not)


def test_a_latency_test_is_no_speed_test_and_does_not_postpone_one():
    p = pair(running=entry(1, measure="latency"))
    assert knowledge.verify_kinds(p, NOW, "running") == ["running"]                                           # (the night survey's pings of yesterday: the speed was never asked)
    assert knowledge.last_full(entry(1, measure="latency")) == 0 and knowledge.last_full(entry(4)) == NOW - 4 * D


def test_a_faster_type_that_one_test_called_poor_waits_for_its_ten_days_like_any_other_speed_test():
    running = entry(1, score=70, down=300.0)
    poor = entry(3, score=30, down=900.0, ok=False)                                                           # (it carried data, scored under the line, and was three times as fast as the running type)
    assert knowledge.poor_retest(pair(running=running, poor=poor), NOW, "running") == ["poor"]                # (the old rule: two days are enough ...)
    assert knowledge.verify_kinds(pair(running=running, poor=poor), NOW, "running") == []                     # (... the speed rule says: it had its speed test three days ago)
    poor_old = entry(11, score=30, down=900.0, ok=False)
    assert knowledge.verify_kinds(pair(running=running, poor=poor_old), NOW, "running") == ["poor"]


def test_stability_is_still_known_after_three_tests_ten_days_apart():
    e = entry(1)
    e = knowledge.merge(e, {"kind": "x", "ok": True, "score": 70, "down_mbps": 480.0, "latency_ms": 90.0, "loss_pct": 0.0, "jitter_ms": 2.0}, "full", NOW - 11 * D)
    e = knowledge.merge(e, {"kind": "x", "ok": True, "score": 70, "down_mbps": 510.0, "latency_ms": 90.0, "loss_pct": 0.0, "jitter_ms": 2.0}, "full", NOW - 21 * D)
    s = knowledge.stability(e, NOW)
    assert s is not None and s["n"] == 3 and s["pct"] >= 75                                                   # (a week holds one test now: the month counts, as the rule says for fewer than three recent ones)


# ------------------------------------------------------------------ the bandwidth job (a measurement of every live tunnel)
def tunnel(name, bw=None, record=None):
    n = next(_N)

    with session() as db:
        ir, fr = Server(name=f"sp-ir-{name}", host=f"203.0.113.{n}", role="iran", online=True), Server(name=f"sp-fr-{name}", host=f"198.51.100.{n}", role="foreign", online=True)
        db.add_all([ir, fr])
        db.flush()
        t = Tunnel(name=name, type="backhaul", transport="tcpmux", port=9901, iran_server_id=ir.id, foreign_server_id=fr.id, state="up", forwarded_ports=[8076], foreign_tunnel_ip=f"10.2.{n}.2",
                   record={**({"bw": bw} if bw else {}), **(record or {})}, secrets={})
        db.add(t)
        db.flush()
        return t.id


def measured(days_ago, **more):
    return {"m": bandwidth.METHOD, "how": "link", "down": 300.0, "at": NOW - int(days_ago * D), "ref": 300.0, "state": "ok", "drop": None, "suspect": False, "hist": [], **more}


def test_a_tunnel_is_measured_again_after_ten_days_not_after_four_hours():
    young = tunnel("sp-young", bw=measured(5))
    old = tunnel("sp-old", bw=measured(11))
    never = tunnel("sp-never")
    assert bandwidth.every() == 10 * D and bandwidth.INTERVAL == 4 * 3600
    assert sorted(bandwidth._due(NOW)) == sorted([old, never]) and young not in bandwidth._due(NOW)           # (never measured: at once; 11 days: due; 5 days: it waits)


def test_the_measurements_that_something_asks_for_do_not_wait_for_the_ten_days():
    poor = tunnel("sp-poor", bw=measured(5), record={"quality": {"score": 25}})
    dropped = tunnel("sp-drop", bw=measured(5, state="drop", drop={"since": NOW - 3000, "from": 300.0}, next_at=NOW - 60))
    calm = tunnel("sp-calm", bw=measured(5))
    due = bandwidth._due(NOW)
    assert poor in due and dropped in due and calm not in due                                                 # (a poor score and a drop are looked at as before; a calm tunnel is not)


def test_the_next_measurement_is_planned_ten_days_ahead_and_a_new_setting_moves_it():
    bw, _ = bandwidth.add_sample(measured(0, hist=[[NOW - 9000, 300.0, None]] * 5), NOW, 295.0, None, "link", 80.0, None)
    assert bw["next_at"] == NOW + 10 * D
    st.put("testbudget", {"speed_days": 2})
    assert bandwidth.every() == 2 * D
    bw, _ = bandwidth.add_sample(measured(0, hist=[[NOW - 9000, 300.0, None]] * 5), NOW, 295.0, None, "link", 80.0, None)
    assert bw["next_at"] == NOW + 2 * D


def test_the_first_measurement_of_a_new_tunnel_never_waits():
    tid = tunnel("sp-new")

    with session() as db:
        assert db.scalar(select(Tunnel.id).where(Tunnel.id == tid)) == tid

    assert tid in bandwidth._due(NOW) and bandwidth._no_sample(tid)
