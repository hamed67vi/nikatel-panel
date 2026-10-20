"""A tunnel that is CUT gets a tunnel that connects, whichever type it is (the owner, 2026-10-16: the foreign server of pl-ir3 was filtered, three tunnel types connected to it - and the Doctor built
none of them because they scored 36 - 42 by delay alone; a restart "fixed" another tunnel that was cut again a minute later and the restart was tried again and again; four restarts in a row went on
for three minutes over a GRE link that was filtered).

The order of the replacements for a cut tunnel (who can still dial whom, what healed the pair before), the first one that connects is built as the real tunnel, the steps that did not hold are not
repeated, a repair has to hold, the plain restart is tried only where it can help, and the Doctor starts the climb by rules - and keeps in its memory why a tunnel was cut and what healed it."""
import time

from sqlalchemy import select

from nikatel.db import session
from nikatel.models import ActionLog, RepairCase, Server, Tunnel
from nikatel.services import (
    actions,
    analyst,
    bandwidth,
    failover,
    knowledge,
    tunnel_test,
)
from nikatel.services import autopilot as ap

NOW = 1_800_000_000


# ------------------------------------------------------------------ the order of the replacements
def test_a_cut_tunnel_tries_the_kinds_by_who_can_still_dial_whom():
    assert set(knowledge.PRIOR) == tunnel_test.KIND_KEYS - tunnel_test.GRID_ONLY and len(knowledge.PRIOR) == len(set(knowledge.PRIOR))          # the standard order names every kind of the tunnel test, once
    assert set(knowledge.CUT_REVERSE) | set(knowledge.CUT_FORWARD) | set(knowledge.CUT_GRE) | set(knowledge.CUT_LINKS) == tunnel_test.KIND_KEYS - tunnel_test.GRID_ONLY
    assert set(knowledge.READY) <= tunnel_test.KIND_KEYS and len(knowledge.READY) == len(set(knowledge.READY)) and "bp:wssmux" in knowledge.READY
    assert knowledge.REVERSE_KINDS == set(knowledge.CUT_REVERSE) and knowledge.FORWARD_KINDS == set(knowledge.CUT_FORWARD)
    filtered = knowledge.cut_order(False, True, True)                                                                  # the foreign address is filtered from Iran: only the foreign server can dial
    assert filtered[0] == "bp:wssmux" and filtered.index("chisel:tls") < filtered.index("bpd:pck") and not set(knowledge.GRE_KINDS) & set(filtered)
    both = knowledge.cut_order(True, True, True)                                                                       # both can dial: the fast kinds (the Iran server dials) first
    assert both[0] == "bpd:pck" and both.index("bpd:pck") < both.index("bp:wssmux")
    assert knowledge.cut_order(None, None, False)[:4] == ["tcpmux", "wssmux", "tcp", "wsmux"] and knowledge.cut_order(None, None, False)[4] == "bp:wssmux"       # nothing known: the robust kinds first, GRE first when it is not blocked
    assert knowledge.unreachable(False, True) == knowledge.FORWARD_KINDS and knowledge.unreachable(True, False) == knowledge.REVERSE_KINDS and knowledge.unreachable(None, None) == frozenset()


def test_the_candidates_of_a_cut_tunnel_put_what_is_known_to_connect_first_and_what_cannot_connect_last():
    pair = {"at": NOW, "kinds": {"bp:tcpmux": knowledge.merge(None, {"kind": "bp:tcpmux", "ok": False, "score": 38, "latency_ms": 159.0, "loss_pct": 0.0}, "latency", NOW),          # poor: it connects (score 38)
                                 "chisel:tls": knowledge.merge(None, {"kind": "chisel:tls", "ok": False, "score": 36, "latency_ms": 165.0, "loss_pct": 0.0}, "latency", NOW),
                                 "bpd:udp": knowledge.merge(None, {"kind": "bpd:udp", "ok": False, "error": "handshake did not complete"}, "latency", NOW - 60)}}              # failed a minute ago
    order = knowledge.cut_order(False, True, True)
    got = knowledge.candidates(pair, "tcpmux", True, False, NOW, limit=len(knowledge.PRIOR), base=order, unreachable_kinds=knowledge.unreachable(False, True))
    assert got[:2] == ["bp:tcpmux", "chisel:tls"]                                                                       # known to connect (even poor): first, the better score first
    assert "bpd:udp" not in got and "tcpmux" not in got                                                                 # what failed lately and the GRE kinds (GRE is the cause): left out
    assert got[2] == "bp:wssmux"                                                                                        # the unknown kinds follow in the order ...
    assert got[-6:] == ["bpd:pck", "chisel:fwd", "paqet", "chisel:fwdtls", "bpd:quic", "bpd:xdi"]                       # ... and the forward ones (the Iran server cannot dial) come last, without the one that failed
    hinted = knowledge.candidates({}, "", False, False, NOW, limit=5, base=order, prefer=["paqet", "bpd:xdi"])
    assert hinted[:2] == ["paqet", "bpd:xdi"]                                                                           # what the other pairs of the same foreign server connect with comes before what nobody tried
    only_blocked = knowledge.candidates({"kinds": {k: knowledge.merge(None, {"kind": k, "ok": False, "error": "x"}, "latency", NOW) for k in knowledge.PRIOR}}, "", False, False, NOW)
    assert only_blocked                                                                                                  # everything failed lately: they are tried again rather than nothing
    lossy = knowledge.candidates(pair, "tcpmux", False, True, NOW)                                                       # a tunnel that still carries data: the poor ones are not candidates
    assert "bp:tcpmux" not in lossy and len(lossy) == knowledge.LIMIT


