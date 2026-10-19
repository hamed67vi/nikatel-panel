"""What the Doctor KNOWS about every pair of servers (services/knowledge.py): which tunnel types connect between an Iran and a foreign server and how well - kept from every pair test the panel
runs and from the quality test of the live tunnels, used to order what the Doctor tries when a tunnel is cut, and refreshed by a night survey that is off until the owner switches it on."""
import time

import pytest
from sqlalchemy import select

from nikatel.config import get_settings
from nikatel.db import session
from nikatel.models import ActionLog, Event, Server, ServerSample, Tunnel
from nikatel.services import autopilot, knowledge, tunnel_test
from nikatel.services import settings as st

NOW = 1_800_000_000
DAY = 86400
HOSTS = iter(range(10, 250))


def entry(kind, ok, score=None, measure="full", at=NOW, error="", **nums):
    return knowledge.merge(None, {"kind": kind, "ok": ok, "score": score, "error": error, **nums}, measure, at)


# ------------------------------------------------------------------ the pure helpers
def test_a_test_is_kept_as_what_connected_what_was_poor_and_what_failed():
    good = entry("bpd:pck", True, 71, down_mbps=665.0, latency_ms=78.0, loss_pct=0.0)
    poor = entry("bp:wssmux", False, 42, "latency", latency_ms=150.0, loss_pct=0.0)                          # built and measured, under the line: it CONNECTS
    failed = entry("bpd:udp", False, None, "latency", error="handshake did not complete")
    assert [knowledge.verdict(e) for e in (good, poor, failed, None, {})] == ["good", "poor", "failed", "", ""]
    assert good["full"]["down_mbps"] == 665.0 and "lat" not in good and poor["lat"]["score"] == 42 and "full" not in poor and failed["last"]["error"] == "handshake did not complete"
    again = knowledge.merge(good, {"kind": "bpd:pck", "ok": False, "error": "no data passes"}, "latency", NOW + 60)       # it stopped working: the last test says so, the old full numbers stay as history
    assert knowledge.verdict(again) == "failed" and again["full"]["score"] == 71 and again["last"]["at"] == NOW + 60


def test_the_kinds_are_ranked_by_the_full_measurement_first_and_blocked_poor_and_due_are_told():
    pair = {"kinds": {"bpd:pck": entry("bpd:pck", True, 71, "full", NOW - DAY), "paqet": entry("paqet", True, 58, "full", NOW - DAY), "chisel:fwd": entry("chisel:fwd", True, 80, "latency", NOW - DAY, latency_ms=100.0),
                      "bp:wssmux": entry("bp:wssmux", False, 42, "latency", NOW - DAY), "bpd:udp": entry("bpd:udp", False, None, "latency", NOW - DAY), "tcpmux": entry("tcpmux", True, 60, "full", NOW - 40 * DAY)}}
    got = knowledge.ranked(pair, NOW)
    assert [r["kind"] for r in got] == ["bpd:pck", "paqet", "chisel:fwd", "tcpmux"] and got[2]["measure"] == "latency" and got[3]["measure"] == "latency"      # a full measurement of this month first; the latency-only (and the old full one) after it, each by score
    assert [r["kind"] for r in knowledge.ranked(pair, NOW, include_poor=True)][-1] == "bp:wssmux" and knowledge.blocked(pair, NOW) == ["bpd:udp"] and knowledge.poor(pair, NOW) == ["bp:wssmux"]
    assert knowledge.blocked(pair, NOW + 15 * DAY) == [] and knowledge.poor(pair, NOW + 31 * DAY) == []             # a failure is forgotten after two weeks (it is tried again), a poor result after a month
    due = knowledge.due_kinds(pair, ["bpd:pck", "paqet", "bpd:udp", "chisel", "tcpmux"], NOW)
    assert due == ["chisel", "tcpmux"]                                                                              # never tested; tested more than a week ago
    assert knowledge.due_kinds(pair, ["bpd:udp"], NOW + 15 * DAY) == ["bpd:udp"] and knowledge.due_kinds(pair, ["bpd:pck"], NOW + 5 * DAY) == []        # a failed kind after two weeks, a good one after one


