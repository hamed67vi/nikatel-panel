"""After a drop the Doctor tests the other types of the pair (speed included) and changes the tunnel to one that gives clearly more and was clean (the owner, 2026-10-21: "when we have a drop the Doctor must test
the other types - the problem may not exist on them - and it can change the type; I said the tunnel must be steady")."""
import time

import pytest

from nikatel.config import get_settings
from nikatel.db import session
from nikatel.models import ServerSample, Tunnel, TunnelCheck
from nikatel.services import actions, autopilot, bandwidth, doctor, knowledge
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


def test_a_type_that_is_not_proven_steady_is_only_suggested_never_taken_by_itself(monkeypatch):
    tid = _world(monkeypatch)
    said, tested, calls = _spy(monkeypatch, [_row("tcp", 10.0), _row("paqet", 200.0)])                 # (tcp, the proven one, gave little now; paqet has one test only)
    bandwidth.investigate(7, tid)
    assert tested and not calls
    sug = doctor.state()["suggestions"]
    assert len(sug) == 1 and sug[0]["kind"] == "type" and sug[0]["to"] == "paqet" and sug[0]["from"] == "tcpmux"
    assert any("It is not changed by itself: its steadiness is not proven yet" in s for s in said)


def test_with_no_memory_of_the_pair_the_doctors_own_order_is_tested(monkeypatch):
    tid = _world(monkeypatch, memory=False)
    monkeypatch.setattr(knowledge, "plan_for", lambda i, f, cur, a, b, now=None: (["tcpmux", "tcp", "bp:wss", "paqet", "chisel"], []))
    _said, tested, calls = _spy(monkeypatch, [_row("bp:wss", 80.0)])
    bandwidth.investigate(7, tid)
    assert tested == [(["tcp", "bp:wss", "paqet"], "full")] and not calls                                # (at most three; the running type is not tried; nothing is known of it: a suggestion only)
    assert doctor.state()["suggestions"][-1]["to"] == "bp:wss"


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