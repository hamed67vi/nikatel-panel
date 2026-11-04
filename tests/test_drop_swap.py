"""After a drop the Doctor tests the other types of the pair (speed included) and changes the tunnel to one that gives clearly more and was clean (the owner, 2026-10-21: "when we have a drop the Doctor must test
the other types - the problem may not exist on them - and it can change the type; I said the tunnel must be steady")."""
import time

import pytest

from nikatel.config import get_settings
from nikatel.db import session
from nikatel.models import ServerSample, Tunnel, TunnelCheck
from nikatel.services import actions, autopilot, bandwidth, doctor, failover, knowledge
from nikatel.services import settings as st

NOW = 1_800_000_000
HOSTS = iter(range(60, 250))


def _world(monkeypatch, iran_cpu=8.0, mode="fix", memory=True, name="dr-a"):
    n = next(HOSTS)

    with session() as db:
        from nikatel.models import Server

        ir, fr = Server(name=f"ir-dr{n}", host=f"203.0.113.{n}", role="iran", online=True), Server(name=f"fr-dr{n}", host=f"198.51.100.{n}", role="foreign", online=True)
        db.add_all([ir, fr])
        db.flush()
        bw = {"m": bandwidth.METHOD, "how": "link", "down": 14.0, "at": NOW - 600, "ref": 161.0, "state": "drop", "drop": {"since": NOW - 600, "from": 161.0, "now": 14.0}, "suspect": False, "next_at": NOW + 1200, "hist": [[NOW - 5400 * i, 161.0, None] for i in range(6, 0, -1)]}
        t = Tunnel(name=name, type="backhaul", transport="tcpmux", port=9901, iran_server_id=ir.id, foreign_server_id=fr.id, state="up", forwarded_ports=[8076], record={"bw": bw}, secrets={})
        db.add(t)
        db.flush()
        tid, iid, fid = t.id, ir.id, fr.id
        now = int(time.time())
        db.add_all([ServerSample(server_id=iid, ts=now - 20, cpu=iran_cpu, ram=30.0, disk=30.0, load1=0.5, rx_mbps=1.0, tx_mbps=2.0, conn=7), ServerSample(server_id=fid, ts=now - 20, cpu=10.0, ram=30.0, disk=30.0, load1=0.5, rx_mbps=1.0, tx_mbps=2.0, conn=7)])
        db.add_all([TunnelCheck(tunnel_id=tid, ts=now - 60 * i, ok=True, rtt_ms=100.0, rx_mbps=1.0, tx_mbps=1.0, error="") for i in range(6)])
        t = db.get(Tunnel, tid)
        t.rx_mbps, t.rtt_ms, t.detail = 1.0, 100.0, {"loss_pct": 0.0}

    st.put("repair", {"mode": mode})
    now = int(time.time())
    tcp = None

    for i in range(3):                                           # (three tests that passed: steady)
        tcp = knowledge.merge(tcp, {"kind": "tcp", "ok": True, "score": 55, "down_mbps": 118.0, "latency_ms": 100.0, "loss_pct": 0.0}, "full", now - 3600 * (4 - i))

    kinds = {"tcp": tcp, "paqet": knowledge.merge(None, {"kind": "paqet", "ok": True, "score": 50, "down_mbps": 150.0, "latency_ms": 100.0, "loss_pct": 0.0}, "full", now - 3600)}
    monkeypatch.setattr(knowledge, "pair", lambda i, f: {"kinds": kinds} if memory else {})
    monkeypatch.setattr(bandwidth, "DROP_SWAP", True)
    monkeypatch.setattr(get_settings(), "read_only", False)
    return tid


def _spy(monkeypatch, results, restart_brings_back=False):
    said, tested, calls = [], [], []
    monkeypatch.setattr(autopilot, "_say", lambda aid, line, done=None, ok=None: said.append(line))
    monkeypatch.setattr(autopilot, "_recommend_now", lambda aid, i, f, kinds, patience=1200, first_ok=False, measure="latency": tested.append((kinds, measure)) or results)
    monkeypatch.setattr(doctor, "_run_retype", lambda aid, t, kind, was, why, auto=False: (calls.append((t, kind, was, auto, why)), doctor.mark_retyped("dr-a", int(time.time()))))
    monkeypatch.setattr(actions, "restart_now", lambda tid, source="": {"state": "up", "why": ""})
    monkeypatch.setattr(bandwidth, "measure", lambda tid: ({"down": 14.0, "ref": 161.0, "drop": {"from": 161.0}}, ""))
    return said, tested, calls


