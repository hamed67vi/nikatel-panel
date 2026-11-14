"""A PROTECTED tunnel that loses packets (1.69.271). The owner, 2026-11-14: the lifeline of the L2TP product (protected) lost 15-65 % of its packets every evening and the smart repair ran about twenty times a
day - each run tested candidates beside it for eight minutes and was refused at the swap, because a protected tunnel is never replaced. Now a protected tunnel is not offered what protection forbids, is looked
at every six hours at most, and the story says whether a fresh link between the same two servers is cleaner (then the loss belongs to THIS link and a new link would cure it - the owner's call)."""
import itertools

from sqlalchemy import select

from nikatel.db import session
from nikatel.models import Event, Server, Tunnel
from nikatel.services import (
    analyst,
    failover,
    freshlook,
    knowledge,
    linkprobe,
    tunnel_test,
)
from nikatel.services import autopilot as ap

_N = itertools.count(40)
NOW = 1_800_000_000

LOSSY = {"cause": "lossy_route", "summary": "the link loses packets", "kind": "tcpmux", "state": "degraded", "where": None, "ext_blocked": False, "dial_i": True, "dial_f": True}
CUT = {"cause": "units_down", "summary": "a unit is not running", "kind": "tcpmux", "state": "down", "where": None, "ext_blocked": False, "dial_i": True, "dial_f": True}


def world(protected: bool, loss: float | None = 30.0):
    n = next(_N)

    with session() as db:
        ir, fr = Server(name=f"fl-ir-{n}", host=f"203.0.113.{n}", role="iran", online=True), Server(name=f"fl-fr-{n}", host=f"198.51.100.{n}", role="foreign", online=True)
        db.add_all([ir, fr])
        db.flush()
        rec = {"link": "gre", "mtu": 1360, "network": f"10.77.{n}.0/30", **({"protected": True} if protected else {})}
        t = Tunnel(name=f"fl-t-{n}", type="backhaul", transport="tcpmux", port=9900 + n, iran_server_id=ir.id, foreign_server_id=fr.id, state="degraded", forwarded_ports=[8000 + n],
                   iran_tunnel_ip=f"10.77.{n}.1", foreign_tunnel_ip=f"10.77.{n}.2", record=rec, detail={"loss_pct": loss}, secrets={"token": "t0k", "gre_key": 77})
        db.add(t)
        db.flush()
        return t.id, t.name


def fake(monkeypatch, d):
    """The diagnosis and the story faked; every door that would build, test beside the tunnel or move it records its call."""
    said, calls = [], []
    monkeypatch.setattr(ap, "diagnose", lambda tid: d)
    monkeypatch.setattr(ap, "_say", lambda aid, line, done=None, ok=None: said.append(line))
    monkeypatch.setattr(ap, "storm_now", lambda: False)
    monkeypatch.setattr(analyst, "hints_for", lambda tunnel, cause, steps: (list(steps), []))
    monkeypatch.setattr(ap.time, "sleep", lambda s: None)
    monkeypatch.setattr(ap, "_recommend_now", lambda *a, **k: calls.append("recommend") or [])
    monkeypatch.setattr(failover, "after_ladder", lambda *a, **k: calls.append("after_ladder") or False)
    monkeypatch.setattr(ap, "probe_other_iran", lambda *a, **k: calls.append("probe_other_iran"))
    monkeypatch.setattr(ap, "_rebuild_safe", lambda *a, **k: calls.append("rebuild") or (False, ["no"]))
    monkeypatch.setattr(knowledge, "plan_for", lambda *a, **k: ([], []))
    return said, calls


def fresh_link(monkeypatch, loss, ok=True):
    asked = []

    def probe(iran_id, foreign_id, links=None, record=True):
        asked.append((links, record))
        return {"gre": {"ok": ok, "loss_pct": loss, "error": "" if ok is not None else "it did not start"}}

    monkeypatch.setattr(linkprobe, "probe", probe)
    return asked


# ------------------------------------------------------------------ the pure parts
def test_the_verdict_tells_this_link_from_the_route():
    assert freshlook.verdict(30.0, 0.0) == "link" and freshlook.verdict(30.0, 10.0) == "link" and freshlook.verdict(None, 0.0) == "link"
    assert freshlook.verdict(30.0, 40.0) == "route" and freshlook.verdict(30.0, 100.0) == "route"
    assert freshlook.verdict(3.0, 0.0) == "clean" and freshlook.verdict(30.0, None) == "unknown"


def test_the_sentences_say_what_it_means_and_that_nothing_was_changed():
    link = freshlook.sentence("lifeline", 30.0, 0.0, "link")
    assert "THIS link" in link and "protected" in link and "nothing was changed" in link and "Relink" in link
    assert "route is poor" in freshlook.sentence("lifeline", 30.0, 40.0, "route") and "30 %" not in freshlook.sentence("lifeline", 30.0, 40.0, "route")
    assert "could not be made" in freshlook.sentence("lifeline", 30.0, None, "unknown", "both servers must be on")


def test_a_look_is_made_every_six_hours_at_most():
    assert freshlook.gap_left({}, NOW) == 0 and freshlook.gap_left(None, NOW) == 0
    rec = {"fresh_look": {"at": NOW - 3600}}
    assert freshlook.gap_left(rec, NOW) == 5 * 3600 and freshlook.gap_left({"fresh_look": {"at": NOW - 7 * 3600}}, NOW) == 0


