"""The first and most important measure of a tunnel is what the USER gets: speed and ping (the owner, 2026-10-21), and the best tunnel of the day is chosen by it.

What is tested: the mark (the one-connection speed counts more than eight, 300 Mb/s is full marks and then the ping decides, the ping is a straight line, loss and jitter come after), the tests of every kind of tunnel use
it, the choice of the best kind of a pair counts it again from the numbers (not from a score written down by an older rule), a type that is faster but answers much slower is no upgrade, and the words say what a user gets."""
from nikatel.services import build_other, knowledge, tunnel_test, userscore
from nikatel.services.userscore import user_score

NOW = 1_800_000_000


def test_the_mark_counts_the_speed_of_one_connection_and_the_ping_first():
    base = {"latency_ms": 40, "jitter_ms": 0, "loss_pct": 0}
    fast = user_score({**base, "down_mbps": 400, "down1_mbps": 300})
    slow_one = user_score({**base, "down_mbps": 400, "down1_mbps": 20})
    assert fast > slow_one + 10                                                                         # (eight fast connections do not hide a slow one: a phone downloads over one)
    same_speed = {"down_mbps": 500, "down1_mbps": 500}
    near, far = user_score({**same_speed, "latency_ms": 40, "jitter_ms": 0, "loss_pct": 0}), user_score({**same_speed, "latency_ms": 160, "jitter_ms": 0, "loss_pct": 0})
    assert near - far >= 20 and user_score({**same_speed, "latency_ms": 30, "jitter_ms": 0, "loss_pct": 0}) == 100                # (above 300 Mb/s the ping decides; 30 ms is full marks)
    assert userscore.ping_points(30) == 100 and userscore.ping_points(180) == 0 and userscore.ping_points(105) == 50 and userscore.ping_points(None) == 0
    assert user_score({**same_speed, "latency_ms": 40, "jitter_ms": 0, "loss_pct": 10}) < user_score({**same_speed, "latency_ms": 40, "jitter_ms": 0, "loss_pct": 0}) - 10         # (loss and jitter after)
    assert user_score(None) == 0 and user_score({"error": "x"}) == 0 and user_score({"latency_ms": None}) == 0


def test_the_tests_of_every_kind_use_the_same_mark():
    q = {"latency_ms": 60, "jitter_ms": 2, "loss_pct": 0, "down_mbps": 400, "up_mbps": 200}
    assert build_other.quality_score(q) == user_score(q) and tunnel_test.score(60, 2, 0, 400) == user_score({"latency_ms": 60, "jitter_ms": 2, "loss_pct": 0, "down_mbps": 400})
    assert build_other.quality_score({"latency_ms": 20, "jitter_ms": 0, "loss_pct": 0, "down_mbps": 900, "up_mbps": 900}) == 100


def _pair(kinds):
    """{kind: (stored score, down, down1, ping, loss)} as FULL measurements made a day ago."""
    return {"kinds": {k: {"full": {"score": v[0], "down_mbps": v[1], "down1_mbps": v[2], "latency_ms": v[3], "jitter_ms": 1.0, "loss_pct": v[4], "at": NOW - 86400}, "last": {"ok": True, "at": NOW - 86400}} for k, v in kinds.items()}}


def test_the_best_kind_is_the_one_the_user_gets_most_from_not_the_one_with_the_highest_old_score():
    p = _pair({"bp:tcpmux": (99, 1400, 600, 40, 0), "bp:wssmux": (99, 1400, 600, 125, 0), "tcpmux": (62, 160, 150, 42, 0), "chisel:tls": (99, 40, 5, 40, 0)})
    rows = knowledge.ranked(p, NOW)
    assert [r["kind"] for r in rows] == ["bp:tcpmux", "bp:wssmux", "tcpmux", "chisel:tls"]            # (the same speed: the ping decides; chisel gives ONE connection 5 Mb/s: last, though its old score says 99)
    assert rows[0]["user"] > rows[1]["user"] > rows[3]["user"] and rows[0]["user"] == user_score({"latency_ms": 40, "jitter_ms": 1.0, "loss_pct": 0, "down_mbps": 1400, "down1_mbps": 600})


def test_a_kind_known_by_pings_only_keeps_its_stored_score():
    p = {"kinds": {"tcpmux": {"lat": {"score": 70, "latency_ms": 50.0, "at": NOW - 3600}, "last": {"ok": True, "at": NOW - 3600, "measure": "latency"}}}}
    rows = knowledge.ranked(p, NOW)
    assert len(rows) == 1 and rows[0]["user"] == 70 and rows[0]["measure"] == "latency"


def test_a_type_that_is_much_faster_but_answers_much_slower_is_no_upgrade_and_the_words_say_what_a_user_gets():
    p = _pair({"tcpmux": (62, 160, 150, 40, 0), "bp:tcp": (99, 1794, 700, 45, 0), "chisel:tls": (90, 900, 400, 160, 0)})
    rows = knowledge.upgrade_kinds(p, NOW, "tcpmux")
    assert [r["kind"] for r in rows] == ["bp:tcp"]                                                       # (chisel is 6 times faster but answers four times slower: not an upgrade)
    assert [r["kind"] for r in knowledge.faster_kinds(p, NOW, "tcpmux", 10.0)] == ["bp:tcp"]
    assert "Mb/s for a user" in knowledge._why_good(knowledge.ranked(p, NOW)[0], NOW) and "45 ms" in knowledge._why_good(knowledge.ranked(p, NOW)[0], NOW)