def two_servers(name):
    with session() as db:
        n = len(db.scalars(select(Server)).all()) + 10
        ir, fr = Server(name="ir-" + name, host=f"203.0.113.{n}", role="iran", online=True), Server(name="fr-" + name, host=f"198.51.100.{n}", role="foreign", online=True)
        db.add_all([ir, fr])
        db.flush()
        return ir.id, fr.id


def test_the_plan_says_who_can_dial_and_what_is_known():
    ir, fr = two_servers("a")
    sibling_ir, _ = two_servers("sib")
    knowledge.record(ir, fr, {"kind": "bp:wssmux", "ok": False, "score": 42, "latency_ms": 150.0, "loss_pct": 0.0}, "latency")
    knowledge.record(sibling_ir, fr, {"kind": "chisel:tls", "ok": False, "score": 36, "latency_ms": 165.0, "loss_pct": 0.0}, "latency")          # another Iran server connects to the same foreign server with chisel:tls
    kinds, lines = knowledge.plan_for(ir, fr, "tcpmux", True, False, dial_i=False, dial_f=True)
    assert kinds[0] == "bp:wssmux" and "tcpmux" not in kinds and len(kinds) == len(knowledge.PRIOR) - 4 and kinds[1] == "chisel:tls"      # all of them, without the four GRE kinds; the neighbour's kind right after what is known here
    assert lines[0].startswith("Who can still open connections: the Iran server CANNOT reach the foreign server, the foreign server CAN reach the Iran server") and "reverse" in lines[0]
    assert any(ln.startswith("Known to connect between these two servers") and "bp:wssmux 42" in ln for ln in lines)
    assert any(ln.startswith("The other tunnels that end on this foreign server connect with: chisel:tls") for ln in lines)
    kinds, lines = knowledge.plan_for(ir, fr + 99, "tcpmux", False, True)                                                 # an unknown pair, a tunnel that only loses packets
    assert len(kinds) == knowledge.LIMIT and lines[0].startswith("Nothing is known yet") and "standard order" in lines[0]
    assert knowledge.recent_failure(knowledge.pair(ir, fr), knowledge.GRE_KINDS, int(time.time())) is False
    knowledge.record(ir, fr, {"kind": "tcpmux", "ok": False, "error": "GRE is probably filtered"}, "latency")
    assert knowledge.recent_failure(knowledge.pair(ir, fr), knowledge.GRE_KINDS, int(time.time())) is True                 # a new GRE link failed a moment ago: it is not tested again at once
    assert knowledge.recent_failure(knowledge.pair(ir, fr), knowledge.GRE_KINDS, int(time.time()) + 3600) is False


