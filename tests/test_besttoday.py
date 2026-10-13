"""The best tunnels of the day: the score, the ranking from the checks, the periodic measurement."""
import time

from nikatel.db import session
from nikatel.models import Server, Tunnel, TunnelCheck
from nikatel.services import besttoday as bt


def test_the_score_rewards_staying_up_then_speed_and_steadiness_and_punishes_breaks():
    best = bt.score_of(100.0, 40.0, 2.0, 0.0, 0)
    assert best >= 95
    assert bt.score_of(100.0, 40.0, 2.0, 0.0, 0) > bt.score_of(97.0, 40.0, 2.0, 0.0, 0) > bt.score_of(90.0, 40.0, 2.0, 0.0, 0)
    assert bt.score_of(99.0, 40.0, 2.0, 0.0, 0) > bt.score_of(99.0, 250.0, 2.0, 0.0, 0)          # a slower line is worse
    assert bt.score_of(99.0, 60.0, 2.0, 0.0, 0) > bt.score_of(99.0, 60.0, 2.0, 8.0, 0)            # packet loss costs
    assert bt.score_of(99.0, 60.0, 2.0, 0.0, 0) > bt.score_of(99.0, 60.0, 2.0, 0.0, 5)            # every break costs
    assert bt.score_of(80.0, 300.0, 80.0, 10.0, 9) == 0 and bt.score_of(100.0, None, None, None, 0) > 50


def test_summarise_counts_the_breaks_the_median_and_the_jitter():
    checks = [(i, ok, rtt) for i, (ok, rtt) in enumerate([(True, 50.0), (True, 52.0), (False, None), (True, 51.0), (True, 80.0), (False, None), (False, None), (True, 50.0)])]
    s = bt.summarise(checks)
    assert s["samples"] == 8 and s["avail"] == 62.5 and s["outages"] == 2 and s["rtt_ms"] == 51.0 and s["jitter_ms"] > 0
    assert bt.summarise([])["avail"] == 0.0 and bt.outages_in([True, True, True]) == 0


def _tunnel(db, name, iran, foreign, suffix):
    ir = db.query(Server).filter_by(name=iran).first() or Server(name=iran, host=f"203.0.113.{suffix}", role="iran")
    fr = db.query(Server).filter_by(name=foreign).first() or Server(name=foreign, host=f"198.51.100.{suffix}", role="foreign")
    db.add_all([ir, fr])
    db.flush()
    t = Tunnel(name=name, type="backhaul", iran_server_id=ir.id, foreign_server_id=fr.id, port=1, state="up")
    db.add(t)
    db.flush()
    return t


def test_the_ranking_puts_the_steady_one_first_marks_the_best_to_a_server_and_waits_for_enough_checks(client):
    now = int(time.time())

    with session() as db:
        good = _tunnel(db, "t-good", "bt-ir1", "bt-fr1", 61)
        bad = _tunnel(db, "t-bad", "bt-ir2", "bt-fr1", 62)
        few = _tunnel(db, "t-few", "bt-ir3", "bt-fr3", 63)

        for i in range(60):
            db.add(TunnelCheck(tunnel_id=good.id, ts=now - 3600 + i * 30, ok=True, rtt_ms=45.0 + (i % 3)))
            db.add(TunnelCheck(tunnel_id=bad.id, ts=now - 3600 + i * 30, ok=i % 4 != 0, rtt_ms=180.0 + (i % 40) if i % 4 else None))

        for i in range(3):
            db.add(TunnelCheck(tunnel_id=few.id, ts=now - 300 + i, ok=True, rtt_ms=40.0))

    r = client.get("/api/besttoday?hours=24").json()
    rows = {x["name"]: x for x in r["rows"]}
    assert r["rows"][0]["name"] == "t-good" and rows["t-good"]["place"] == 1 and rows["t-bad"]["place"] == 2
    assert rows["t-good"]["score"] > rows["t-bad"]["score"] and rows["t-good"]["avail"] == 100.0 and rows["t-bad"]["outages"] > 5
    assert rows["t-good"]["best_for"] is True and rows["t-bad"]["best_for"] is False          # two tunnels lead to bt-fr1: the better one is marked
    assert rows["t-few"]["score"] is None and rows["t-few"]["place"] is None and r["rows"][-1]["name"] == "t-few"          # too few checks: no score, last
    assert client.get("/api/besttoday?hours=0").status_code == 422


def test_the_job_measures_every_tunnel_and_survives_a_failure(monkeypatch):
    from nikatel.services import tunnel_test

    monkeypatch.setattr(tunnel_test, "quality_all", lambda: [{"id": 1, "score": 80}, {"id": 2, "error": "x"}])
    bt.refresh()

    def boom():
        raise ValueError("no")

    monkeypatch.setattr(tunnel_test, "quality_all", boom)
    bt.refresh()