def _row(kind, down, loss=0.0, ping=100.0):
    return {"kind": kind, "ok": True, "score": 50, "down_mbps": down, "loss_pct": loss, "latency_ms": ping}


def test_the_pick_wants_a_clean_connected_type_that_gives_clearly_more_and_prefers_a_proven_steady_one():
    pick = bandwidth.drop_pick
    rows = [_row("paqet", 200.0), _row("tcp", 90.0)]
    assert pick(rows, {"tcp": 100, "paqet": None}, 14.0, 100.0)["kind"] == "tcp"                      # a type with a known stability goes before one that is not proven, though it is slower
    assert pick(rows, {"tcp": None, "paqet": None}, 14.0, 100.0)["kind"] == "paqet"                   # nothing proven: the faster
    assert pick([_row("tcp", 25.0)], {}, 14.0, 100.0) is None                                          # not twice what it gives now (28)
    assert pick([_row("tcp", 19.0)], {}, 2.0, 100.0) is None                                           # not even the lowest line (20 Mb/s)
    assert pick([_row("tcp", 90.0, loss=3.0)], {}, 14.0, 100.0) is None                                # lost packets in its test: not steady
    assert pick([_row("tcp", 90.0, ping=400.0)], {}, 14.0, 100.0) is None                              # answers far slower than the running type
    assert pick([_row("tcp", 90.0, ping=400.0)], {}, 14.0, None)["kind"] == "tcp"                      # (no ping known for the running one: not asked)
    assert pick([{"kind": "tcp", "ok": False, "score": None, "down_mbps": None}], {}, 14.0, 100.0) is None
    assert pick([_row("tcp", 90.0)], {"tcp": 40}, 14.0, 100.0) is None                                 # a type the memory knows to be UNSTEADY is never picked, however fast its test was


def test_a_drop_that_the_restart_does_not_cure_tests_the_steady_types_with_speed_and_changes_to_the_best(monkeypatch):
    tid = _world(monkeypatch)
    said, tested, calls = _spy(monkeypatch, [_row("tcp", 95.0), _row("paqet", 200.0)])
    bandwidth.investigate(7, tid)
    assert tested == [(["tcp", "paqet"], "full")]                                                      # the speed is measured (not delay and loss only); the steady type is tried first
    assert calls and calls[0][:4] == (tid, "tcp", "tcpmux", True) and "dropped to 14 Mb/s (usually 161)" in calls[0][4] and "tcp gave 95 Mb/s" in calls[0][4] and "stability in the Doctor's memory" in calls[0][4]
    assert any("The Doctor tests tcp, paqet between ir-dr" in s for s in said) and any(s == "The Doctor changes the type by itself (fix mode): tcpmux → tcp." for s in said)
    said.clear()
    bandwidth.investigate(8, tid)                                                                      # once in 24 hours
    assert len(calls) == 1 and tested.__len__() == 1 and any("The Doctor does not change the type now: dr-a was changed by the Doctor" in s for s in said)


def test_nothing_is_changed_when_no_tested_type_is_clearly_better_or_the_doctor_is_off_or_the_cause_is_outside(monkeypatch):
    tid = _world(monkeypatch)
    said, tested, calls = _spy(monkeypatch, [_row("tcp", 22.0), _row("paqet", 90.0, loss=4.0)])
    bandwidth.investigate(7, tid)
    assert tested and not calls and any("No tested type gives clearly more than 14 Mb/s with a clean line and a steady record: dr-a stays tcpmux" in s for s in said)

    tid2 = _world(monkeypatch, mode="off", name="dr-b")
    said, tested, calls = _spy(monkeypatch, [_row("tcp", 95.0)])
    bandwidth.investigate(7, tid2)
    assert not tested and not calls and any("does not test other types" in s or "it is told, not repaired" in s for s in said)

    tid3 = _world(monkeypatch, iran_cpu=96.0, name="dr-c")                                                          # a busy Iran server: another type of this tunnel does not cure it
    said, tested, calls = _spy(monkeypatch, [_row("tcp", 95.0)])
    bandwidth.investigate(7, tid3)
    assert not tested and not calls