def test_a_faster_type_is_one_the_memory_saw_give_five_times_what_the_slow_tunnel_gives():
    """The owner, 2026-10-19: a tunnel that tested 0.8 Mb/s on a pair where the memory had `tcp` at 119 Mb/s."""
    pair = {"kinds": {"tcp": entry("tcp", True, 50, "full", NOW - 3600, down_mbps=119.0, latency_ms=167.0, loss_pct=0.0), "paqet": entry("paqet", True, 40, "full", NOW - 3600, down_mbps=9.0),
                      "bpd:pck": entry("bpd:pck", True, 71, "full", NOW - 3600, down_mbps=300.0), "chisel:fwd": entry("chisel:fwd", True, 80, "latency", NOW - 3600, latency_ms=100.0),        # (a latency-only entry has no speed)
                      "bp:wss": entry("bp:wss", False, 42, "latency", NOW - 3600, down_mbps=900.0), "bpd:udp": entry("bpd:udp", False, None, "latency", NOW - 3600, error="no data"),               # (poor / failed: never offered)
                      "wssmux": entry("wssmux", True, 60, "full", NOW - 20 * DAY, down_mbps=500.0)}}                                                                                          # (twenty days old: only a hint)
    got = knowledge.faster_kinds(pair, NOW, "tcpmux", 4.0)
    assert [r["kind"] for r in got] == ["bpd:pck", "tcp"] and got[0]["down_mbps"] == 300.0                      # the fastest first; 20 Mb/s and 5 x 4 are the line: paqet (9) is under it
    assert [r["kind"] for r in knowledge.faster_kinds(pair, NOW, "tcpmux", 0.8)] == ["bpd:pck", "tcp"]            # a baseline under 1 counts as 1: the line is FASTER_MIN_MBPS, never "any speed"
    assert [r["kind"] for r in knowledge.faster_kinds(pair, NOW, "tcpmux", 20.0)] == ["bpd:pck", "tcp"]           # 5 x 20 = 100: tcp (119) is over it ...
    assert [r["kind"] for r in knowledge.faster_kinds(pair, NOW, "tcpmux", 25.0)] == ["bpd:pck"]                  # ... 5 x 25 = 125: tcp is not five times what the tunnel gives, only the 300 is
    assert knowledge.faster_kinds(pair, NOW, "tcpmux", 61.0) == [] and knowledge.faster_kinds(None, NOW, "tcpmux", 1.0) == [] and knowledge.faster_kinds({}, NOW, "tcpmux", 1.0) == []
    assert [r["kind"] for r in knowledge.faster_kinds(pair, NOW, "bpd:pck", 4.0)] == ["tcp"]                      # the running type is never offered to itself
    assert [r["kind"] for r in knowledge.faster_kinds(pair, NOW, "tcpmux", 4.0, allowed=lambda k: k != "bpd:pck")] == ["tcp"]          # the licence
    assert [r["kind"] for r in knowledge.faster_kinds(pair, NOW + 15 * DAY, "tcpmux", 4.0)] == []                 # two weeks later every number is old


def test_the_night_window_may_wrap_past_midnight_and_equal_hours_mean_any_time():
    tz = 210                                                           # Tehran: UTC+3:30

    def at(h, m=0):                                                    # a UTC time that is h:m in Tehran
        return NOW - NOW % DAY + (h * 60 + m - tz) * 60

    assert knowledge.in_window(at(3), tz, 2, 6) and knowledge.in_window(at(2), tz, 2, 6) and not knowledge.in_window(at(6), tz, 2, 6) and not knowledge.in_window(at(14), tz, 2, 6)
    assert knowledge.in_window(at(23, 30), tz, 22, 4) and knowledge.in_window(at(1), tz, 22, 4) and not knowledge.in_window(at(5), tz, 22, 4)
    assert knowledge.in_window(at(12), tz, 0, 0)


# ------------------------------------------------------------------ what is kept in the panel
def two_servers(name="a"):
    n = next(HOSTS)

    with session() as db:
        ir, fr = Server(name="ir-" + name, host=f"203.0.113.{n}", role="iran", online=True), Server(name="fr-" + name, host=f"198.51.100.{n}", role="foreign", online=True)
        db.add_all([ir, fr])
        db.flush()
        return ir.id, fr.id


