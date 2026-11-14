"""The chronic look (services/chronic.py, 1.69.269): a tunnel that keeps being cut is moved to a steadier Iran server.
The owner, 2026-11-14: "yes, build" - after a tunnel was cut twice in 50 minutes on a server the panel's host cannot reach."""
import time

import pytest

from nikatel.config import get_settings
from nikatel.db import session
from nikatel.models import Event, Server, Tunnel
from nikatel.services import actions, autopilot, chronic, doctor, failover, knowledge
from nikatel.services import settings as st

NOW = 1_800_000_000


def row(name="x-tun", tid=1, week=8, day=3, state="up", iran_id=1, protected=False, iran_name="ir-a", foreign="fr-a", rtt=100.0):
    return {"id": tid, "name": name, "state": state, "iran_id": iran_id, "iran_name": iran_name, "foreign_name": foreign, "rtt": rtt, "protected": protected, "week": week, "day": day}


def cand(cid=2, name="ir-b", score=90):
    return {"id": cid, "name": name, "online": True, "score": score, "rtt_ms": 95.0, "loss_pct": 0.0, "gre_bad": False, "prev_cut": False}


# ------------------------------------------------------------------ pure
def test_a_tunnel_is_chronic_after_eight_cuts_in_a_week_with_three_of_them_in_the_last_day():
    assert chronic.is_chronic(8, 3) and chronic.is_chronic(30, 5)
    assert not chronic.is_chronic(7, 4)                                                         # (not enough in the week)
    assert not chronic.is_chronic(23, 1) and not chronic.is_chronic(9, 2)                        # (cut a lot last week, quiet since: mended, not chronic)
    assert not chronic.is_chronic(0, 0)


def test_the_server_for_a_chronic_tunnel_is_one_the_ranking_accepts_and_that_has_no_chronic_tunnel(monkeypatch):
    monkeypatch.setattr(failover, "room_for", lambda c, n: False)
    ranking = [cand(2, "ir-b"), cand(3, "ir-c")]
    assert chronic.pick(ranking, 100.0, set())["id"] == 2
    assert chronic.pick(ranking, 100.0, {2})["id"] == 3                                         # (ir-b carries a chronic tunnel itself: not there)
    assert chronic.pick(ranking, 100.0, {2, 3}) is None
    assert chronic.pick([cand(2, "ir-b", score=40)], 100.0, set()) is None                      # (the quality rule of the move for quality still holds)


def test_the_sentence_says_how_many_cuts_where_and_what_was_done():
    r = {**row(), "server_tunnels": 3, "server_week": 17}
    s = chronic.sentence(r, "It stays.")
    assert "cut 8 times in 7 days, 3 of them in the last 24 hours" in s and "ir-a" in s and "17 cuts across its 3 tunnels" in s and s.endswith("It stays.")
    assert "across its" not in chronic.sentence({**row(), "server_tunnels": 1, "server_week": 8}, "x")


# ------------------------------------------------------------------ the job
@pytest.fixture
def calm(monkeypatch):
    monkeypatch.setattr(chronic.jobs, "stopping", lambda: False)
    monkeypatch.setattr(get_settings(), "read_only", False)
    monkeypatch.setattr(autopilot, "quiet_now", lambda: False)
    monkeypatch.setattr(actions, "doctor_mode", lambda: "move")
    st.put("chronic", {"told": {}, "moved": {}, "last_move": 0})
    yield


def events(text):
    with session() as db:
        return [e.text for e in db.query(Event).all() if text in e.text]


def test_a_chronic_tunnel_is_moved_once_and_told_once_a_day(monkeypatch, calm):
    moves = []
    monkeypatch.setattr(chronic, "_gather", lambda now: [row("a1-tun", 1, 8, 3), row("b1-tun", 2, 1, 0, iran_id=5, iran_name="ir-e")])
    monkeypatch.setattr(chronic, "_try_move", lambda r, bad, doc, now: moves.append(r["name"]) or "")
    chronic.run(NOW)
    assert moves == ["a1-tun"]                                                                  # (the quiet tunnel is left alone)
    assert st.get("chronic")["last_move"] == NOW and st.get("chronic")["moved"] == {"a1-tun": NOW}
    assert len(events("Tunnel a1-tun")) == 1 and "moves it to a steadier Iran server" in events("Tunnel a1-tun")[0]
    chronic.run(NOW + 1800)
    assert len(events("Tunnel a1-tun")) == 1                                                    # (one event a day)


def test_only_one_chronic_tunnel_is_moved_in_a_turn_and_the_others_are_told_why_not(monkeypatch, calm):
    asked = []
    monkeypatch.setattr(chronic, "_gather", lambda now: [row("a2-tun", 1, 8, 4), row("c2-tun", 3, 9, 3, iran_id=7, iran_name="ir-g")])
    monkeypatch.setattr(chronic, "_try_move", lambda r, bad, doc, now: asked.append(r["name"]) or "")
    chronic.run(NOW)
    assert asked == ["a2-tun"]                                                                  # (the one cut more in the last day first; the second is not asked in the same turn)
    assert "It was not moved this time." in events("Tunnel c2-tun")[0]


def test_a_refusal_is_told_and_nothing_is_recorded_as_moved(monkeypatch, calm):
    monkeypatch.setattr(chronic, "_gather", lambda now: [row("a3-tun", 1, 8, 3)])
    monkeypatch.setattr(chronic, "_try_move", lambda r, bad, doc, now: "No other Iran server is steadier: it stays.")
    chronic.run(NOW)
    assert "it stays" in events("Tunnel a3-tun")[0]
    assert st.get("chronic")["moved"] == {} and st.get("chronic")["last_move"] == 0