def test_what_healed_a_pair_the_last_time_goes_first_and_a_cut_is_counted():
    ir, fr = two_servers("pb")
    now = int(time.time())
    assert knowledge.playbook(ir, fr, "gre_blocked", now) == {} and knowledge.repeats(ir, fr, now) == 0 and knowledge.cut_before(ir, fr, ("gre_blocked", "route_cut"), now) is False
    knowledge.remember(ir, fr, "gre_blocked", "pl-x", [{"step": "switch", "ok": False}, {"step": "switch", "ok": True, "kind": "chisel:tls"}], True, 190, now - 3 * 86400)
    knowledge.remember(ir, fr, "gre_blocked", "pl-x", [{"step": "switch", "ok": True, "kind": "bp:wssmux"}], True, 75, now - 86400)
    knowledge.remember(ir, fr, "lossy_route", "pl-x", [{"step": "switch", "ok": True, "kind": "paqet"}], True, 60, now - 3600)
    knowledge.remember(ir, fr, "route_cut", "pl-x", [{"step": "switch", "ok": False}, {"step": "move", "ok": False}], False, 900, now - 1800)                 # nothing healed it
    pb = knowledge.playbook(ir, fr, "gre_blocked", now)
    assert pb == {"kind": "bp:wssmux", "step": "switch", "at": now - 86400, "n": 1, "secs": 75}                       # the most recent that healed THIS cause
    assert knowledge.playbook(ir, fr, "lossy_route", now)["kind"] == "paqet" and knowledge.playbook(ir, fr, "route_cut", now) == {}
    assert knowledge.playbook(ir, fr, "gre_blocked", now + 100 * 86400) == {}                                         # a quarter of a year later it is history
    assert knowledge.repeats(ir, fr, now) == 4 and knowledge.repeats(ir, fr, now, days=2) == 3 and knowledge.cut_before(ir, fr, ("gre_blocked", "route_cut"), now) is True
    assert knowledge.cut_before(ir, fr, ("gre_blocked",), now + 20 * 86400) is False
    kinds, lines = knowledge.plan_for(ir, fr, "tcpmux", True, False, dial_i=True, dial_f=True, cause="gre_blocked")
    assert kinds[0] == "bp:wssmux" and lines[0] == "The last time this happened on this pair (gre blocked), bp:wssmux healed it - it goes first."
    kinds, lines = knowledge.plan_for(ir, fr, "tcpmux", True, False, dial_i=True, dial_f=True, cause="route_cut")      # nothing healed a route cut: the order of a cut tunnel
    assert kinds[0] == "bpd:pck" and not lines[0].startswith("The last time")
    kinds, _ = knowledge.plan_for(ir, fr, "tcpmux", True, False, dial_i=False, dial_f=True, cause="gre_blocked")
    assert kinds[0] == "bp:wssmux"
    knowledge.record(ir, fr, {"kind": "bp:wssmux", "ok": False, "error": "no data passes"}, "latency")                # it failed lately: the playbook does not push it
    kinds, _ = knowledge.plan_for(ir, fr, "tcpmux", True, False, dial_i=True, dial_f=True, cause="gre_blocked")
    assert kinds[0] == "bpd:pck" and "bp:wssmux" not in kinds[:3]


# ------------------------------------------------------------------ the first kind that connects is the one to build
def test_the_tests_stop_at_the_first_candidate_that_connects(monkeypatch):
    polls = [{"done": False, "results": []},
             {"done": False, "results": [{"kind": "bp:wssmux", "ok": False, "error": "no data passes"}]},
             {"done": False, "results": [{"kind": "bp:wssmux", "ok": False, "error": "no data passes"}, {"kind": "chisel:tls", "ok": False, "score": 36, "latency_ms": 165.0, "loss_pct": 0.0}]},
             {"done": True, "results": [{"kind": "bp:wssmux", "ok": False, "error": "no data passes"}, {"kind": "chisel:tls", "ok": False, "score": 36, "latency_ms": 165.0, "loss_pct": 0.0},
                                        {"kind": "bp:tcpmux", "ok": False, "score": 38, "latency_ms": 159.0, "loss_pct": 0.0}]}]      # (the next one was already running when the stop came)
    stopped = []
    monkeypatch.setattr(ap.tunnel_test, "compare_start", lambda *a, **k: "job1")
    monkeypatch.setattr(ap.tunnel_test, "compare_status", lambda jid: polls.pop(0))
    monkeypatch.setattr(ap.tunnel_test, "compare_stop", lambda jid: stopped.append(jid))
    monkeypatch.setattr(ap, "_say", lambda *a, **k: None)
    monkeypatch.setattr(ap.time, "sleep", lambda s: None)
    got = ap._recommend_now(1, 1, 2, ["bp:wssmux", "chisel:tls", "bp:tcpmux"], first_ok=True)
    assert [r["kind"] for r in got] == ["bp:wssmux", "chisel:tls"] and stopped == ["job1"]                                # a poor tunnel (36) connects: the rest is not tried, nothing after it is returned
    assert ap.choose_better(None, got, cut=True)["kind"] == "chisel:tls"


def test_a_busy_test_slot_is_waited_for_with_the_error_the_tests_really_raise(monkeypatch):
    """compare_start raises RuntimeError("another tunnel test is still running"), not ValueError: the repair used to stop with that error instead of waiting for its turn."""
    calls = []

    def fake_start(iran_id, foreign_id, kinds, measure="latency"):
        calls.append(1)

        if len(calls) < 3:
            raise RuntimeError("another tunnel test is still running")

        return "job1"

    monkeypatch.setattr(ap.tunnel_test, "compare_start", fake_start)
    monkeypatch.setattr(ap.tunnel_test, "compare_status", lambda jid: {"done": True, "results": [{"kind": "tcpmux", "ok": True, "score": 70, "loss_pct": 0.0}]})
    monkeypatch.setattr(ap, "_say", lambda *a, **k: None)
    monkeypatch.setattr(ap.time, "sleep", lambda s: None)
    assert ap._recommend_now(1, 1, 2, ["tcpmux"])[0]["score"] == 70 and len(calls) == 3