def test_a_type_that_is_not_proven_steady_is_never_taken_by_itself_and_never_suggested(monkeypatch):
    tid = _world(monkeypatch)
    said, tested, calls = _spy(monkeypatch, [_row("tcp", 10.0), _row("paqet", 200.0)])                 # (tcp, the proven one, gave little now; paqet has one test only)
    bandwidth.investigate(7, tid)
    assert tested and not calls
    assert doctor.state()["suggestions"] == []                                                                  # (no suggestion any more, 1.69.83)
    assert any("paqet gave 200 Mb/s in the test, but its steadiness is not proven yet" in s and "dr-a stays tcpmux" in s for s in said)


def test_with_no_memory_of_the_pair_the_doctors_own_order_is_tested(monkeypatch):
    tid = _world(monkeypatch, memory=False)
    monkeypatch.setattr(knowledge, "plan_for", lambda i, f, cur, a, b, now=None: (["tcpmux", "tcp", "bp:wss", "paqet", "chisel"], []))
    _said, tested, calls = _spy(monkeypatch, [_row("bp:wss", 80.0)])
    bandwidth.investigate(7, tid)
    assert tested == [(["tcp", "bp:wss", "paqet"], "full")] and not calls                                # (at most three; the running type is not tried; nothing is known of it: a suggestion only)
    assert doctor.state()["suggestions"] == []


def test_the_slots_of_a_drop_test_go_to_types_a_server_can_run():
    slots = bandwidth.drop_slots
    assert slots(["wg:tcpmux", "wg:tcp", "tcp"], {"wg:tcpmux", "wg:tcp"}, ["bp:wss", "paqet"], 3) == (["tcp", "bp:wss", "paqet"], ["wg:tcpmux", "wg:tcp"])
    assert slots(["tcp", "paqet", "bp:wss"], set(), ["chisel"], 3) == (["tcp", "paqet", "bp:wss"], [])                       # nothing to leave out: nothing changes
    assert slots(["wg:tcp", "tcp"], {"wg:tcp"}, [], 3) == (["tcp"], ["wg:tcp"])                                                # nothing else to try: fewer than three, never a type that cannot run
    assert slots(["tcp", "paqet", "bp:wss", "wg:tcp"], {"wg:tcp"}, [], 3) == (["tcp", "paqet", "bp:wss"], [])                  # the one that cannot run is beyond the three: it does not matter
    assert slots(["wg:tcp", "tcp", "paqet", "bp:wss"], {"wg:tcp"}, ["bp:wss", "chisel"], 3) == (["tcp", "paqet", "bp:wss"], ["wg:tcp"])   # (the next type comes from the list first, the extra ones after; nothing twice)


def test_a_type_a_server_cannot_run_does_not_take_a_slot_of_the_drop_test(monkeypatch):
    now = int(time.time())
    skip = lambda kind, ago: knowledge.merge(None, {"kind": kind, "ok": False, "skipped": "tools", "error": "not tested: wireguard-tools is not installed"}, "full", now - ago)
    tid = _world(monkeypatch, memory=False)
    monkeypatch.setattr(knowledge, "pair", lambda i, f: {"kinds": {"wg:tcpmux": skip("wg:tcpmux", 600), "wg:tcp": skip("wg:tcp", 600)}})
    monkeypatch.setattr(knowledge, "plan_for", lambda i, f, cur, a, b, now=None: (["wg:tcpmux", "wg:tcp", "tcp", "bp:wss", "paqet", "chisel"], []))
    said, tested, calls = _spy(monkeypatch, [_row("bp:wss", 80.0)])
    bandwidth.investigate(7, tid)
    assert tested == [(["tcp", "bp:wss", "paqet"], "full")] and not calls                                                       # the two slots a WireGuard type would have wasted went to the next types
    assert any(s.startswith("Not tried: wg:tcpmux, wg:tcp - a server lacks what they need") for s in said)


