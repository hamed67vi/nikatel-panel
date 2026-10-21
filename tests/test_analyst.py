"""The analyst: profiles from the checks, outages that come together, what fixes what, and the hints the smart repair reads."""
import time

from nikatel.services import analyst as an

NOW = 1_800_000_000


def checks(pattern, start=NOW - 86400, step=60, rtt=40.0, err="x"):
    """pattern: a string of 'o' (ok) and 'x' (failed), one check each."""
    return [(start + i * step, ch == "o", rtt if ch == "o" else None, "" if ch == "o" else err) for i, ch in enumerate(pattern)]


def test_an_outage_needs_two_failed_checks_in_a_row_and_lasts_until_the_next_good_one():
    ck = [(ts, ok) for ts, ok, _r, _e in checks("ooxoooxxxxoooxx")]
    outs = an.find_outages(ck, now=NOW)
    assert len(outs) == 2 and outs[0]["secs"] == 4 * 60 and not outs[0]["ongoing"]      # the single lost check is noise
    assert outs[1]["ongoing"] is True and outs[1]["end"] is None
    assert an.find_outages([(0, False), (60, False), (300, True)], now=5000)[0]["secs"] == 300       # it lasts until the next good check
    assert an.find_outages([(0, False), (4000, False), (4060, False)], now=5000)[0]["start"] == 4000    # a hole in the data starts a new run


def test_profile_availability_mttr_verdict_and_latency():
    ck = checks("o" * 100 + "xx" + "o" * 97 + "xxx" + "o" * 98, start=NOW - 300 * 60)
    p = an.tunnel_profile(ck, NOW, days=1)
    assert p["checks"] == 300 and p["outages"] == 2 and p["availability"] == round(295 * 100 / 300, 2)
    assert p["mttr_secs"] == int((2 * 60 + 3 * 60) / 2) and p["rtt_base"] == 40.0 and p["verdict"] == "chronic"       # two outages a day and 98 %: chronic
    quiet = an.tunnel_profile(checks("o" * 400, start=NOW - 400 * 60), NOW, days=1)
    assert quiet["verdict"] == "stable" and quiet["outages"] == 0 and quiet["mttr_secs"] is None
    assert an.tunnel_profile([], NOW, 1)["verdict"] == "no data"


def minute_checks(days, failed=()):
    """One check a minute for `days` days (the newest a minute before NOW); `failed`: (minutes ago, how many in a row) - the checks that failed."""
    bad = {m for a, n in failed for m in range(a, a - n, -1)}
    return [(NOW - m * 60, m not in bad) for m in range(days * 1440, 0, -1)]


def test_the_stability_box_says_the_share_that_answered_the_cuts_the_last_one_and_how_long_it_is_up():
    """The owner, 2026-10-17: the stability of a tunnel in its own box. A cut = two failed checks in a row (the analyst's rule); one lost check is noise and counts only against the share."""
    ck = minute_checks(3, failed=[(244, 5), (600, 1), (4000, 10)])           # a cut 4 h ago (5 checks), one lost check 10 h ago, a cut of 10 checks 2.8 days ago
    v = an.stability_view(ck, NOW, "up", NOW - 90 * 3600)
    assert v["d1"]["checks"] == 1440 and v["d1"]["cuts"] == 1 and v["d1"]["down_secs"] == 300 and v["d1"]["longest_secs"] == 300 and v["d1"]["avail"] == round(1434 * 100 / 1440, 2)
    assert v["d7"]["checks"] == 4320 and v["d7"]["cuts"] == 2 and v["d7"]["down_secs"] == 900 and v["d7"]["longest_secs"] == 600 and v["d7"]["avail"] == round(4304 * 100 / 4320, 2)
    assert v["last"] == {"start": NOW - 244 * 60, "end": NOW - 239 * 60, "secs": 300, "ongoing": False} and v["up_for"] == 90 * 3600 and v["verdict"] == "stable"
    assert len(v["strip"]) == an.STRIP_CELLS == 48 and v["strip"][0] == 2 and v["strip"][39] == 1 and v["strip"][40] == 1 and v["strip"][47] == 2      # (the cut shows as "now and then" cells: half an hour each)
    assert v["strip_to"] == NOW and v["strip_from"] == NOW - 86400