# ------------------------------------------------------------------ the smart repair of a cut tunnel
def world(name="pl-ir1"):
    ir, fr = two_servers(name)

    with session() as db:
        t = Tunnel(name=name, type="backhaul", transport="tcpmux", port=9906, iran_server_id=ir, foreign_server_id=fr, state="down", forwarded_ports=[8072], record={})
        db.add(t)
        db.flush()
        return t.id, ir, fr


def fake_repair(monkeypatch, d, works=()):
    """The diagnosis, the builds and the story, faked: `works` are the tunnel types that connect on this route (the build of any other fails)."""
    said, attempts = [], []
    monkeypatch.setattr(ap, "diagnose", lambda tid: d)
    monkeypatch.setattr(ap, "_say", lambda aid, line, done=None, ok=None: said.append(line))
    monkeypatch.setattr(ap, "storm_now", lambda: False)
    monkeypatch.setattr(analyst, "hints_for", lambda tunnel, cause, steps: (list(steps), []))
    monkeypatch.setattr(ap.time, "sleep", lambda s: None)

    def fake_replace(aid, tid, iran, foreign, kind, why, skip_offline=False, snap=None, first=True, restore=True, out=None):
        attempts.append((kind, first, restore))
        ok = kind in works

        if out is not None:
            out.append("" if ok else "the tunnel started but no data passes through it")

        return ok

    monkeypatch.setattr(ap, "_replace", fake_replace)
    monkeypatch.setattr(ap, "_wait", lambda build_aid, limit=900: (True, ["Tunnel is ready."]))
    monkeypatch.setattr(ap, "_build_kind", lambda *a, **k: 1)
    monkeypatch.setattr(failover, "after_ladder", lambda *a, **k: False)
    monkeypatch.setattr(ap, "probe_other_iran", lambda *a, **k: None)
    return said, attempts


GRE_CUT = {"cause": "gre_blocked", "summary": "GRE (protocol 47) is filtered on this route", "kind": "tcpmux", "state": "down", "where": None, "ext_blocked": False, "dial_i": True, "dial_f": True}


def test_a_filtered_route_is_not_tried_with_the_same_method_again_the_first_type_that_connects_is_built(monkeypatch):
    """The real case: BackPack tcpmux (159 ms, score 38), BackPack wssmux (150 ms, 42) and Chisel TLS (165 ms, 36) all connected; the Doctor said "no other tunnel type works well" and built none. And it
    tested a new GRE link first, for a minute, over a route where GRE is filtered."""
    tid, ir, fr = world()
    said, attempts = fake_repair(monkeypatch, GRE_CUT, works={"bp:wssmux"})
    assert ap.ladder_for("gre_blocked", "tcpmux", "switch") == ["switch"] and ap.ladder_for("gre_blocked", "tcpmux", "rebuild") == ["relink"]          # no new GRE link where GRE is filtered
    assert ap.smart_fix(7, tid, "switch", "auto") is True
    kinds = [a[0] for a in attempts]
    assert kinds[0] == "bpd:pck" and kinds[-1] == "bp:wssmux" and "tcpmux" not in kinds and "wssmux" not in kinds              # the Iran server can dial: the fast kinds first; the one that connects stays; no GRE kind
    assert [a[1] for a in attempts] == [True] + [False] * (len(attempts) - 1) and not any(a[2] for a in attempts)               # the tunnel is removed once, and the old type is not built again while another one is tried
    assert any("any tunnel type that connects is better than none" in ln for ln in said) and any(ln.startswith("Trying bp:wssmux (8 of ") for ln in said)
    assert not any("A new GRE link" in ln or "No other tunnel type works well" in ln for ln in said)
    p = knowledge.pair(ir, fr)
    assert knowledge.verdict(p["kinds"]["bpd:pck"]) == "failed" and knowledge.verdict(p["kinds"]["bp:wssmux"]) == "poor"    # what each one answered is kept: the next time the ones that failed are not tried, the one that worked is
    assert knowledge.playbook(ir, fr, "gre_blocked")["kind"] == "bp:wssmux" and knowledge.cut_before(ir, fr, ("gre_blocked",))

    with session() as db:
        case = db.scalar(select(RepairCase).where(RepairCase.tunnel == "pl-ir1"))
        assert case.healed and next(s for s in case.steps if s["step"] == "switch")["kind"] == "bp:wssmux"

    # the same pair is cut again: it starts where the last cut ended
    knowledge.record(ir, fr, {"kind": "bpd:pck", "ok": True, "score": 80, "latency_ms": 90.0}, "full")                          # (a night survey found the fast kind working again; the playbook still goes first)
    said, attempts = fake_repair(monkeypatch, GRE_CUT, works={"bp:wssmux", "bpd:pck"})
    assert ap.smart_fix(8, tid, "switch", "auto") is True and [a[0] for a in attempts] == ["bp:wssmux"]
    assert any(ln.startswith("The last time this happened on this pair (gre blocked), bp:wssmux healed it") for ln in said)


