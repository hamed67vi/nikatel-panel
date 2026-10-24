"""The Doctor does nothing to a tunnel whose server is off (the owner, 2026-10-24, with a picture of a tunnel row that says "repairing…", has no menu and does not open: "when a server is off the Doctor must see
it and not do extra work").

The real case, read from the panel's own log: a smart repair began while both servers answered, the Iran server went off a minute later, and the repair went on - it took the tunnel off both servers, then tried
26 tunnel types one after the other, each build waiting 30 s for a server that was off (17 minutes). All that time the row was a placeholder with no window, and the 26 types were held against the pair as
"blocked for two weeks". Now:

  start    a repair (the button, the Doctor, "fix all", a type chosen by hand, a move) is refused / does nothing while a server of the tunnel is off - nothing is asked of the servers or of the AI
  during   the server is looked at again before every step and before every tunnel type; when it is off the repair ends there, the tunnel (if it was already taken off) goes back in the list as down at once
  memory   a build that died because the server went off is "could not be tried", never "blocked" - and the ones the old Doctor kept as blocked are read the same way
"""
import time

import pytest
from sqlalchemy import select

from nikatel.config import get_settings
from nikatel.db import session
from nikatel.models import ActionLog, Event, Server, Tunnel
from nikatel.services import (
    analyst,
    builder,
    failover,
    knowledge,
    tunnel_test,
)
from nikatel.services import autopilot as ap
from nikatel.services import settings as st
from nikatel.services.tunnels import servers_off_of

LOST = "Stopped: ssh connection lost (Connection timed out). Everything created was removed again."
CANNOT_PING = "Stopped: the IPIP link is up but the inner addresses cannot ping each other (Iran→foreign 7/10, foreign→Iran 9/10). Everything created was removed again."
CUT = {"cause": "route_cut", "summary": "the two servers cannot reach each other (loss 100.0% / 100.0%)", "kind": "tcpmux", "state": "down", "where": "foreign_side", "ext_blocked": False, "dial_i": True, "dial_f": True}


@pytest.fixture(autouse=True)
def _writable(monkeypatch):
    monkeypatch.setattr(get_settings(), "read_only", False)          # (the tests run read-only)
    monkeypatch.setattr(builder, "run_action", lambda *a, **k: "")


def world(tag, name="off-tun", state="down"):
    with session() as db:
        n = len(db.scalars(select(Server)).all()) + 40
        ir, fr = Server(name="ir-" + tag, host=f"203.0.113.{n}", role="iran", online=True), Server(name="fr-" + tag, host=f"198.51.100.{n}", role="foreign", online=True)
        db.add_all([ir, fr])
        db.flush()
        t = Tunnel(name=name, type="backhaul", transport="tcpmux", port=9911, iran_server_id=ir.id, foreign_server_id=fr.id, state=state, forwarded_ports=[8081], iran_tunnel_ip="10.99.1.1", foreign_tunnel_ip="10.99.1.2",
                   record={"mtu": 1360, "keepalive": 75, "opts": {}, "network": "10.99.1.0/30"}, secrets={"token": "t0k", "gre_key": 5}, created_at=1_790_000_000)
        db.add(t)
        db.flush()
        return t.id, ir.id, fr.id


def power(server_id, online):
    with session() as db:
        db.get(Server, server_id).online = online


def tunnel_by_name(name):
    with session() as db:
        t = db.scalar(select(Tunnel).where(Tunnel.name == name))
        return None if t is None else {c.name: getattr(t, c.name) for c in Tunnel.__table__.columns}


def events_about(name):
    with session() as db:
        return [e.text for e in db.scalars(select(Event).where(Event.ref == name))]


def say_to(monkeypatch):
    said = []
    monkeypatch.setattr(ap, "_say", lambda aid, line, done=None, ok=None: said.append(line))
    return said


# ------------------------------------------------------------------ the question itself
def test_the_pair_is_asked_fresh_by_id():
    _tid, ir, fr = world("ask1", "ask-tun")
    assert servers_off_of(ir, fr) == []
    power(ir, False)
    assert servers_off_of(ir, fr) == ["Iran server ir-ask1 is off or does not answer"]
    power(fr, False)
    assert servers_off_of(ir, fr) == ["Iran server ir-ask1 is off or does not answer", "Foreign server fr-ask1 is off or does not answer"]
    power(ir, None)                                                                            # (not known yet is not off)
    assert servers_off_of(ir, fr) == ["Foreign server fr-ask1 is off or does not answer"]

    with session() as db:
        db.get(Server, fr).enabled = False                                                     # (a server the owner switched off is not "off")

    assert servers_off_of(ir, fr) == [] and servers_off_of(99999, 99998) == []