def test_every_result_is_kept_per_pair_and_the_page_state_shows_it(client):
    ir, fr = two_servers("k1")

    with session() as db:
        db.add(Tunnel(name="k1-t", type="backhaul", transport="tcpmux", port=9901, iran_server_id=ir, foreign_server_id=fr, state="up", forwarded_ports=[8070]))

    knowledge.record(ir, fr, {"kind": "bpd:pck", "ok": True, "score": 71, "latency_ms": 78.0, "loss_pct": 0.0, "down_mbps": 665.0}, "full")
    knowledge.record(ir, fr, {"kind": "bpd:udp", "ok": False, "error": "handshake did not complete"}, "latency")
    knowledge.record(ir, fr, {"kind": "not-a-kind", "ok": True, "score": 99}, "full")                                 # (a name the tunnel test does not know is not kept)
    s = knowledge.state()
    row = next(p for p in s["pairs"] if p["iran_id"] == ir)
    assert row["foreign_id"] == fr and row["tunnels"] == 1 and row["tested"] == 2 and row["best"][0]["kind"] == "bpd:pck" and row["best"][0]["down_mbps"] == 665.0
    assert [b["kind"] for b in row["blocked"]] == ["bpd:udp"] and row["blocked"][0]["error"] == "handshake did not complete" and row["due"] == len(knowledge.READY) - 2
    assert s["on"] is True and (s["from_hour"], s["to_hour"]) == (2, 6) and s["running"] is None and s["kinds"] == len(knowledge.PRIOR) and s["ready"] == knowledge.READY and row["cuts"] == 0        # the night survey is on: nights only, quiet servers only
    j = client.get("/api/doctor/knowledge").json()
    assert j["ok"] and any(p["iran_id"] == ir for p in j["pairs"])


def test_the_quality_test_of_a_live_tunnel_with_a_door_is_a_free_full_measurement(monkeypatch):
    ir, fr = two_servers("k2")

    with session() as db:
        live = Tunnel(name="k2-live", type="paqet", transport="kcp", port=9902, iran_server_id=ir, foreign_server_id=fr, state="up", forwarded_ports=[8071], record={})
        temp = Tunnel(name="test-9", type="paqet", transport="kcp", port=9903, iran_server_id=ir, foreign_server_id=fr, state="up", forwarded_ports=[39400], record={})
        db.add_all([live, temp])
        db.flush()
        ids = (live.id, temp.id)

    knowledge.note_quality(ids[0], {"score": 62, "latency_ms": 90.0, "jitter_ms": 4.0, "loss_pct": 0.0, "down_mbps": 330.0, "up_mbps": 20.0})
    knowledge.note_quality(ids[1], {"score": 99, "latency_ms": 10.0})                                                # a temporary test tunnel: its result already came through the pair test
    knowledge.note_quality(ids[0], {"error": "no data passes"})
    p = knowledge.pair(ir, fr)
    assert list(p["kinds"]) == ["paqet"] and p["kinds"]["paqet"]["full"]["down_mbps"] == 330.0 and knowledge.verdict(p["kinds"]["paqet"]) == "good"


def test_a_pair_test_teaches_the_doctor_whatever_started_it(monkeypatch):
    """The compare job keeps every row it produces: the Tunnel test page, the Doctor's own tests while it repairs, the Create flow and the survey all feed the same knowledge."""
    ir, fr = two_servers("k3")
    results = {"bpd:pck": {"latency_ms": 80.0, "loss_pct": 0.0, "down_mbps": 600.0, "score": 70}, "chisel:tls": {"latency_ms": 160.0, "loss_pct": 0.0, "score": 36}}

    def fake_other(iran_id, foreign_id, kind, measure="full"):
        with session() as db:
            e = ActionLog(ref="test-1", action="tunnel_test", source="manual", ok=kind == "bpd:pck", finished_at=int(time.time()), story=["done"])
            db.add(e)
            db.flush()
            aid = e.id

        tunnel_test.RESULTS[aid] = results[kind]
        return aid

    monkeypatch.setattr(tunnel_test, "start_other", fake_other)
    monkeypatch.setattr(tunnel_test.time, "sleep", lambda s: None)
    job = {"id": "cmp1", "iran_id": ir, "foreign_id": fr, "measure": "latency", "source": "manual", "kinds": ["bpd:pck", "chisel:tls"], "results": [], "current": "", "current_aid": None, "done": False, "stop": False, "started": int(time.time())}
    tunnel_test._compare_worker(job)
    p = knowledge.pair(ir, fr)
    assert knowledge.verdict(p["kinds"]["bpd:pck"]) == "good" and p["kinds"]["bpd:pck"]["lat"]["score"] == 70 and knowledge.verdict(p["kinds"]["chisel:tls"]) == "poor"      # (built and measured at 36: it connects)
    assert p["kinds"]["bpd:pck"]["last"]["measure"] == "latency" and "full" not in p["kinds"]["bpd:pck"]