def test_a_filtered_foreign_server_is_tried_with_a_tunnel_it_dials_in_first(monkeypatch):
    tid, _, _ = world("pl-ir2")
    said, attempts = fake_repair(monkeypatch, {**GRE_CUT, "cause": "route_cut", "summary": "the two servers cannot reach each other", "dial_i": False, "dial_f": True}, works={"bp:wssmux"})
    assert ap.smart_fix(8, tid, "switch", "auto") is True
    assert [a[0] for a in attempts] == ["bp:wssmux"]                                                                    # a route cut now climbs to "switch" (it was only "move"): the first reverse kind connects
    assert any("CANNOT reach the foreign server" in ln for ln in said)


def test_when_nothing_connects_every_kind_is_tried_the_old_type_is_put_back_and_the_answers_are_kept(monkeypatch):
    tid, ir, fr = world("pl-ir3")
    said, attempts = fake_repair(monkeypatch, {**GRE_CUT, "cause": "route_cut", "dial_i": False, "dial_f": False})
    assert ap.smart_fix(9, tid, "switch", "auto") is False
    assert len(attempts) == len(knowledge.PRIOR) - 4 and attempts[0][1] is True                                         # all of them except the GRE kinds
    assert any(ln.startswith(f"None of the {len(knowledge.PRIOR) - 4} tunnel types connected on this route. Putting the old tcpmux tunnel back") for ln in said)
    assert any("None of the tunnel types connects on this route right now. The repair tries again in 1 minute (then 2, 5 and every 10 minutes; the types that failed are not tried again)." in ln for ln in said)
    assert set(knowledge.blocked(knowledge.pair(ir, fr), int(time.time()))) == {a[0] for a in attempts}                  # each one failed here: none is tried again for two weeks
    assert knowledge.playbook(ir, fr, "route_cut") == {} and knowledge.repeats(ir, fr) == 1                              # the cut is remembered though nothing healed it


def test_a_lossy_tunnel_whose_own_measurement_refuses_is_still_repaired(monkeypatch):
    """Real log, 2026-10-16 (fr-ir1 flapping): "Stopped: no data passes through the tunnel right now" - the live measurement raised RuntimeError and the repair ended there."""
    tid, _, _ = world("fl-ir1")
    said, attempts = fake_repair(monkeypatch, {**GRE_CUT, "cause": "lossy_route", "state": "degraded", "kind": "bp:wssmux", "where": "pair_route"}, works={"chisel:tls"})
    tests = []
    monkeypatch.setattr(tunnel_test, "quality", lambda tid: (_ for _ in ()).throw(RuntimeError("no data passes through the tunnel right now")))
    monkeypatch.setattr(ap, "_wait_quiet", lambda aid, tid: True)
    monkeypatch.setattr(ap, "_recommend_now", lambda aid, iran_id, foreign_id, kinds, patience=1200, first_ok=False: tests.append((list(kinds), first_ok)) or [
        {"kind": "chisel:tls", "ok": False, "score": 39, "latency_ms": 160.0, "loss_pct": 0.0}])
    assert ap.smart_fix(10, tid, "switch", "auto") is True
    assert any("could not be measured" in ln and "no data passes" in ln for ln in said) and tests[-1][1] is False         # (a tunnel that still carries data keeps the test-first rule)
    assert attempts[0][0] == "chisel:tls" and attempts[0][1] is True


def test_the_engine_of_a_cut_tunnel_that_may_be_a_stuck_service_is_restarted_before_a_new_type_is_built(monkeypatch):
    tid, _, _ = world("un-ir1")
    _, attempts = fake_repair(monkeypatch, {"cause": "unknown", "summary": "the services run but no data passes", "kind": "tcpmux", "state": "down", "where": None, "ext_blocked": False}, works={"wssmux"})
    tried = []
    monkeypatch.setattr(actions, "fix", lambda *a, **k: tried.append("restart") or 1)
    monkeypatch.setattr(ap, "_wait", lambda build_aid, limit=900: (False, ["Checked again: still down"]))
    monkeypatch.setattr(ap, "_rebuild_safe", lambda aid, tid, fresh_link=False: tried.append("rebuild") or (False, ["Build failed"]))
    assert ap.smart_fix(11, tid, "switch", "auto") is True
    assert tried == ["restart", "rebuild"] and attempts[0][0] == "wssmux"                                              # restart, rebuild, then whichever type connects (no GRE is ruled out for this cause)