# ------------------------------------------------------------------ the start
def test_a_repair_that_finds_a_server_off_does_nothing(monkeypatch):
    tid, ir, _fr = world("none1", "none-tun")
    power(ir, False)
    said = say_to(monkeypatch)
    monkeypatch.setattr(ap, "diagnose", lambda t: (_ for _ in ()).throw(AssertionError("the servers and the AI are not asked while a server is off")))
    monkeypatch.setattr(ap, "_rebuild_safe", lambda *a, **k: (_ for _ in ()).throw(AssertionError("nothing is rebuilt")))
    monkeypatch.setattr(ap, "_replace", lambda *a, **k: (_ for _ in ()).throw(AssertionError("nothing is replaced")))
    assert ap.smart_fix(5, tid, "switch", "manual") is False
    assert len(said) == 1 and "Iran server ir-none1 is off or does not answer" in said[0] and "nothing is done to this tunnel until it is back" in said[0]
    t = tunnel_by_name("none-tun")
    assert t is not None and t["id"] == tid and t["secrets"] == {"token": "t0k", "gre_key": 5}


def test_the_button_and_the_doctor_are_refused_at_the_start_with_the_plain_sentence():
    tid, _ir, fr = world("start1", "start-tun")
    power(fr, False)

    with pytest.raises(ValueError, match="Foreign server fr-start1 is off or does not answer.*nothing is done to this tunnel"):
        ap.smart_fix_start(tid, "switch", source="auto")

    with pytest.raises(ValueError, match="is off or does not answer"):
        ap.replace_start(tid, "wssmux")

    with session() as db:
        assert db.scalar(select(ActionLog.id).where(ActionLog.ref == "start-tun")) is None     # (no story, no action: nothing was started)

    assert tunnel_by_name("start-tun") is not None


def test_a_move_may_leave_an_iran_server_that_is_off_but_not_go_to_one(monkeypatch):
    tid, ir, fr = world("mv1", "mv-tun")
    other = world("mv1-other", "mv-other")[1]
    started = []
    monkeypatch.setattr(ap, "_start", lambda label, fn, *a, **k: started.append(label) or 1)
    power(ir, False)                                                                           # (the very reason to move)
    assert ap.move_start(tid, other) == 1 and started == ["mv-tun"]
    power(other, False)

    with pytest.raises(ValueError, match="Iran server ir-mv1-other is off or does not answer"):
        ap.move_start(tid, other)

    power(other, True)
    power(fr, False)

    with pytest.raises(ValueError, match="Foreign server fr-mv1 is off or does not answer"):
        ap.move_start(tid, other)

    assert started == ["mv-tun"]


def test_fix_all_leaves_the_tunnels_of_a_server_that_is_off_alone(monkeypatch):
    _a, ia, _fa = world("all1", "all-off")
    b, ib, _fb = world("all2", "all-on")
    power(ia, False)
    said = say_to(monkeypatch)
    fixed = []
    monkeypatch.setattr(ap, "smart_fix", lambda aid, tid, level, source="manual", finish=True: fixed.append(tid) or True)
    ap.fix_all(1, "rebuild")
    assert fixed == [b] and any(ln.startswith("all-off: Iran server ir-all1 is off or does not answer") for ln in said)
    fixed.clear()
    said.clear()
    power(ib, False)
    ap.fix_all(1, "rebuild")                                                                   # (both held: the story says so and ends)
    assert fixed == [] and any("Nothing to repair now: 2 tunnel(s) wait for a server that is off." in ln for ln in said)


def test_the_plain_restart_asks_no_server_that_is_off(monkeypatch):
    """The first step of every repair, and the Fix button: it used to ask both servers over SSH first - for a server that is off that is its whole timeout, and the answer was always "does not answer"."""
    from nikatel.services import actions

    tid, _ir, fr = world("fix1", "fix-tun")
    power(fr, False)
    monkeypatch.setattr(actions, "_query", lambda *a, **k: (_ for _ in ()).throw(AssertionError("no question to a server that is off")))

    with session() as db:
        e = ActionLog(ref="fix-tun", action="fix", source="manual", story=["Starting…"])
        db.add(e)
        db.flush()
        aid = e.id

    actions._fix(tid, aid, False, "manual")

    with session() as db:
        e = db.get(ActionLog, aid)
        assert e.action == "fix-skip" and e.ok is False and e.finished_at and e.story[-1].startswith("Foreign server fr-fix1 is off or does not answer: nothing is asked of the servers")        # (not counted against the hourly budget of restarts)