# ------------------------------------------------------------------ the survey
def test_a_survey_tests_every_kind_in_the_standard_order_and_refuses_what_is_not_a_pair(monkeypatch):
    ir, fr = two_servers("k4")
    started = []
    monkeypatch.setattr(tunnel_test, "compare_start", lambda iran_id, foreign_id, kinds, measure="full", source="manual", install_wg=False: started.append((kinds, measure, source)) or "cmp9")
    assert knowledge.survey_start(ir, fr) == "cmp9"
    assert started[0][0] == knowledge.PRIOR and started[0][1:] == ("full", "survey-manual")
    knowledge.survey_start(ir, fr, "survey", ["paqet", "bpd:pck"])
    assert started[1][0] == ["bpd:pck", "paqet"] and started[1][2] == "survey" and started[1][1] == "latency"              # (the standard order, not the order asked; the night survey asks delay and loss only - no speed test on a server that carries users)

    with session() as db:
        said = [e.text for e in db.scalars(select(Event).where(Event.ref == "ir-k4"))]

    assert any(m.startswith(f"Survey of ir-k4 ⇄ fr-k4 started: {len(knowledge.PRIOR)} tunnel types") and "by hand: with a speed test" in m for m in said)

    for a, b in ((fr, ir), (ir, 99999)):
        with pytest.raises(ValueError, match="pick an Iran server and a foreign server"):
            knowledge.survey_start(a, b)

    with session() as db:
        db.get(Server, fr).online = False

    with pytest.raises(ValueError, match="both servers must be on"):
        knowledge.survey_start(ir, fr)


def fake_job(ir, fr, source="survey", done=False, results=None):
    job = {"id": "cmp77", "iran_id": ir, "foreign_id": fr, "measure": "full", "source": source, "kinds": list(knowledge.PRIOR), "results": results or [], "current": "paqet", "current_aid": None, "done": done, "stop": False, "started": int(time.time())}
    tunnel_test._COMPARE["cmp77"] = job
    return job


def test_a_repair_takes_the_place_of_a_survey_and_the_survey_can_be_stopped(monkeypatch):
    ir, fr = two_servers("k5")
    stopped = []
    monkeypatch.setattr(tunnel_test, "compare_stop", lambda jid: stopped.append(jid))
    assert knowledge.yield_slot() is False

    with pytest.raises(ValueError, match="no survey is running"):
        knowledge.stop()

    job = fake_job(ir, fr, results=[{"kind": "tcpmux", "ok": False}])
    try:
        r = knowledge._running()
        assert r == {"id": "cmp77", "iran_id": ir, "foreign_id": fr, "kind": "paqet", "done": 1, "total": len(knowledge.PRIOR), "source": "survey"}
        assert knowledge.yield_slot() is True and stopped == ["cmp77"]
        job["source"] = "manual"                                                                                      # a pair test of the owner is not a survey: it is not touched
        assert knowledge._running() is None and knowledge.yield_slot() is False
    finally:
        tunnel_test._COMPARE.pop("cmp77", None)


def test_the_repair_waits_for_its_turn_and_a_running_survey_gives_up_its_place(monkeypatch):
    ir, fr = two_servers("k6")
    fake_job(ir, fr)
    stopped, calls = [], []

    def start(iran_id, foreign_id, kinds, measure="latency"):
        calls.append(1)

        if len(calls) < 2:
            raise RuntimeError("another tunnel test is still running")

        return "job1"

    def stop(jid):
        stopped.append(jid)
        tunnel_test._COMPARE.pop("cmp77", None)

    monkeypatch.setattr(autopilot.tunnel_test, "compare_start", start)
    monkeypatch.setattr(autopilot.tunnel_test, "compare_stop", stop)
    monkeypatch.setattr(autopilot.tunnel_test, "compare_status", lambda jid: {"done": True, "results": [{"kind": "tcpmux", "ok": True, "score": 70, "loss_pct": 0.0}]})
    monkeypatch.setattr(autopilot, "_say", lambda *a, **k: None)
    monkeypatch.setattr(autopilot.time, "sleep", lambda s: None)

    try:
        assert autopilot._recommend_now(1, ir, fr, ["tcpmux"])[0]["score"] == 70
        assert stopped == ["cmp77"]
    finally:
        tunnel_test._COMPARE.pop("cmp77", None)