def test_a_type_that_could_not_be_tried_a_day_ago_is_asked_again(monkeypatch):
    now = int(time.time())
    skip = lambda kind, ago: knowledge.merge(None, {"kind": kind, "ok": False, "skipped": "tools", "error": "not tested: wireguard-tools is not installed"}, "full", now - ago)
    tid = _world(monkeypatch, memory=False)                                                                                     # (the owner may have installed the tools since)
    monkeypatch.setattr(knowledge, "pair", lambda i, f: {"kinds": {"wg:tcpmux": skip("wg:tcpmux", 2 * 86400), "wg:tcp": skip("wg:tcp", 2 * 86400)}})
    monkeypatch.setattr(knowledge, "plan_for", lambda i, f, cur, a, b, now=None: (["wg:tcpmux", "wg:tcp", "tcp", "bp:wss", "paqet", "chisel"], []))
    said, tested, _calls = _spy(monkeypatch, [_row("bp:wss", 80.0)])
    bandwidth.investigate(7, tid)
    assert tested == [(["wg:tcpmux", "wg:tcp", "tcp"], "full")] and not any(s.startswith("Not tried") for s in said)


def _poor(kind, score, down, ago=3600):
    """A type that carried data in a test but scored under the line (the kind of memory a pair with a throttled carrier ends with)."""
    return knowledge.merge(None, {"kind": kind, "ok": False, "score": score, "down_mbps": down, "latency_ms": 100.0, "loss_pct": 0.0}, "full", int(time.time()) - ago)


def test_a_type_that_carried_data_before_is_tried_before_the_types_never_tried(monkeypatch):
    tid = _world(monkeypatch, memory=False)
    swingy = None

    for ago, down in ((9, 500.0), (6, 20.0), (3, 400.0)):                                                                   # three tests with a huge swing: the type is known to be UNSTEADY
        swingy = knowledge.merge(swingy, {"kind": "bp:kcp", "ok": False, "score": 40, "down_mbps": down, "latency_ms": 100.0, "loss_pct": 0.0}, "full", int(time.time()) - ago * 3600)

    monkeypatch.setattr(knowledge, "pair", lambda i, f: {"kinds": {"bp:ws": _poor("bp:ws", 42, 300.0), "bp:tcp": _poor("bp:tcp", 38, 200.0), "paqet": _poor("paqet", 10, 20.0), "bp:kcp": swingy}})
    monkeypatch.setattr(knowledge, "plan_for", lambda i, f, cur, a, b, now=None: (["l2tpv3:tcpmux", "gretap:tcpmux", "tcp"], []))
    _said, tested, calls = _spy(monkeypatch, [_row("bp:ws", 300.0)])
    bandwidth.investigate(7, tid)
    assert len(tested) == 1 and tested[0][1] == "full" and set(tested[0][0]) == {"bp:ws", "bp:tcp", "paqet"}                  # what carried data goes first; the unsteady one and the kernel links that never carried data are not tried
    assert not calls                                                                                                        # (a type that is not proven steady is still never taken by itself)


def test_a_steady_type_is_still_tried_first_and_the_known_poor_ones_only_fill_the_empty_slots(monkeypatch):
    tid = _world(monkeypatch)                                                                                               # the memory: tcp (steady) and paqet (one good test) - two types
    now = int(time.time())
    kinds = {"tcp": None, "paqet": knowledge.merge(None, {"kind": "paqet", "ok": True, "score": 50, "down_mbps": 150.0, "latency_ms": 100.0, "loss_pct": 0.0}, "full", now - 3600), "bp:ws": _poor("bp:ws", 42, 300.0)}

    for i in range(3):
        kinds["tcp"] = knowledge.merge(kinds["tcp"], {"kind": "tcp", "ok": True, "score": 55, "down_mbps": 118.0, "latency_ms": 100.0, "loss_pct": 0.0}, "full", now - 3600 * (4 - i))

    monkeypatch.setattr(knowledge, "pair", lambda i, f: {"kinds": kinds})
    _said, tested, _calls = _spy(monkeypatch, [_row("tcp", 95.0), _row("paqet", 200.0), _row("bp:ws", 300.0)])
    bandwidth.investigate(7, tid)
    assert tested == [(["tcp", "paqet", "bp:ws"], "full")]