# ------------------------------------------------------------------ a repair that did not hold
def test_the_steps_that_healed_a_tunnel_lately_are_not_repeated_when_it_is_cut_again():
    world("rl-ir1")
    now = int(time.time())
    assert ap.relapsed("rl-ir1", now) == set()

    with session() as db:
        db.add(ActionLog(ref="rl-ir1", action="fix", source="auto", ok=True, finished_at=now - 600, story=["Checked again: the tunnel works."]))
        db.add(RepairCase(tunnel="rl-ir1", ts=now - 300, steps=[{"step": "rebuild", "ok": True, "secs": 90}, {"step": "restart", "ok": False, "secs": 40}], healed=True))
        db.add(ActionLog(ref="rl-old", action="fix", source="auto", ok=True, finished_at=now - 7200, story=[]))

    assert ap.relapsed("rl-ir1", now) == {"restart", "rebuild"} and ap.relapsed("rl-old", now) == set()               # (a step that failed is not one that healed; an hour old is not a relapse)
    assert ap.relapsed("rl-ir1", now + 3600) == set()


def test_the_climb_skips_the_steps_that_did_not_hold(monkeypatch):
    tid, _, _ = world("rl-ir2")
    now = int(time.time())
    said, attempts = fake_repair(monkeypatch, {"cause": "unknown", "summary": "the services run but no data passes", "kind": "tcpmux", "state": "down", "where": None, "ext_blocked": False}, works={"wssmux"})

    with session() as db:
        db.add(ActionLog(ref="rl-ir2", action="fix", source="auto", ok=True, finished_at=now - 120, story=[]))

    tried = []
    monkeypatch.setattr(actions, "fix", lambda *a, **k: tried.append("restart") or 1)
    monkeypatch.setattr(ap, "_rebuild_safe", lambda aid, tid, fresh_link=False: tried.append("rebuild") or (False, ["Build failed"]))
    assert ap.smart_fix(11, tid, "switch", "auto") is True
    assert tried == ["rebuild"] and attempts[0][0] == "wssmux"                  # no restart: it healed this tunnel two minutes ago and the tunnel is cut again; then the first type of the order of a cut tunnel
    assert any("restart healed this tunnel less than 30 minutes ago" in ln for ln in said)


def test_a_repair_has_to_hold_or_the_climb_goes_on(monkeypatch):
    tid, _, _ = world("rl-ir3")
    said = []
    monkeypatch.setattr(ap, "_say", lambda aid, line, done=None, ok=None: said.append(line))
    monkeypatch.setattr(ap.time, "sleep", lambda s: None)
    states = iter(["up", "down"])

    def check(tunnel_id, fresh_ext=False):
        with session() as db:
            db.get(Tunnel, tunnel_id).state = next(states)

    monkeypatch.setattr(ap, "check_tunnel", check)
    assert ap._holds(1, tid) is True
    assert ap._holds(1, tid) is False and said[-1].startswith(f"It was up for a moment and is down again {ap.SOAK} seconds later") and ap.SOAK == 25


def test_a_plain_repair_is_healed_only_after_two_checks_up_in_a_row(monkeypatch):
    tid, _, _ = world("rl-ir4")
    monkeypatch.setattr(actions.time, "sleep", lambda s: None)
    monkeypatch.setattr(actions, "external_probe_one", lambda *a, **k: None)
    rechecked = []
    monkeypatch.setattr(bandwidth, "recheck", lambda tunnel_id: rechecked.append(tunnel_id))             # (a stalled tunnel is measured again before every look)
    plan = actions.Plan([], [], ports=[])
    seq = iter(["up", "down", "up", "up"])                                    # up for a moment, cut again, then really up

    def check(tunnel_id, fresh_ext=False):
        with session() as db:
            db.get(Tunnel, tunnel_id).state = next(seq)

    monkeypatch.setattr(actions, "check_tunnel", check)
    assert actions._recheck(tid, plan, "203.0.113.20", 1, tries=4)[0] == "up" and len(rechecked) == 4
    seq2 = iter(["down", "up", "down", "down", "up"])

    def check2(tunnel_id, fresh_ext=False):
        with session() as db:
            db.get(Tunnel, tunnel_id).state = next(seq2)

    monkeypatch.setattr(actions, "check_tunnel", check2)
    state, why = actions._recheck(tid, plan, "203.0.113.20", 1, tries=4)
    assert state == "degraded" and "not steadily" in why                      # the last check was up, the one before it was not: not healed