# ------------------------------------------------------------------ the night job and its rails
def live_pair(name, mbps=0.0):
    ir, fr = two_servers(name)

    with session() as db:
        db.add(Tunnel(name=f"{name}-t", type="backhaul", transport="tcpmux", port=9905, iran_server_id=ir, foreign_server_id=fr, state="up", forwarded_ports=[8075], rx_mbps=mbps, tx_mbps=0.0))

    return ir, fr


def night(monkeypatch, hour=3):
    """Switch the survey on and make it `hour` o'clock in Tehran (the window is 2 - 6)."""
    st.put("knowledge", {"on": True})
    t = NOW - NOW % DAY + (hour * 60 - 210) * 60
    monkeypatch.setattr(knowledge.time, "time", lambda: t)
    monkeypatch.setattr(knowledge.jobs, "stopping", lambda: False)
    monkeypatch.setattr(get_settings(), "read_only", False)
    monkeypatch.setattr(autopilot, "repairing_names", list)
    monkeypatch.setattr(autopilot, "storm_now", lambda: False)
    return t


def test_the_job_starts_the_survey_of_the_pair_with_the_least_knowledge_only_at_night_on_quiet_servers(monkeypatch):
    ir1, fr1 = live_pair("n1")
    ir2, fr2 = live_pair("n2")
    knowledge.record(ir1, fr1, {"kind": "tcpmux", "ok": True, "score": 60}, "full")                                    # the first pair knows one kind already
    started = []
    monkeypatch.setattr(knowledge, "survey_start", lambda iran_id, foreign_id, source="survey-manual", kinds=None: started.append((iran_id, foreign_id, source, kinds)) or "cmp1")
    night(monkeypatch, hour=14)
    knowledge.run()
    assert started == [(ir2, fr2, "survey-cold", knowledge.READY)]                                                    # an afternoon: only the pair nobody tested (on quiet servers); the one that knows a kind waits for the night
    started.clear()
    night(monkeypatch, hour=3)
    st.put("knowledge", {"on": False})
    knowledge.run()
    assert started == []                                                                                              # switched off: nothing
    night(monkeypatch, hour=3)
    knowledge.run()
    assert len(started) == 1 and started[0][:3] == (ir2, fr2, "survey") and started[0][3] == knowledge.READY           # the pair nobody tested first, the kinds a repair reaches for
    started.clear()
    monkeypatch.setattr(autopilot, "repairing_names", lambda: ["some-tunnel"])
    knowledge.run()
    assert started == []                                                                                              # a repair is running: nothing
    monkeypatch.setattr(autopilot, "repairing_names", list)
    monkeypatch.setattr(get_settings(), "read_only", True)
    knowledge.run()
    assert started == []                                                                                              # read-only: nothing is built


def test_a_busy_server_or_users_on_the_tunnels_keep_the_survey_waiting_and_the_next_pair_goes(monkeypatch):
    ir1, fr1 = live_pair("n3", mbps=200.0)                                                                             # 200 Mb/s for users
    ir2, fr2 = live_pair("n4")
    now = night(monkeypatch)

    with session() as db:
        db.add(ServerSample(server_id=fr2, ts=now - 30, cpu=75.0, ram=40.0, disk=30.0, load1=1.0, rx_mbps=1.0, tx_mbps=1.0, conn=5))

    assert "carry 200 Mb/s" in knowledge.not_now(ir1, fr1, now)
    assert "busy" in knowledge.not_now(ir2, fr2, now) and knowledge.not_now(ir2, fr2, now + 3600) == ""                    # (an old sample says nothing)
    ir3, fr3 = live_pair("n5")
    started = []
    monkeypatch.setattr(knowledge, "survey_start", lambda iran_id, foreign_id, source="survey-manual", kinds=None: started.append((iran_id, foreign_id)) or "cmp2")
    knowledge.run()
    assert started == [(ir3, fr3)]                                                                                    # the first two wait, the third has quiet servers