def test_a_change_made_by_hand_to_a_dropped_tunnel_is_kept_in_the_doctors_memory(monkeypatch):
    tid = _world(monkeypatch)
    snap = autopilot._snapshot(tid)                                                                                         # (the tunnel of this world has dropped: its record says so)
    assert snap["record"]["bw"]["state"] == "drop"
    autopilot.taught(snap, "bp:ws", int(time.time()) - 75)
    pb = knowledge.playbook(snap["iran_id"], snap["foreign_id"], "slow")
    assert pb["kind"] == "bp:ws" and pb["step"] == "swap" and pb["n"] == 1 and 70 <= pb["secs"] <= 90                       # the next drop of this pair tests bp:ws first

    healthy = dict(snap, record={"bw": {"state": "ok", "down": 500.0}})                                                      # a healthy tunnel changed by choice: nothing is kept
    autopilot.taught(healthy, "tcp", int(time.time()))
    assert knowledge.playbook(snap["iran_id"], snap["foreign_id"], "slow")["kind"] == "bp:ws"
    assert len(knowledge._doc()["incidents"]) == 1


def test_the_change_started_by_hand_teaches_only_when_it_worked(monkeypatch):
    tid = _world(monkeypatch)
    snap = autopilot._snapshot(tid)
    said = []
    monkeypatch.setattr(autopilot, "_start", lambda label, fn, *a, **k: fn(1, *a))                                          # (the thread of the action runs here, at once)
    monkeypatch.setattr(autopilot, "_say", lambda aid, line, done=None, ok=None: said.append(line))
    monkeypatch.setattr(autopilot, "servers_off_of", lambda i, f: None)
    monkeypatch.setattr(autopilot, "_replace", lambda *a, **k: False)
    autopilot.replace_start(tid, "bp:ws")
    assert said[-1] == "Not changed." and knowledge.playbook(snap["iran_id"], snap["foreign_id"], "slow") == {}               # it failed: nothing to learn
    monkeypatch.setattr(autopilot, "_replace", lambda *a, **k: True)
    autopilot.replace_start(tid, "bp:ws")
    assert said[-1] == "Done." and knowledge.playbook(snap["iran_id"], snap["foreign_id"], "slow")["kind"] == "bp:ws"


def _dropped_for(tid, ago):
    """The tunnel has been dropped for `ago` seconds."""
    with session() as db:
        t = db.get(Tunnel, tid)
        bw = dict(t.record["bw"])
        bw["drop"] = {"since": int(time.time()) - ago, "from": 161.0, "now": 14.0}
        t.record = {**t.record, "bw": bw}


def _quality_calls(monkeypatch):
    calls = []
    monkeypatch.setattr(failover, "quality_move", lambda aid, tid, reason, now_mbps, rtt, say: calls.append((tid, reason, now_mbps, rtt)) or False)
    return calls


def test_a_tunnel_that_stayed_dropped_and_no_type_cures_is_handed_to_the_move_for_quality(monkeypatch):      # (1.69.186; the owner, 2026-11-02: "I gave the Doctor permission to change the IP as well")
    tid = _world(monkeypatch)
    _dropped_for(tid, 2 * 3600)
    calls = _quality_calls(monkeypatch)
    _said, tested, _calls = _spy(monkeypatch, [_row("tcp", 22.0), _row("paqet", 90.0, loss=4.0)])              # (no type is clearly better)
    bandwidth.investigate(7, tid)
    assert tested and calls == [(tid, "quality", 14.0, 100.0)]