def test_the_stability_of_a_tunnel_that_is_cut_now_a_new_one_and_one_with_holes():
    ck = minute_checks(1, failed=[(2, 2)])                                    # the last two checks failed and nothing has answered since
    v = an.stability_view(ck, NOW, "down", NOW - 600)
    assert v["last"]["ongoing"] is True and v["last"]["end"] is None and v["last"]["secs"] == 120 and v["up_for"] is None and v["d1"]["cuts"] == 1
    new = an.stability_view(minute_checks(1)[-10:], NOW, "up", NOW - 600)    # ten checks: too few to say a share
    assert new["d1"]["avail"] is None and new["d7"]["avail"] is None and new["verdict"] == "no data" and new["last"] is None and new["up_for"] == 600
    short = an.stability_view(minute_checks(1)[-120:], NOW)                    # only the last two hours: the cells before them say "no check"
    assert short["strip"][:44] == [-1] * 44 and short["strip"][47] == 2
    bad = an.stability_view(minute_checks(2, failed=[(300, 200)]), NOW)       # a long cut of 200 checks: the share and the verdict follow
    assert bad["d1"]["cuts"] == 1 and bad["d1"]["longest_secs"] == 200 * 60 and bad["verdict"] in ("watch", "chronic")
    assert an.stability_view([], NOW)["d7"]["checks"] == 0 and an.stability_view([], NOW)["verdict"] == "no data"


def test_the_stability_route_reads_the_checks_of_one_tunnel(client):
    from nikatel.db import session
    from nikatel.models import Server, Tunnel, TunnelCheck

    now = int(time.time())

    with session() as db:
        i, f = Server(name="ir9", host="203.0.113.9", role="iran", country="IR", online=True), Server(name="fi9", host="198.51.100.9", role="foreign", online=True)
        db.add_all([i, f])
        db.flush()
        t = Tunnel(name="t9", type="backhaul", transport="tcpmux", iran_server_id=i.id, foreign_server_id=f.id, state="up", state_since=now - 7200)
        db.add(t)
        db.flush()
        tid = t.id

        for k in range(120):
            db.add(TunnelCheck(tunnel_id=tid, ts=now - (120 - k) * 60, ok=not (50 <= k < 53), rtt_ms=40.0, error="" if not (50 <= k < 53) else "no data"))

    r = client.get(f"/api/tunnels/{tid}/stability").json()
    assert r["ok"] and r["d1"]["checks"] == 120 and r["d1"]["cuts"] == 1 and r["d1"]["avail"] == round(117 * 100 / 120, 2) and r["last"]["ongoing"] is False and 7100 <= r["up_for"] <= 7300
    assert len(r["strip"]) == 48 and client.get("/api/tunnels/999999/stability").status_code == 404


def test_a_rising_latency_and_a_weak_hour_make_a_tunnel_at_risk():
    ck = checks("o" * 200, start=NOW - 200 * 60)
    ck = [c if NOW - c[0] > 1500 else (c[0], True, 140.0, "") for c in ck]          # the last 25 minutes: 140 ms against 40
    p = an.tunnel_profile(ck, NOW, days=1)
    assert p["rtt_ratio"] and p["rtt_ratio"] > 1.5
    assert any("latency" in w for w in an.risk_of(p, "up", 3))
    assert an.risk_of(p, "down", 3) == []                                            # a tunnel that is down is not "at risk", it is down
    weak = {**an.tunnel_profile(checks("o" * 400, start=NOW - 400 * 60), NOW, 1), "weak_hours": [21], "verdict": "watch"}
    assert any("weak hours" in w for w in an.risk_of(weak, "up", 21)) and not an.risk_of(weak, "up", 9)


