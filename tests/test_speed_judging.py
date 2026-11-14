"""The Doctor's speed test, judged better (1.69.267; the owner, 2026-11-14: "what would you improve in the Doctor's speed test?" - "yes, build" the first three):
the middle of the last tests is kept next to the last one, a type that ONE test called poor is looked at again when it was much faster, and the scores of one pair compare like with like."""
from nikatel.services import bandwidth, knowledge
from nikatel.services.userscore import user_score

NOW = 1_800_000_000
H = 3600
DAY = 24 * H


# ------------------------------------------------------------------ 1. the middle of the last tests
def test_the_middle_of_the_last_tests_is_kept_with_its_range():
    hist = [[NOW - 5 * H, 38.0, 80.0], [NOW - 4 * H, 71.3, 88.0], [NOW - 3 * H, 87.0, 95.0], [NOW - 2 * H, 55.0, 110.0], [NOW - H, 121.2, 99.0]]
    assert bandwidth.recent_stats(hist, NOW) == {"n": 5, "med": 71.3, "lo": 38.0, "hi": 121.2}                  # (the row said 38: the middle of the last five is 71)
    assert bandwidth.recent_stats(hist[:2], NOW) == {"n": 2, "med": round((38.0 + 71.3) / 2, 1), "lo": 38.0, "hi": 71.3}               # (two tests: the middle of two)
    assert bandwidth.recent_stats(hist[:1], NOW) is None and bandwidth.recent_stats([], NOW) is None and bandwidth.recent_stats(None, NOW) is None
    old = [[NOW - 30 * H, 10.0, None]] + hist
    assert bandwidth.recent_stats(old, NOW)["lo"] == 38.0                                                       # (a test of more than a day ago is no part of the middle)
    more = [[NOW - 9 * H, 500.0, None]] + hist
    assert bandwidth.recent_stats(more, NOW) == bandwidth.recent_stats(hist, NOW)                                # (only the last five)


def test_a_sample_keeps_the_middle_in_the_bw_of_the_tunnel_and_the_judging_is_the_same():
    bw, change = bandwidth.add_sample(None, NOW - 3 * H, 100.0, 50.0, "door")
    assert bw["recent"] is None and change == ""                                                                # (one test: no middle yet)

    for i, v in enumerate((120.0, 40.0, 90.0)):
        bw, _ = bandwidth.add_sample(bw, NOW - (2 - i) * H, v, 50.0, "door")

    assert bw["recent"] == {"n": 4, "med": 95.0, "lo": 40.0, "hi": 120.0} and bw["down"] == 90.0               # (the last number stays the last number: the drop and slow rules are not changed)


def test_the_tooltip_of_the_capacity_tells_the_middle():
    app = (__import__("pathlib").Path(__file__).resolve().parent.parent / "nikatel" / "web" / "app.js").read_text(encoding="utf-8")
    assert "MIDDLE of its last ${rc.n} tests" in app and "one test alone says little" in app


# ------------------------------------------------------------------ 2. a poor type is looked at again
def entry(down, at_ago=3 * H, ok=True, loss=0.0, score=70, down1=None, up=None, lat=100.0, jitter=3.0, tests=()):
    full = {"score": score, "at": NOW - at_ago, "down_mbps": down, "down1_mbps": down1, "up_mbps": up, "latency_ms": lat, "loss_pct": loss, "jitter_ms": jitter}
    return {"last": {"ok": ok, "measured": True, "at": NOW - at_ago, "measure": "full", "score": score, "error": ""}, "full": full,
            "hist": [{"at": NOW - h * H, "ok": True, "m": "full", "down": down} for h in tests]}


def pair(**kinds):
    return {"at": NOW, "kinds": kinds}


def test_a_much_faster_poor_type_is_tested_again_after_two_days_but_not_before():
    p = pair(running=entry(100.0), fast=entry(194.0, at_ago=3 * DAY, ok=False, score=49))
    assert knowledge.verdict(p["kinds"]["fast"]) == "poor"
    assert knowledge.poor_retest(p, NOW, "running") == ["fast"]
    assert knowledge.verify_kinds(p, NOW, "running")[-1] == "fast"                                              # (it is one of the kinds of the next verify survey)
    p = pair(running=entry(100.0), fast=entry(194.0, at_ago=DAY, ok=False, score=49))
    assert knowledge.poor_retest(p, NOW, "running") == []                                                       # (tested a day ago: not again yet)