def test_a_drop_of_the_whole_iran_server_is_handed_to_the_move_for_quality_without_testing_types(monkeypatch):      # (the owner, 2026-11-02, on a tunnel that dropped with its server's other tunnel: "why does the Doctor do nothing?")
    tid = _world(monkeypatch)
    _dropped_for(tid, 2 * 3600)
    now = int(time.time())

    with session() as db:
        t = db.get(Tunnel, tid)
        bw = {"m": bandwidth.METHOD, "how": "link", "down": 10.0, "at": now - 60, "ref": 100.0, "state": "drop", "drop": {"since": now - 7200, "from": 100.0, "now": 10.0}, "suspect": False, "next_at": now + 1200, "hist": []}
        db.add(Tunnel(name="dr-x", type="backhaul", transport="tcp", port=9902, iran_server_id=t.iran_server_id, foreign_server_id=t.foreign_server_id, state="up", forwarded_ports=[8077], record={"bw": bw}, secrets={}))

    calls = _quality_calls(monkeypatch)
    said, tested, _swaps = _spy(monkeypatch, [_row("tcp", 95.0)])
    bandwidth.investigate(7, tid)
    assert any("tunnels of the Iran server" in s and "dropped together" in s for s in said)                              # (the explanation: its own line or provider)
    assert calls == [(tid, "quality", 14.0, 100.0)] and not tested                                                       # no type of this server cures it: no types are tested, the move is looked at


def test_the_look_at_a_drop_of_the_iran_server_itself_comes_every_twenty_minutes_and_the_ninety_minute_rule_is_gone():      # (1.69.208, the owner, 2026-11-04)
    assert not hasattr(failover, "QUALITY_DROP_AGE") and not hasattr(bandwidth, "QUALITY_LOOK_AFTER")
    bw = {"state": "drop", "drop": {"since": NOW}, "why": {"code": "iran_wide"}}
    assert bandwidth.relook_due(bw, 0, NOW + 10 * 60) is False                                                           # (the first look has just been)
    assert bandwidth.relook_due(bw, 0, NOW + 21 * 60) is True                                                            # no type cures it: every look is a chance to move the tunnel
    assert bandwidth.relook_due(bw, NOW + 22 * 60, NOW + 30 * 60) is False                                               # (looked at just then)
    assert bandwidth.relook_due(bw, NOW + 22 * 60, NOW + 43 * 60) is True
    other = {"state": "drop", "drop": {"since": NOW}, "why": {"code": "this_only"}}
    assert bandwidth.relook_due(other, 0, NOW + 91 * 60) is False and bandwidth.relook_due(other, 0, NOW + 3 * 3600 + 60) is True      # any other cause keeps the three hours


def test_a_short_drop_is_handed_to_the_move_too_there_is_no_waiting(monkeypatch):      # (1.69.208, the owner, 2026-11-04: "remove the 90-minute rule - a config must not stay down")
    tid = _world(monkeypatch)
    _dropped_for(tid, 10 * 60)
    calls = _quality_calls(monkeypatch)
    _said, tested, _calls = _spy(monkeypatch, [_row("tcp", 22.0)])
    bandwidth.investigate(7, tid)
    assert tested and calls == [(tid, "quality", 14.0, 100.0)]


def test_a_cure_by_type_needs_no_move(monkeypatch):
    tid = _world(monkeypatch)
    _dropped_for(tid, 3 * 3600)
    calls = _quality_calls(monkeypatch)
    _said, _tested, swaps = _spy(monkeypatch, [_row("tcp", 95.0)])                                              # a proven steady type gives much more: the type is changed, nothing moves
    bandwidth.investigate(7, tid)
    assert swaps and calls == []


@pytest.mark.parametrize("code_case", ["a"])
def test_the_swap_is_off_in_the_other_tests(code_case):
    assert bandwidth.DROP_SWAP is False and code_case