# ------------------------------------------------------------------ during
def test_a_server_that_goes_off_between_the_steps_ends_the_repair_there(monkeypatch):
    tid, ir, _fr = world("mid1", "mid-tun")
    said = say_to(monkeypatch)
    monkeypatch.setattr(ap, "diagnose", lambda t: {"cause": "units_down", "summary": "tunnel services are not running", "kind": "tcpmux", "state": "down", "ext_blocked": False})
    monkeypatch.setattr(analyst, "hints_for", lambda tunnel, cause, steps: (list(steps), []))
    monkeypatch.setattr(ap, "storm_now", lambda: False)
    calls = []

    def restart(aid, limit=900):                                                               # (the restart is the first step: the Iran server stops answering while it runs)
        power(ir, False)
        return False, ["still down"]

    monkeypatch.setattr(ap.actions, "fix", lambda tunnel_id, source="manual": 1)
    monkeypatch.setattr(ap, "_wait", restart)
    monkeypatch.setattr(ap, "_rebuild_safe", lambda *a, **k: calls.append("rebuild") or (False, ["x"]))
    monkeypatch.setattr(ap, "_replace", lambda *a, **k: calls.append("replace") or False)
    monkeypatch.setattr(analyst, "record_case", lambda *a, **k: calls.append("case"))
    monkeypatch.setattr(knowledge, "remember", lambda *a, **k: calls.append("remember"))
    monkeypatch.setattr(failover, "after_ladder", lambda *a, **k: calls.append("failover") or False)
    assert ap.smart_fix(5, tid, "switch", "auto") is False
    assert calls == []                                                                         # no rebuild, no replace, no move, and nothing kept as a verdict about the route
    assert any("Before the step “rebuild”: Iran server ir-mid1 is off or does not answer" in ln for ln in said)
    assert any(ln.startswith("mid-tun: stopped - Iran server ir-mid1 is off or does not answer") for ln in said)
    assert any("stopped: Iran server ir-mid1 is off or does not answer" in e for e in events_about("mid-tun"))
    assert tunnel_by_name("mid-tun")["id"] == tid


def switch_world(monkeypatch, d):
    said = say_to(monkeypatch)
    monkeypatch.setattr(ap, "diagnose", lambda tid: d)
    monkeypatch.setattr(ap, "storm_now", lambda: False)
    monkeypatch.setattr(analyst, "hints_for", lambda tunnel, cause, steps: (list(steps), []))
    monkeypatch.setattr(ap.time, "sleep", lambda s: None)
    monkeypatch.setattr(ap, "_probe_links", lambda aid, snap, ks: [k for k in ks if tunnel_test.kind_link(k) == "gre"])
    monkeypatch.setattr(ap, "_unfit_servers", lambda snap: {})
    monkeypatch.setattr(ap, "_carrier_last_resort", lambda aid, snap, tried: [])
    monkeypatch.setattr(ap, "_wait_quiet", lambda aid, tid: True)
    return said