# ------------------------------------------------------------------ the plain restart: only where it can help, and quickly
def test_a_route_that_is_silent_and_was_cut_like_this_before_gets_no_plain_restart(monkeypatch):
    tid, ir, fr = world("pr-ir1")
    other, _, _ = world("pr-ir2")
    now = int(time.time())

    with session() as db:
        for n in (tid, other):
            t = db.get(Tunnel, n)
            t.state, t.state_since, t.last_error = "down", now - 400, "no answer from the foreign end over the GRE link"

    started = []
    monkeypatch.setattr(actions, "fix", lambda tunnel_id, source="manual", dry_run=False: started.append(tunnel_id) or 1)
    monkeypatch.setattr(actions, "doctor_mode", lambda: "fix")
    monkeypatch.setattr(actions.get_settings(), "read_only", False)
    knowledge.remember(ir, fr, "gre_blocked", "pr-ir1", [{"step": "switch", "ok": True, "kind": "bp:wssmux"}], True, 90, now - 86400)
    actions.auto_repair()
    assert started == [other]                                                                                          # the first was filtered before: the smart repair builds what connects, no restart is tried
    assert actions.is_route_fault("no answer from the foreign end over the GRE link") and actions.is_route_fault("port 8072 answers on the server but not from ir9: blocked in front of the server")
    assert not actions.is_route_fault("Iran: backhaul-iran-tcpmux-9906.service is inactive") and not actions.is_route_fault("port 8075 accepts connections but no data comes back through the tunnel")


def test_a_cut_tunnel_is_restarted_whole_and_checked_once(monkeypatch):
    from nikatel.services import tunnels as tn

    tid, _, _ = world("pr-ir3")

    def fake_query(node, plan, is_iran):
        units = plan.iran_units if is_iran else plan.foreign_units
        text = "".join(f"UNIT {u} active\n" for u in units)

        if is_iran:                                                  # the GRE link is silent: every ping lost, nothing answers
            text += f"IFACE {plan.iface} UNKNOWN 10 20\nPEER {plan.iran_peer}  100\nLISTEN {plan.ports[0]} 1\nDATA {plan.ports[0]} 000 0\n"

        return tn.parse_side(text + "NOW 1000\n")

    with session() as db:
        t = db.get(Tunnel, tid)
        t.foreign_tunnel_ip, t.record = "10.2.9.2", {}
        t.detail = {"data_seen": True, "data_fails": 9}

    monkeypatch.setattr(actions, "_query", fake_query)
    ran, rechecks = [], []
    monkeypatch.setattr(actions, "run_action", lambda node, name, params=None, timeout=60: ran.append((node.host, params["UNITS"])) or "")
    monkeypatch.setattr(actions, "_recheck", lambda *a, **k: rechecks.append(k.get("tries")) or ("down", "no answer from the foreign end over the GRE link"))
    monkeypatch.setattr(actions.get_settings(), "read_only", False)
    monkeypatch.setattr(actions.ai, "advise", lambda *a, **k: None)
    aid = actions.fix(tid, source="auto")
    end = time.time() + 20

    while time.time() < end and not actions.status(aid)["done"]:
        time.sleep(0.1)

    s = actions.status(aid)
    assert s["done"] and s["ok"] is False and len(ran) == 4 and rechecks == [6]                                          # four restarts, ONE look after them (not a look after each: three minutes for a cut route)
    assert s["story"][-1].startswith("Every step was tried; the tunnel is still not up. The smart repair goes on from here.")
    assert sum(ln.startswith("Checked again") for ln in s["story"]) == 1


# ------------------------------------------------------------------ when the Doctor climbs, and how often it tries a cut tunnel
def test_the_smart_repair_starts_by_rules_and_no_tunnel_waits_more_than_five_minutes_past_its_grace():
    assert ap.escalate_due("down", 30, 60, 0, False, False) is False and ap.escalate_due("down", 70, 60, 0, False, False) is False       # a blip is not a cut; a restart first
    assert ap.escalate_due("down", 70, 60, 1, False, False) is True                                                                     # one plain restart that did not heal it
    assert ap.escalate_due("down", 70, 60, 0, True, True) is True and ap.escalate_due("down", 70, 60, 0, True, False) is False          # a silent route that was cut like this before needs no restart first
    assert ap.escalate_due("down", 70, 60, 0, False, True) is False and ap.escalate_due("down", 61 + 300, 60, 0, False, False) is True  # (and nothing waits for ever for a restart that never came)
    assert ap.escalate_due("degraded", ap.DEGRADED_AFTER - 1, 60, 0, False, False) is False and ap.escalate_due("degraded", ap.DEGRADED_AFTER, 60, 0, False, False) is True and ap.DEGRADED_AFTER == 120      # (was ten minutes)
    assert ap.escalate_due("down", 0, actions.DOWN_GRACE, 1, False, False) is True and ap.escalate_due("down", 0, actions.DOWN_GRACE, 0, False, False) is False         # no waiting time: right after one restart that did not heal it