def test_two_chronic_tunnels_on_one_server_make_the_server_say_so(monkeypatch, calm):
    monkeypatch.setattr(chronic, "_gather", lambda now: [row("a4-tun", 1, 8, 3, iran_id=1, iran_name="ir-s4"), row("d4-tun", 4, 8, 3, iran_id=1, iran_name="ir-s4"), row("e4-tun", 5, 0, 0, iran_id=1, iran_name="ir-s4")])
    monkeypatch.setattr(chronic, "_try_move", lambda r, bad, doc, now: "nope")
    chronic.run(NOW)
    ev = events("tunnels are chronic")
    assert len(ev) == 1 and "2 of its 3 tunnels are chronic" in ev[0] and "worth retiring" in ev[0]
    chronic.run(NOW + 3600)
    assert len(events("tunnels are chronic")) == 1                                               # (once in two days)


def test_the_job_does_nothing_when_the_doctor_is_off_a_failover_is_on_or_nothing_is_chronic(monkeypatch, calm):
    asked = []
    monkeypatch.setattr(chronic, "_gather", lambda now: asked.append(1) or [])
    monkeypatch.setattr(actions, "doctor_mode", lambda: "off")
    chronic.run(NOW)
    monkeypatch.setattr(actions, "doctor_mode", lambda: "move")
    monkeypatch.setattr(autopilot, "quiet_now", lambda: True)
    chronic.run(NOW)
    assert asked == []
    monkeypatch.setattr(autopilot, "quiet_now", lambda: False)
    chronic.run(NOW)
    assert asked == [1]


# ------------------------------------------------------------------ the rails of the move
def test_the_move_obeys_its_rails_and_goes_through_the_doctors_own_move(monkeypatch, calm):
    started = []
    monkeypatch.setattr(doctor, "can_move", lambda: True)
    monkeypatch.setattr(failover, "move_blocked", lambda: "")
    monkeypatch.setattr(doctor, "may_move", lambda name, moves, now: "")
    monkeypatch.setattr(autopilot, "ranking", lambda tid: [cand(2, "ir-b")])
    monkeypatch.setattr(failover, "room_for", lambda c, n: False)
    monkeypatch.setattr(failover, "_make_plan", lambda iran, ids, reason, target_id=None: {"pulled": [], "problems": [], "target": {"id": target_id}})
    monkeypatch.setattr(doctor, "move_now", lambda tid, to, reason, why, **kw: started.append((tid, to, reason, why)) or 1)
    doc = {"moved": {}, "last_move": 0}
    assert chronic._try_move(row(), set(), doc, NOW) == ""
    tid, to, reason, why = started[0]
    assert (tid, to, reason) == (1, 2, "quality") and "cut 8 times in 7 days" in why and "ir-b" in why        # (the reason "quality": its limits - a day's moves, the cooldown - are the same)
    assert "protected" in chronic._try_move(row(protected=True), set(), doc, NOW)
    assert "cut now" in chronic._try_move(row(state="down"), set(), doc, NOW)
    assert "24 hours" in chronic._try_move(row(), set(), {"moved": {}, "last_move": NOW - 3600}, NOW)       # (one a day for the whole panel)
    assert "week" in chronic._try_move(row(), set(), {"moved": {"x-tun": NOW - 86400}, "last_move": 0}, NOW)                # (one per tunnel in a week)
    monkeypatch.setattr(doctor, "may_move", lambda name, moves, now: "x-tun moved 40 minutes ago; the next move waits 2 hours")
    assert "40 minutes ago" in chronic._try_move(row(), set(), doc, NOW)
    monkeypatch.setattr(doctor, "may_move", lambda name, moves, now: "")
    assert "stays" in chronic._try_move(row(), {2}, doc, NOW)                                                # (the only candidate carries a chronic tunnel itself)
    monkeypatch.setattr(failover, "_make_plan", lambda iran, ids, reason, target_id=None: {"pulled": ["other-tun"], "problems": [], "target": {"id": target_id}})
    assert "would pull other-tun" in chronic._try_move(row(), set(), doc, NOW)                               # (a DNS record other tunnels share is never moved)
    monkeypatch.setattr(doctor, "can_move", lambda: False)
    assert "only tells you" in chronic._try_move(row(), set(), doc, NOW)
    assert len(started) == 1


def test_the_job_and_the_docs_are_wired():
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent
    assert 'jobs.register("chronic", chronic.EVERY, chronic.run)' in (root / "nikatel" / "main.py").read_text(encoding="utf-8")
    assert "chronic.py" in (root / "ARCHITECTURE.md").read_text(encoding="utf-8")


def test_the_cuts_the_doctor_remembers_for_the_pair_count_too_a_rebuild_does_not_reset_them():
    with session() as db:
        ir, fr = Server(name="ch-ir", host="203.0.113.201", role="iran", online=True), Server(name="ch-fr", host="198.51.100.201", role="foreign", online=True)
        db.add_all([ir, fr])
        db.flush()
        db.add(Tunnel(name="ch-tun", type="backpack", transport="wssmux", port=9990, iran_server_id=ir.id, foreign_server_id=fr.id, state="up", forwarded_ports=[8076], foreign_tunnel_ip="10.2.77.2", record={}, secrets={}, enabled=True))
        iid, fid = ir.id, fr.id

    now = int(time.time())

    for i in range(9):
        knowledge.remember(iid, fid, "lossy_route", "ch-tun", [], False, 60, now - i * 600)          # (nine repairs of this pair in the last two hours: the tunnel row is new, its own check history is empty)

    row = next(r for r in chronic._gather(now) if r["name"] == "ch-tun")
    assert row["week"] >= 9 and row["day"] >= 9 and chronic.is_chronic(row["week"], row["day"])