def test_outages_that_come_together_point_at_the_shared_server():
    outs = {"a": [{"start": 1000, "end": 1300}, {"start": 5000, "end": 5200}, {"start": 9000, "end": 9100}], "b": [{"start": 1050, "end": 1250}, {"start": 5100, "end": 5300}, {"start": 9050, "end": 9200}], "c": [{"start": 20000, "end": 20100}]}
    ends = {"a": ("ir1", "fo1"), "b": ("ir1", "fo2"), "c": ("ir2", "fo3")}
    found = an.correlate(outs, ends)
    assert [f["server"] for f in found] == ["ir1"] and found[0]["together"] == 6
    apart = {"a": [{"start": 1000, "end": 1100}, {"start": 4000, "end": 4100}, {"start": 8000, "end": 8100}], "b": [{"start": 2500, "end": 2600}, {"start": 6000, "end": 6100}, {"start": 12000, "end": 12100}]}
    assert an.correlate(apart, {"a": ("ir1", "fo1"), "b": ("ir1", "fo2")}) == []


def test_step_stats_count_what_healed_how_often_and_how_fast():
    rows = [{"tunnel": "t", "cause": "units_down", "step": "restart", "ok": True, "secs": 30, "ts": NOW}, {"tunnel": "t", "cause": "units_down", "step": "restart", "ok": False, "secs": 40, "ts": NOW},
            {"tunnel": "t", "cause": "gre_blocked", "step": "relink", "ok": True, "secs": 120, "ts": NOW}]
    s = an.step_stats(rows)
    assert s["by_step"]["restart"] == {"attempts": 2, "healed": 1, "rate": 50, "avg_secs": 30}
    assert s["by_cause"]["gre_blocked"]["relink"]["rate"] == 100


def row(step, ok, ago=3600, cause="unknown", tunnel="t"):
    return {"tunnel": tunnel, "cause": cause, "step": step, "ok": ok, "secs": 10, "ts": NOW - ago}


def test_hints_skip_a_step_that_failed_again_and_again_but_never_the_way_out():
    mine = [row("restart", False, 3000 + i * 100) for i in range(3)]
    keep, notes = an.choose_steps(["restart", "rebuild"], "unknown", mine, mine, NOW)
    assert keep == ["rebuild"] and "restart" in notes[0] and "3 times in a row" in notes[0]
    healed_between = [row("restart", False, 5000), row("restart", True, 4000), row("restart", False, 3000), row("restart", False, 2000)]
    assert an.choose_steps(["restart", "rebuild"], "unknown", healed_between, healed_between, NOW)[0] == ["restart", "rebuild"]       # it healed once since: not "again and again"
    assert an.choose_steps(["restart", "rebuild"], "units_down", mine, mine, NOW)[0] == ["restart", "rebuild"]              # a dead service is always restarted first
    assert an.choose_steps(["switch", "move"], "lossy_route", mine, mine, NOW)[0] == ["switch", "move"]
    only = an.choose_steps(["restart"], "unknown", mine, mine, NOW)
    assert only[0] == ["restart"] and only[1] == []                                    # never an empty ladder
    everywhere = [row("rebuild", False, 1000 + i, cause="route_cut", tunnel=f"x{i}") for i in range(10)]
    keep2, notes2 = an.choose_steps(["rebuild", "move"], "route_cut", [], everywhere, NOW)
    assert keep2 == ["move"] and "0 of 10" in notes2[0]
    old = [row("restart", False, 20 * 86400 + i) for i in range(3)]
    assert an.choose_steps(["restart", "rebuild"], "unknown", old, old, NOW)[0] == ["restart", "rebuild"]                    # older than the window: forgotten