def test_a_cut_tunnel_climbs_after_one_plain_restart_and_is_tried_again_after_one_two_five_then_ten_minutes(monkeypatch):
    tid, _, _ = world("es-ir1")
    now = int(time.time())

    with session() as db:
        t = db.get(Tunnel, tid)
        t.state, t.state_since, t.last_error = "down", now - 200, "port 8072 accepts connections but no data comes back through the tunnel"

    started = []
    monkeypatch.setattr(ap, "smart_fix_start", lambda *a, **k: started.append(a))
    monkeypatch.setattr(ap.actions, "doctor_mode", lambda: "fix")
    monkeypatch.setattr(ap.get_settings(), "read_only", False)
    monkeypatch.setattr(ap, "outage_state", lambda: {"storm": False})
    ap.auto_escalate()
    assert started == []                                                                                         # not a single plain restart yet: it comes first

    with session() as db:
        db.add(ActionLog(ref="es-ir1", action="fix", source="auto", ok=False, finished_at=now - 100, story=["Every step was tried; the tunnel is still not up."], ts=now - 150))

    ap.auto_escalate()
    assert [a[0] for a in started] == [tid]                                                                      # ONE restart that did not heal it says restarts do not help here

    with session() as db:                                                                                        # the first smart repair of this cut ended 30 s ago
        first = ActionLog(ref="es-ir1", action="autopilot", source="auto", ok=False, finished_at=now - 30, ts=now - 150, story=["x"])
        db.add(first)
        db.flush()
        first_id = first.id

    started.clear()
    ap.auto_escalate()
    assert started == []                                                                                         # 30 s after its end: the first retry waits a minute

    with session() as db:
        db.get(ActionLog, first_id).finished_at = now - 70

    ap.auto_escalate()
    assert [a[0] for a in started] == [tid]                                                                      # 70 s after the end of the first: tried again

    with session() as db:                                                                                        # a second try of this cut ended 100 s ago
        second = ActionLog(ref="es-ir1", action="autopilot", source="auto", ok=False, finished_at=now - 100, ts=now - 190, story=["x"])
        db.add(second)
        db.flush()
        second_id = second.id

    started.clear()
    ap.auto_escalate()
    assert started == []                                                                                         # the second retry waits two minutes

    with session() as db:
        db.get(ActionLog, second_id).finished_at = now - 130

    ap.auto_escalate()
    assert [a[0] for a in started] == [tid]                                                                      # 130 s after the end of the second: tried again


def test_the_diagnosis_reuses_a_fresh_check_and_asks_every_server_at_once(monkeypatch):
    tid, _, _ = world("dg-ir1")
    now = int(time.time())

    with session() as db:
        t = db.get(Tunnel, tid)
        t.last_check_at, t.last_error = now - 5, "no answer from the foreign end over the GRE link"
        t.detail = {"loss_pct": 100.0, "iran_units": {"u": "active"}, "foreign_units": {"u": "active"}, "iran_error": "", "foreign_error": ""}

    checked, asked = [], []
    monkeypatch.setattr(ap, "check_tunnel", lambda tunnel_id, fresh_ext=False: checked.append(tunnel_id))
    monkeypatch.setattr(ap, "_ping_loss", lambda node, host: asked.append("ping") or 0.0)
    monkeypatch.setattr(ap, "_dial", lambda node, host, port: asked.append("dial") or (port == 22))
    monkeypatch.setattr(ap, "_path_scan", lambda tunnel_id: asked.append("scan") or {"where": "none", "text": "no loss found", "rows": []})
    monkeypatch.setattr(ap.ai, "advise", lambda *a, **k: None)
    d = ap.diagnose(tid)
    assert checked == [] and d["cause"] == "gre_blocked" and d["ladder"] == ["switch"] and d["dial_i"] is True and d["dial_f"] is True and d["where"] == "none"        # a check of five seconds ago is not made again
    assert sorted(asked) == ["dial", "dial", "ping", "ping", "scan"]                                                                                               # the pings, the dials and the path scan, all of them

    with session() as db:
        db.get(Tunnel, tid).last_check_at = now - 100

    ap.diagnose(tid)
    assert checked == [tid]                                                                                                                                        # an old one is

def test_the_dial_test_asks_the_other_server_over_tcp(monkeypatch):
    calls = []
    monkeypatch.setattr(ap, "run_script", lambda node, name, params=None, timeout=20: calls.append((name, dict(params))) or ("OPEN\n" if params["PORT"] == "22" else "CLOSED\n"))
    node = ap.Node("203.0.113.1", "root", 22)
    assert ap._dial(node, "198.51.100.5", 22) is True and ap._dial(node, "198.51.100.5", 2222) is False and calls[0] == ("tcp_probe", {"HOST": "198.51.100.5", "PORT": "22"})
    monkeypatch.setattr(ap, "run_script", lambda *a, **k: (_ for _ in ()).throw(ap.SSHError("timeout")))
    assert ap._dial(node, "198.51.100.5", 22) is None