def test_the_real_case_the_server_goes_off_during_the_types_and_the_run_ends_at_once(monkeypatch):
    """19:55 on the owner's panel, replayed: the second build finds the Iran server gone; 24 more builds, 30 s each, were tried before."""
    tid, ir, fr = world("real1", "real-tun")
    said = switch_world(monkeypatch, CUT)
    built, calls = [], []

    def try_build(snap, iran_id, foreign_id, kind, opts=None):
        built.append(kind)

        if len(built) == 2:
            power(ir, False)                                                                   # (the pulse marks the server off ~8 s after it stopped)
            return False, [LOST]

        return False, [CANNOT_PING if len(built) == 1 else LOST]

    monkeypatch.setattr(ap, "_try_build", try_build)
    monkeypatch.setattr(analyst, "record_case", lambda *a, **k: calls.append("case"))
    monkeypatch.setattr(knowledge, "remember", lambda *a, **k: calls.append("remember"))
    monkeypatch.setattr(failover, "after_ladder", lambda *a, **k: calls.append("failover") or False)
    monkeypatch.setattr(ap, "probe_other_iran", lambda *a, **k: calls.append("probe"))
    assert ap.smart_fix(7, tid, "switch", "auto") is False
    assert len(built) == 2                                                                     # not 26: the look before the third type ended it (and the old type was not built on a server that is off)
    assert calls == []                                                                         # no move to another Iran server, no test from there, no verdict about the route
    t = tunnel_by_name("real-tun")
    assert t is not None and t["id"] == tid and t["state"] == "down" and "Iran server ir-real1 is off or does not answer" in t["last_error"]      # (put back at once: no placeholder, the window opens)
    assert any("put back in the list as DOWN" in ln for ln in said) and any("The tunnel was already taken off for this repair" in ln for ln in said)
    assert st.get("pending_builds").get("items", []) == []
    assert any("stopped: Iran server ir-real1 is off or does not answer" in e for e in events_about("real-tun"))
    p = knowledge.pair(ir, fr)
    assert knowledge.verdict(p["kinds"][built[0]]) == "failed" and knowledge.verdict(p["kinds"][built[1]]) == "untested"          # (the type that really failed is held against the pair; the one the server's loss killed is not)
    assert built[1] not in knowledge.blocked(p, int(time.time())) and built[0] in knowledge.blocked(p, int(time.time()))


def test_a_server_that_is_off_before_the_first_type_removes_nothing(monkeypatch):
    tid, _ir, fr = world("real2", "real2-tun")
    said = switch_world(monkeypatch, CUT)
    monkeypatch.setattr(ap, "_try_build", lambda *a, **k: (_ for _ in ()).throw(AssertionError("nothing is built")))
    snap = ap._snapshot(tid)
    power(fr, False)

    with pytest.raises(ap.ServerOff, match="Foreign server fr-real2 is off or does not answer"):
        ap._switch_cut(1, snap, ["wssmux", "tcpmux"])

    assert tunnel_by_name("real2-tun")["state"] == "down" and tunnel_by_name("real2-tun")["last_error"] == ""         # (it was never touched: not even the row)
    assert any("Nothing is removed or built." in ln for ln in said) and not any("put back" in ln for ln in said)


def test_the_old_type_is_not_built_again_on_a_server_that_went_off(monkeypatch):
    tid, _ir, fr = world("real3", "real3-tun")
    said = switch_world(monkeypatch, CUT)
    built = []

    def try_build(snap, iran_id, foreign_id, kind, opts=None):
        built.append(kind)
        power(fr, False)                                                                       # (the only type in the queue fails and the server is gone when the loop looks again)
        return False, [CANNOT_PING]

    monkeypatch.setattr(ap, "_try_build", try_build)
    snap = ap._snapshot(tid)

    with pytest.raises(ap.ServerOff):
        ap._switch_cut(1, snap, ["wssmux"])

    assert built == ["wssmux"] and tunnel_by_name("real3-tun")["state"] == "down"             # (no second build of the old kind: the row went back)
    assert any("put back in the list as DOWN" in ln for ln in said)


def test_a_replace_for_a_pair_with_a_server_off_removes_nothing_but_a_failover_may_leave_one(monkeypatch):
    tid, ir, fr = world("rep1", "rep-tun")
    say_to(monkeypatch)
    built = []
    monkeypatch.setattr(ap, "_try_build", lambda snap, i, f, kind, opts=None: built.append(kind) or (True, ["Tunnel is ready."]))
    power(fr, False)
    out = []
    assert ap._replace(1, tid, ir, fr, "wssmux", "Chosen by you.", out=out) is False
    assert built == [] and out and out[-1].startswith(ap.HELD) and tunnel_by_name("rep-tun") is not None and st.get("pending_builds").get("items", []) == []
    power(fr, True)
    power(ir, False)                                                                           # a failover: the old Iran server is off on purpose, the new pair is what must answer
    other = world("rep1-other", "rep-other")[1]
    assert ap._replace(1, tid, other, fr, "wssmux", "Failover.", skip_offline=True) is True and built == ["wssmux"]