def test_tunnels_of_one_carrier_dropping_together_are_a_type_problem_not_a_tunnel_problem():
    base = {"now": 14.0, "ref": 161.0, "loss": 0.0, "live": 1.0, "iran": {"name": "ir1", "cpu": 8}, "foreign": {"name": "fr1", "cpu": 10}, "rtt": 100.0, "rtt_ref": 100.0, "same_iran": {"total": 5, "dropped": 1}, "same_foreign": {"total": 1, "dropped": 1}}
    fam = {"family": "wssmux", "total": 3, "dropped": 3, "others_total": 6, "others_dropped": 0}
    code, text = bandwidth.explain({**base, "same_kind": fam})
    assert code == "kind_wide" and "3 of the 3 tunnels that run on wssmux dropped together while the tunnels of the other types are fine" in text and "another type does" in text
    assert bandwidth.explain({**base, "same_kind": {**fam, "others_dropped": 5}})[0] == "this_only"          # the others drop too: not the type
    live = {**base, "same_iran": {"total": 3, "dropped": 2}, "same_foreign": {"total": 1, "dropped": 1}}                  # (the live panel, 2026-10-21: 2 of the 3 tunnels of an Iran server drop - both on wssmux, the third, another type, is fine)
    assert bandwidth.explain({**live, "same_kind": {"family": "wssmux", "total": 8, "dropped": 5, "others_total": 11, "others_dropped": 2}})[0] == "kind_wide"          # not "the Iran server's own line"
    assert bandwidth.explain({**live, "same_kind": {"family": "tcpmux", "total": 3, "dropped": 2, "others_total": 3, "others_dropped": 3}})[0] == "iran_wide"      # the whole server drops, every type: its line
    assert bandwidth.explain({**base, "same_kind": {**fam, "total": 1, "dropped": 1}})[0] == "this_only"          # one tunnel of the type: it is this tunnel
    assert bandwidth.explain({**base, "iran": {"name": "ir1", "cpu": 95}, "same_kind": fam})[0] == "iran_busy"        # the cheaper explanations come first


def test_a_disturbed_carrier_is_not_restarted_and_its_other_types_are_not_tried(monkeypatch):
    tid = _world(monkeypatch)

    with session() as db:
        from nikatel.models import Server

        for i, kind in enumerate(("backhaul", "backpack")):                                                   # two more tunnels on the same carrier, dropped too
            n = next(HOSTS)
            ir, fr = Server(name=f"ir-x{i}", host=f"203.0.113.{n}", role="iran", online=True), Server(name=f"fr-x{i}", host=f"198.51.100.{n}", role="foreign", online=True)
            db.add_all([ir, fr])
            db.flush()
            db.add(Tunnel(name=f"dr-x{i}", type=kind, transport="tcpmux", port=9901, iran_server_id=ir.id, foreign_server_id=fr.id, state="up", forwarded_ports=[8076], record={"bw": {"state": "drop"}}, secrets={}))

    said, tested, _calls = _spy(monkeypatch, [_row("bp:wssmux", 95.0), _row("paqet", 90.0)])
    restarted = []
    monkeypatch.setattr(actions, "restart_now", lambda tid, source="": restarted.append(tid) or {"state": "up"})
    f = bandwidth._facts(tid)
    assert f["same_kind"] == {"family": "tcpmux", "total": 3, "dropped": 3, "others_total": 0, "others_dropped": 0}
    bandwidth.investigate(7, tid)
    assert not restarted and any("tunnels that run on tcpmux dropped together" in s for s in said)           # (no restart of the engine: it is the type)
    assert tested and all(not k.endswith("tcpmux") and k != "tcpmux" for k in tested[0][0])                    # no type of the disturbed carrier is tried


def test_what_a_swap_taught_is_kept_and_the_next_drop_of_the_pair_tests_that_type_first(monkeypatch):
    """The owner, 2026-10-23: "when it has tested the types, the problem and the type that fixed it must go into its memory"."""
    from nikatel.services import swap

    tid = _world(monkeypatch, name="dr-p")

    with session() as db:
        t = db.get(Tunnel, tid)
        ir, fr = t.iran_server_id, t.foreign_server_id
        snap = {"name": "dr-p", "iran_id": ir, "foreign_id": fr}

    swap._remember(snap, "paqet", False)                                                     # a type that did not carry well is kept as an attempt, never as a fix
    assert knowledge.playbook(ir, fr, "slow") == {}
    swap._remember(snap, "paqet", True)
    pb = knowledge.playbook(ir, fr, "slow")
    assert pb["kind"] == "paqet" and pb["n"] == 1 and pb["step"] == "swap"

    said, tested, calls = _spy(monkeypatch, [_row("tcp", 95.0), _row("paqet", 200.0)])
    bandwidth.investigate(7, tid)
    assert tested == [(["paqet", "tcp"], "full")]                                            # the type that fixed it last time first (before: tcp, paqet)
    assert any("fixed it - it is tested first" in s and "paqet" in s for s in said)
    assert calls and calls[0][1] == "tcp"                                                    # (tested first is not taken first: paqet has one test only - not proven steady - so the proven tcp is the one the Doctor changes to)