def test_the_poor_type_must_be_much_faster_clean_and_allowed_and_the_running_type_known():
    slow = pair(running=entry(100.0), slow=entry(110.0, at_ago=3 * DAY, ok=False, score=49))
    assert knowledge.poor_retest(slow, NOW, "running") == []                                                    # (not 1.2 times as fast: not worth a test)
    lossy = pair(running=entry(100.0), lossy=entry(300.0, at_ago=3 * DAY, ok=False, score=49, loss=3.0))
    assert knowledge.poor_retest(lossy, NOW, "running") == []
    p = pair(running=entry(100.0), fast=entry(194.0, at_ago=3 * DAY, ok=False, score=49))
    assert knowledge.poor_retest(p, NOW, "running", allowed=lambda k: k != "fast") == []                        # (the licence)
    assert knowledge.poor_retest(pair(fast=entry(194.0, at_ago=3 * DAY, ok=False, score=49)), NOW, "running") == []      # (no full test of the running type: nothing to compare with)
    failed = pair(running=entry(100.0), dead={"last": {"ok": False, "measured": False, "at": NOW - 3 * DAY, "measure": "full", "score": None, "error": "did not connect"}, "hist": []})
    assert knowledge.poor_retest(failed, NOW, "running") == []                                                  # (a type that never worked is not "poor")
    two = pair(running=entry(100.0), a=entry(200.0, at_ago=3 * DAY, ok=False, score=49), b=entry(300.0, at_ago=3 * DAY, ok=False, score=49))
    assert knowledge.poor_retest(two, NOW, "running") == ["b"]                                                  # (the fastest, and only one: a test moves up to 120 MB)


# ------------------------------------------------------------------ 3. like with like
def test_a_one_connection_reading_counts_only_when_every_full_test_has_one():
    door = entry(121.2, up=99.2, lat=112.8, jitter=10.8)                                                         # (the quality test of a running tunnel: no one-connection reading)
    pair_test = entry(193.9, down1=66.8, up=60.0, lat=120.3, jitter=4.5)                                         # (a pair test: eight connections and one)
    mixed = pair(a=door, b=pair_test)
    assert knowledge._mixed(mixed, NOW) is True
    rows = {r["kind"]: r for r in knowledge.ranked(mixed, NOW)}
    assert rows["b"]["user"] == user_score({**pair_test["full"], "down1_mbps": None}) > user_score(pair_test["full"])      # (counted without the one-connection reading: the same basis as the door test)
    both = pair(a=entry(121.2, down1=40.0, up=99.2), b=pair_test)
    assert knowledge._mixed(both, NOW) is False
    assert {r["kind"]: r for r in knowledge.ranked(both, NOW)}["b"]["user"] == user_score(pair_test["full"])      # (both have it: it counts)
    none = pair(a=door, b=entry(193.9, up=60.0, lat=120.3, jitter=4.5))
    assert knowledge._mixed(none, NOW) is False                                                                  # (neither has it: nothing to drop)
    latency_only = pair(a=door, b={"last": {"ok": True, "measured": True, "at": NOW, "measure": "latency", "score": 60, "error": ""}, "lat": {"score": 60, "at": NOW, "latency_ms": 100.0, "loss_pct": 0.0}, "hist": []})
    assert knowledge._mixed(latency_only, NOW) is False                                                          # (a latency test says nothing about speed: it does not make the pair "mixed")


def test_the_rows_carry_the_upload_and_the_upgrade_is_judged_on_the_same_basis_as_the_running_type():
    running = entry(121.2, up=99.2, lat=112.8, jitter=10.8, score=51)
    fast = entry(193.9, down1=66.8, up=60.0, lat=120.3, jitter=4.5, score=49)
    p = pair(**{"chisel:fwdtls": running, "bp:wssmux": fast})
    row = next(r for r in knowledge.ranked(p, NOW) if r["kind"] == "bp:wssmux")
    assert row["up_mbps"] == 60.0                                                                                # (the upload counts on both sides: it did not on the rows before)
    assert [r["kind"] for r in knowledge.upgrade_kinds(p, NOW, "chisel:fwdtls")] == ["bp:wssmux"]                # (1.6 times and 70 Mb/s more, and as good for the user on the common basis)
    assert knowledge._base_user(running["full"], knowledge._mixed(p, NOW)) == user_score(running["full"])         # (the running type has no one-connection reading: nothing was dropped from it)
    assert knowledge._base_user({"score": 44}, False) == 44                                                      # (no numbers of speed: the score it was given)