def test_a_survey_stops_when_the_night_is_over_or_a_repair_needs_the_place_and_one_event_tells_how_it_ended(monkeypatch):
    ir, fr = live_pair("n6")
    stopped = []
    started = []
    monkeypatch.setattr(knowledge, "survey_start", lambda iran_id, foreign_id, source="survey-manual", kinds=None: started.append(source) or "cmp78")      # (after the night the pair nobody tested is surveyed by day: it must not be a real survey here)
    monkeypatch.setattr(tunnel_test, "compare_stop", lambda jid: stopped.append(jid))
    job = fake_job(ir, fr, results=[{"kind": "bpd:pck", "ok": True, "score": 71}, {"kind": "paqet", "ok": True, "score": 58}, {"kind": "tcpmux", "ok": False, "error": "x"}])

    try:
        night(monkeypatch, hour=4)
        knowledge.run()
        assert stopped == []                                                                                          # inside the window, nothing needs the place: it goes on
        night(monkeypatch, hour=9)
        knowledge.run()
        assert stopped == ["cmp77"]                                                                                   # the window is closed
        stopped.clear()
        night(monkeypatch, hour=4)
        monkeypatch.setattr(autopilot, "repairing_names", lambda: ["pl-x"])
        knowledge.run()
        assert stopped == ["cmp77"]                                                                                   # a repair started
        job["source"] = "survey-manual"
        stopped.clear()
        night(monkeypatch, hour=9)
        knowledge.run()
        assert stopped == []                                                                                          # a survey the owner asked for goes on after the night (a repair still stops it)
        job["done"], job["stop"] = True, True
        knowledge.run()
        knowledge.run()
    finally:
        tunnel_test._COMPARE.pop("cmp77", None)

    with session() as db:
        said = [e.text for e in db.scalars(select(Event).where(Event.ref == "ir-n6"))]

    assert sum(m.startswith(f"Survey of ir-n6 ⇄ fr-n6 stopped: 3 of {len(knowledge.PRIOR)} tunnel types tested, 2 work - best: bpd:pck 71, paqet 58.") for m in said) == 1        # told once
    assert started and set(started) == {"survey-cold"}                                                                # (the night is over and the pair has no knowledge: by day it is the cold survey, never the night one)


def test_the_switch_the_hours_and_the_survey_are_reachable_through_the_api(client, monkeypatch):
    ir, fr = two_servers("api")
    assert client.put("/api/doctor/knowledge", json={"on": True, "from_hour": 1, "to_hour": 5}).json() == {"ok": True, "on": True, "from_hour": 1, "to_hour": 5}
    assert client.put("/api/doctor/knowledge", json={"from_hour": 24}).status_code == 422 and client.put("/api/doctor/knowledge", json={"to_hour": -1}).status_code == 422
    assert client.put("/api/doctor/knowledge", json={"on": False}).json()["on"] is False
    monkeypatch.setattr(get_settings(), "read_only", False)
    monkeypatch.setattr(knowledge, "survey_start", lambda iran_id, foreign_id, *a, **k: "cmp5")
    assert client.post("/api/doctor/knowledge/survey", json={"iran_id": ir, "foreign_id": fr}).json() == {"ok": True, "id": "cmp5"}
    monkeypatch.setattr(knowledge, "survey_start", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("another tunnel test is still running")))
    assert client.post("/api/doctor/knowledge/survey", json={"iran_id": ir, "foreign_id": fr}).status_code == 409
    monkeypatch.setattr(knowledge, "survey_start", lambda *a, **k: (_ for _ in ()).throw(ValueError("both servers must be on")))
    assert client.post("/api/doctor/knowledge/survey", json={"iran_id": ir, "foreign_id": fr}).status_code == 400
    assert client.post("/api/doctor/knowledge/stop").status_code == 404                                                # nothing runs
    monkeypatch.setattr(get_settings(), "read_only", True)
    assert client.post("/api/doctor/knowledge/survey", json={"iran_id": ir, "foreign_id": fr}).status_code == 403


def test_a_server_that_is_removed_takes_what_the_doctor_knew_about_its_pairs_with_it(client):
    ir, fr = two_servers("gone")
    other_ir, other_fr = two_servers("stay")
    knowledge.record(ir, fr, {"kind": "paqet", "ok": True, "score": 58}, "full")
    knowledge.record(other_ir, other_fr, {"kind": "paqet", "ok": True, "score": 58}, "full")
    assert client.delete(f"/api/servers/{fr}").status_code == 200
    assert knowledge.pair(ir, fr) == {} and knowledge.pair(other_ir, other_fr)["kinds"]["paqet"]