def test_servers_idle_redundant_busy():
    base = {"role": "foreign", "city": "Frankfurt", "tunnels": 0, "tunnels_mbps": 0}
    rows = [{**base, "name": "a", "cores": 2, "peak_cpu_p95": 55, "peak_mbps": 200}, {**base, "name": "b", "cores": 2, "peak_cpu_p95": 25, "peak_mbps": 90},
            {**base, "name": "c", "cores": 1, "peak_cpu_p95": 10, "peak_mbps": 5}, {**base, "name": "d", "city": "Paris", "cores": 2, "peak_cpu_p95": 80, "peak_mbps": 300},
            {**base, "name": "e", "city": "Rome", "cores": 2, "peak_cpu_p95": 28, "peak_mbps": 60}, {**base, "name": "f", "city": "Rome", "cores": 2, "peak_cpu_p95": 29, "peak_mbps": 60}]
    v = {r["name"]: r for r in an.server_verdicts(rows)}
    assert v["c"]["verdict"] == "idle" and v["d"]["verdict"] == "busy"
    assert v["e"]["verdict"] == "redundant" and "f" in v["e"]["note"]                  # the lighter of two in the same city, the other has room for it
    assert v["a"]["verdict"] in ("ok", "light") and v["f"]["verdict"] in ("ok", "light")


def test_overview_and_the_smart_repair_record(client):
    from nikatel.db import session
    from nikatel.models import Server, Tunnel, TunnelCheck

    with session() as db:
        i, f = Server(name="ir1", host="1.1.1.1", role="iran", country="IR", online=True, facts={"cores": 2}), Server(name="fi1", host="2.2.2.2", role="foreign", online=True, facts={"cores": 2})
        db.add_all([i, f])
        db.flush()
        t = Tunnel(name="t1", type="backhaul", transport="tcpmux", iran_server_id=i.id, foreign_server_id=f.id, state="up")
        db.add(t)
        db.flush()
        now = int(time.time())

        for k in range(200):
            db.add(TunnelCheck(tunnel_id=t.id, ts=now - (200 - k) * 60, ok=not (100 <= k < 103), rtt_ms=50.0, error="" if not (100 <= k < 103) else "no data"))

    an._CACHE.clear()
    an.record_case("t1", "ir1", "fi1", "tcpmux", "units_down", [{"step": "restart", "ok": False, "secs": 20}, {"step": "rebuild", "ok": True, "secs": 90}], True, 110, "auto")
    r = client.get("/api/analyst?days=1").json()
    assert r["ok"] and r["summary"]["tunnels"] == 1 and r["tunnels"][0]["outages"] == 1 and r["tunnels"][0]["mttr_secs"] == 180
    assert r["repairs"]["by_step"]["rebuild"]["healed"] == 1 and {s["name"] for s in r["servers"]} == {"ir1", "fi1"}
    keep, notes = an.hints_for("t1", "unknown", ["restart", "rebuild"])
    assert keep == ["restart", "rebuild"] and notes == []


def test_two_checks_of_the_same_second_with_and_without_a_ping_do_not_break_the_overview(client):
    """The owner, 2026-10-21 (Tunnel Doctor, Analytics: "internal error"): the page of a server that goes off or comes back looks at its tunnels at once, so two checks of one tunnel can carry the same second - one with
    a ping, one with none - and sorting their tuples compared None with a number."""
    from nikatel.db import session
    from nikatel.models import Server, Tunnel, TunnelCheck

    with session() as db:
        i, f = Server(name="ir-an1", host="203.0.113.201", role="iran", online=True), Server(name="fo-an1", host="198.51.100.201", role="foreign", online=True)
        db.add_all([i, f])
        db.flush()
        t = Tunnel(name="t-an1", type="backhaul", transport="tcpmux", iran_server_id=i.id, foreign_server_id=f.id, state="up", enabled=True, forwarded_ports=[8075])
        db.add(t)
        db.flush()
        ts = int(time.time()) - 600
        db.add_all([TunnelCheck(tunnel_id=t.id, ts=ts, ok=True, rtt_ms=41.0), TunnelCheck(tunnel_id=t.id, ts=ts, ok=True, rtt_ms=None), TunnelCheck(tunnel_id=t.id, ts=ts + 60, ok=True, rtt_ms=40.0)])

    r = client.get("/api/analyst")
    assert r.status_code == 200 and r.json()["ok"] is True and any(p["name"] == "t-an1" for p in r.json()["tunnels"])