def test_a_rebuild_is_refused_before_it_removes_anything_and_stops_after_a_failed_build(monkeypatch):
    tid, ir, _fr = world("rb1", "rb-tun")
    said = say_to(monkeypatch)
    seen = []

    def rebuild(tunnel_id, fresh_link=False, check=True):
        seen.append(check)
        builder.delete_tunnel(tunnel_id, trash=True)
        return 9

    monkeypatch.setattr(builder, "rebuild_tunnel", rebuild)
    built = []
    monkeypatch.setattr(ap, "_try_build", lambda snap, i, f, kind, opts=None: built.append(kind) or (True, ["Tunnel is ready."]))
    power(ir, False)
    ok, story = ap._rebuild_safe(3, tid)
    assert ok is False and "Iran server ir-rb1 is off or does not answer" in story[0] and seen == [] and tunnel_by_name("rb-tun") is not None
    power(ir, True)
    monkeypatch.setattr(ap, "_wait", lambda aid, limit=900: (power(ir, False) or False, [LOST]))      # (the server stops in the middle of the build)
    ok, story = ap._rebuild_safe(3, tid)
    assert ok is False and built == [] and seen == [True]                                      # the old type is not built on a server that is off ...
    t = tunnel_by_name("rb-tun")
    assert t is not None and t["state"] == "down" and any("put back in the list as DOWN" in ln for ln in said)          # ... the row goes back at once


def test_the_hand_rebuild_is_refused_by_the_api_and_follow_the_new_address_is_not(client, monkeypatch):
    tid, ir, fr = world("api1", "api-tun")
    power(ir, False)
    r = client.post(f"/api/tunnels/{tid}/rebuild")
    assert r.status_code == 400 and "Iran server ir-api1 is off or does not answer" in r.json()["error"] and "not removed" in r.json()["error"]
    assert tunnel_by_name("api-tun") is not None
    r = client.post("/api/autopilot/smart-fix", json={"tunnel_id": tid, "level": "switch"})
    assert r.status_code == 400 and "nothing is done to this tunnel until it is back" in r.json()["error"]
    assert client.post("/api/autopilot/replace", json={"tunnel_id": tid, "kind": "wssmux"}).status_code == 400
    # a server that got a new address: its flag still says off for a few seconds after the edit, so that rebuild is not asked
    monkeypatch.setattr(builder, "delete_tunnel", lambda t, **kw: ["Iran: removed", "foreign: removed"])
    started = []
    monkeypatch.setattr(builder, "start_build", lambda *a, **k: started.append(a) or 5)
    assert builder.rebuild_tunnel(tid, check=False) == 5 and started

    with pytest.raises(ValueError, match="is off or does not answer"):
        builder.rebuild_tunnel(tid)

    asked = []
    monkeypatch.setattr(ap, "_start", lambda label, fn, *a, **k: fn(1) or 1)
    monkeypatch.setattr(ap, "_rebuild_safe", lambda aid, t, fresh_link=False, check=True: asked.append(check) or (True, ["Tunnel is ready."]))
    monkeypatch.setattr(ap, "_say", lambda *a, **k: None)
    builder.follow_server_host(fr, "198.51.100.1", "198.51.100.2")
    assert asked == [False]


# ------------------------------------------------------------------ memory
def test_a_build_that_died_with_the_server_is_not_a_verdict_on_the_route():
    now = 1_790_000_000
    assert knowledge.server_lost(LOST) and knowledge.server_lost(["x", "Iran server a is off or does not answer"]) and knowledge.server_lost("the server does not answer over SSH")
    assert not knowledge.server_lost(CANNOT_PING) and not knowledge.server_lost(None) and not knowledge.server_lost([])
    e = knowledge.merge(None, {"kind": "bp:tcp", "ok": False, "error": LOST}, "latency", now)
    assert e["last"]["skipped"] == "server" and knowledge.verdict(e) == "untested" and "hist" not in e
    assert knowledge.verdict(knowledge.merge(None, {"kind": "bp:tcp", "ok": False, "error": CANNOT_PING}, "latency", now)) == "failed"
    assert knowledge.verdict(knowledge.merge(None, {"kind": "tcpmux", "ok": True, "works": True, "score": 70}, "latency", now)) == "good"          # (a build that carried data is never "could not be tried", whatever its story says)
    old = {"last": {"ok": False, "measured": False, "at": now, "measure": "latency", "score": None, "error": LOST[:140]}}          # what the Doctor kept before it knew: 26 of them on the owner's pair
    pair = {"kinds": {"bp:kcp": old, "bp:quic": {"last": {**old["last"], "error": CANNOT_PING[:140]}}}}
    assert knowledge.verdict(old) == "untested" and knowledge.blocked(pair, now + 3600) == ["bp:quic"]
    assert knowledge.untested(pair) == [{"kind": "bp:kcp", "reason": "server", "error": LOST[:140], "at": now}]