def test_a_degraded_protected_tunnel_is_repaired_at_most_every_six_hours_a_cut_one_as_before():
    assert ap.retry_gap("degraded", 3, True) == 6 * 3600 and ap.retry_gap("degraded", 3, False) == 1800
    assert ap.retry_gap("down", 1, True) == 60 and ap.retry_gap("down", 4, True) == 600 and ap.retry_gap("down", 4, False) == 600


# ------------------------------------------------------------------ the look
def test_a_clean_fresh_link_means_the_loss_belongs_to_this_link(monkeypatch):
    tid, name = world(True)
    asked = fresh_link(monkeypatch, 0.0)
    r = freshlook.look(ap._snapshot(tid), NOW)
    assert r["verdict"] == "link" and not r["skipped"] and "THIS link" in r["text"]
    assert asked == [(["gre"], False)]                                              # one temporary GRE link, nothing kept in the probe's memory
    kept = ap._snapshot(tid)["record"]["fresh_look"]
    assert kept == {"at": NOW, "own": 30.0, "fresh": 0.0, "verdict": "link"}
    assert ap._snapshot(tid)["record"]["protected"] is True and ap._snapshot(tid)["record"]["network"].startswith("10.77.")          # the record is otherwise untouched
    with session() as db:
        assert db.scalar(select(Event).where(Event.ref == name, Event.level == "warn", Event.text.like("%THIS link%")))


def test_a_second_look_within_six_hours_is_not_made(monkeypatch):
    tid, _ = world(True)
    asked = fresh_link(monkeypatch, 0.0)
    freshlook.look(ap._snapshot(tid), NOW)
    r = freshlook.look(ap._snapshot(tid), NOW + 3600)
    assert r["skipped"] and "not again for 300 min" in r["text"] and len(asked) == 1
    again = freshlook.look(ap._snapshot(tid), NOW + 6 * 3600 + 1)
    assert not again["skipped"] and len(asked) == 2


def test_a_fresh_link_that_loses_too_means_the_route_and_makes_no_event(monkeypatch):
    tid, name = world(True)
    fresh_link(monkeypatch, 40.0)
    r = freshlook.look(ap._snapshot(tid), NOW)
    assert r["verdict"] == "route"
    with session() as db:
        assert db.scalar(select(Event).where(Event.ref == name, Event.level == "warn")) is None


def test_a_probe_that_cannot_run_is_told_and_never_stops_the_repair(monkeypatch):
    tid, _ = world(True)

    def refused(*a, **k):
        raise ValueError("both servers must be on")

    monkeypatch.setattr(linkprobe, "probe", refused)
    r = freshlook.look(ap._snapshot(tid), NOW)
    assert r["verdict"] == "unknown" and "both servers must be on" in r["text"]
    fresh_link(monkeypatch, None, ok=None)
    t2, _ = world(True)
    assert freshlook.look(ap._snapshot(t2), NOW)["verdict"] == "unknown"


# ------------------------------------------------------------------ the repair
def test_a_protected_lossy_tunnel_is_not_tested_beside_swapped_or_moved(monkeypatch):
    tid, _ = world(True)
    said, calls = fake(monkeypatch, LOSSY)
    asked = fresh_link(monkeypatch, 0.0)
    assert ap.smart_fix(5, tid, "switch", "auto") is False
    assert calls == []                                                               # no candidates tested beside it, no move, no test from another Iran server, no rebuild
    assert len(asked) == 1
    assert any("is protected" in ln and "switch, move" in ln for ln in said)
    assert any("THIS link" in ln for ln in said) and any("it is protected" in ln and "nothing is offered" in ln for ln in said)
    assert not any("Move to another Iran server" in ln for ln in said)


def test_the_next_repair_of_a_protected_tunnel_does_not_look_again(monkeypatch):
    tid, _ = world(True)
    said, calls = fake(monkeypatch, LOSSY)
    asked = fresh_link(monkeypatch, 0.0)
    ap.smart_fix(5, tid, "switch", "auto")
    said.clear()
    ap.smart_fix(6, tid, "switch", "auto")
    assert len(asked) == 1 and any("not again for" in ln for ln in said) and calls == []


def test_a_protected_tunnel_that_is_cut_is_still_restarted(monkeypatch):
    tid, _ = world(True)
    said, calls = fake(monkeypatch, CUT)
    asked = fresh_link(monkeypatch, 0.0)
    from nikatel.services import actions

    monkeypatch.setattr(actions, "fix", lambda tunnel_id, source="manual": 1)
    monkeypatch.setattr(ap, "_wait", lambda build_aid, limit=900: (True, ["Tunnel is ready."]))
    monkeypatch.setattr(ap, "_holds", lambda aid, tunnel_id, seconds=0: True)
    assert ap.smart_fix(7, tid, "switch", "auto") is True
    assert "rebuild" not in calls and asked == []                                    # the rebuild step is not offered; no fresh-link look for a cut that is not a loss
    assert any("rebuild, switch" in ln and "protected" in ln for ln in said)


def test_an_unprotected_lossy_tunnel_is_repaired_as_before(monkeypatch):
    tid, _ = world(False)
    said, calls = fake(monkeypatch, LOSSY)
    asked = fresh_link(monkeypatch, 0.0)

    def no_data(tid_):
        raise ValueError("no data passes right now")

    monkeypatch.setattr(tunnel_test, "quality", no_data)
    assert ap.smart_fix(8, tid, "switch", "auto") is False
    assert "recommend" in calls and "after_ladder" in calls and "probe_other_iran" in calls          # candidates tested, then the move
    assert asked == [] and not any("protected" in ln for ln in said)
