"""What the USERS get through a tunnel (services/userq.py): which configs ride which tunnel (the label, the address of the Iran server, the A record of a CDN name), how each config does against ITSELF,
the grade of the tunnel with its hysteresis, the one sentence that says whether the problem is the tunnel or lies beyond it, and the job that keeps it all in the record of the tunnel."""
import time

from sqlalchemy import select

from nikatel.db import session
from nikatel.models import Config, ConfigSample, Event, Server, Tunnel
from nikatel.services import autopilot, configs, dnscache, probe, userq

NOW = 1_800_000_000
SERVERS = [{"id": 1, "name": "ir-alpha", "host": "203.0.113.10"}, {"id": 2, "name": "ir-beta", "host": "203.0.113.20"}]
TUNNELS = [{"id": 10, "name": "nl1-ir-alpha", "iran_id": 1, "ports": {80, 8075}}, {"id": 11, "name": "de2-ir-beta", "iran_id": 2, "ports": {80}}, {"id": 12, "name": "tr3-ir-beta", "iran_id": 2, "ports": {8072}}]
RECORDS = [{"fqdn": "gt.cdn.example.com", "type": "A", "ips": ["203.0.113.10"], "port": None}, {"fqdn": "cn.cdn.example.com", "type": "A", "ips": ["203.0.113.20"], "port": 8072}, {"fqdn": "mx.cdn.example.com", "type": "A", "ips": ["203.0.113.99"], "port": 80},
           {"fqdn": "txt.cdn.example.com", "type": "TXT", "ips": [], "port": None}]


def cfg(name, address, port=80, label=""):
    return {"name": name, "tunnel": label, "address": address, "port": port}


def test_a_config_is_placed_on_its_tunnel_by_label_address_or_dns_record():
    got = userq.map_configs([cfg("by-label", "whatever.example", 1234, "de2-ir-beta"), cfg("by-ip", "203.0.113.10", 8075), cfg("by-server-name", "IR-BETA", 80), cfg("cdn", "gt.cdn.example.com", 80), cfg("cdn-origin-port", "cn.cdn.example.com", 80),
                             cfg("wrong-port", "203.0.113.10", 9999), cfg("other-ip", "203.0.113.99", 80), cfg("unknown-name", "nowhere.example", 80), cfg("cdn-not-ours", "mx.cdn.example.com", 80), cfg("no-address", "", 0), cfg("txt", "txt.cdn.example.com", 80)],
                            TUNNELS, SERVERS, RECORDS)
    assert got["by-label"] == {"tunnel": 11, "how": "label"}                                          # the operator's word wins
    assert got["by-ip"] == {"tunnel": 10, "how": "address"} and got["by-server-name"] == {"tunnel": 11, "how": "address"}
    assert got["cdn"] == {"tunnel": 10, "how": "dns"}                                                   # the A record of the name points at ir-alpha (port 80 is forwarded there)
    assert got["cdn-origin-port"] == {"tunnel": 12, "how": "dns"}                                       # the record sends to port 8072: the tunnel that forwards 8072 on ir-beta, not the one that forwards 80
    assert not ({"wrong-port", "other-ip", "unknown-name", "cdn-not-ours", "no-address", "txt"} & set(got))      # nothing is guessed


def test_two_tunnels_that_could_both_carry_a_config_leave_it_unplaced():
    tunnels = [{"id": 1, "name": "a", "iran_id": 1, "ports": {80}}, {"id": 2, "name": "b", "iran_id": 1, "ports": {80}}]
    assert userq.map_configs([cfg("c", "203.0.113.10", 80)], tunnels, SERVERS, []) == {}


def samples(up_pattern, ping=500.0, step=120, now=NOW):
    """Tests of the last two hours, the newest last: up_pattern is a string of 1 (answered) and 0."""
    n = len(up_pattern)
    return [(now - (n - i) * step, c == "1", ping if c == "1" else None) for i, c in enumerate(up_pattern)]


def test_what_the_tests_of_a_config_say_about_it_against_its_own_week():
    s = userq.config_stats(samples("1" * 60, 700.0), NOW, 650.0)
    assert s["n"] == 60 and s["up_share"] == 1.0 and s["ping"] == 700.0 and s["ref"] == 650.0 and s["ratio"] == 1.08
    s = userq.config_stats(samples("1" * 9), NOW, None)
    assert s["up_share"] is None and s["ratio"] is None                                                  # nine tests: too few to judge
    old = [(NOW - 3 * 3600, True, 100.0)]
    assert userq.config_stats(old, NOW)["n"] == 0                                                        # outside the window
    s = userq.config_stats(samples("1" * 45 + "0" * 15), NOW, 200.0)
    assert s["up_share"] == 0.75 and s["ping"] == 500.0                                                  # the last six ANSWERED pings, whatever came in between


def test_a_config_is_good_slow_unstable_or_down():
    good = userq.config_stats(samples("1" * 60, 600.0), NOW, 580.0)
    assert userq.config_issue("up", 99999, good) == ("good", "")
    assert userq.config_issue("down", 1200, good)[0] == "down" and "20 minutes" in userq.config_issue("down", 1200, good)[1]
    unsure = userq.config_stats(samples("1" * 45 + "0" * 15), NOW, 200.0)                              # a config that answered 75 % of its tests
    assert userq.config_issue("up", 99999, unsure)[0] == "unstable" and "75 %" in userq.config_issue("up", 99999, unsure)[1]
    slow = userq.config_stats(samples("1" * 60, 706.0), NOW, 192.0)                                      # ... and its ping 706 against 192
    assert userq.config_issue("up", 99999, slow) == ("slow_ping", "ping 706 ms (usually 192)")
    assert userq.config_issue("up", 99999, userq.config_stats(samples("1" * 60, 150.0), NOW, 60.0))[0] == "good"      # 2.5 times, but only 90 ms more: a quick config is not "slow" for that
    assert userq.config_issue("down", 100, good)[0] == "good"                                            # down for two minutes: the probe's own settle has not decided; the tests of the window do
    assert userq.config_issue("unknown", 0, good) == ("unknown", "")
    assert userq.config_issue("up", 0, userq.config_stats([], NOW)) == ("unknown", "")                   # a config just added: nothing to say
    fast = {"down": 8.0, "ref": 60.0, "age": 3600}
    assert userq.config_issue("up", 99999, good, fast) == ("slow_speed", "speed 8 Mb/s (usually 60)")
    assert userq.config_issue("up", 99999, good, {**fast, "age": 13 * 3600})[0] == "good"                # an old speed test says nothing about now
    assert userq.config_issue("up", 99999, good, {"down": 8.0, "ref": 4.0, "age": 100})[0] == "good"      # a config that never gave more than 4 Mb/s


def test_the_grade_of_a_tunnel_follows_how_many_of_its_configs_have_a_problem():
    g = userq.tunnel_ux
    assert g([])["grade"] == "unknown" and g([("a", "unknown", "")])["grade"] == "unknown"
    assert g([("a", "good", ""), ("b", "good", "")])["grade"] == "good"
    ux = g([("a", "good", ""), ("b", "good", ""), ("c", "slow_ping", "ping 700 ms (usually 200)")])
    assert ux["grade"] == "fair" and ux["n"] == 3 and ux["known"] == 3 and ux["bad"] == [{"config": "c", "issue": "slow_ping", "text": "ping 700 ms (usually 200)"}]
    assert g([("a", "good", ""), ("b", "down", "does not connect")])["grade"] == "poor"                  # half
    assert g([("a", "down", "x"), ("b", "unstable", "y")])["grade"] == "bad"                            # nobody gets through
    assert g([("a", "slow_ping", "x")])["grade"] == "poor"                                               # the only one is slow, not dead
    assert g([("a", "unknown", ""), ("b", "down", "x")])["grade"] == "bad" and g([("a", "unknown", ""), ("b", "good", "")])["grade"] == "good"    # (the unknown ones are not counted)


def test_a_grade_changes_after_two_looks_in_a_row_that_agree():
    assert userq.settle_grade("", "poor", None) == ("poor", {"grade": "poor", "n": 1})                    # a tunnel with no grade yet takes the first at once
    grade, pend = userq.settle_grade("good", "poor", None)
    assert grade == "good" and pend == {"grade": "poor", "n": 1}
    grade, pend = userq.settle_grade("good", "poor", pend)
    assert grade == "poor"
    grade, pend = userq.settle_grade("poor", "good", pend)
    assert grade == "poor"                                                                                # one good look does not end it
    assert userq.settle_grade("poor", "fair", {"grade": "good", "n": 1})[0] == "poor"                     # two different new grades do not add up
    assert userq.settle_grade("good", "good", {"grade": "poor", "n": 1})[0] == "good"


def test_the_sentence_of_the_users_and_where_the_problem_lies():
    ux = userq.tunnel_ux([("Germany-tcp", "slow_ping", "ping 706 ms (usually 192)"), ("Italy", "down", "does not connect (for 20 minutes)"), ("A", "good", ""), ("B", "good", "")])
    assert userq.summary(ux) == "2 of the 4 configs through it have a problem: Germany-tcp ping 706 ms (usually 192); Italy does not connect (for 20 minutes)."
    assert userq.summary(userq.tunnel_ux([("X", "down", "does not connect")])).startswith("1 of the 1 configs through it has a problem")
    fine = {"state": "up", "n_bad": 2, "known": 4, "bw_state": "ok", "bw_text": "310 Mb/s", "iran": {"name": "ir1", "cpu": 20}, "foreign": {"name": "fr1", "cpu": 30}, "loss": 0.0, "rtt": 80.0, "rtt_ref": 78.0}
    code, text = userq.explain_users(fine)
    assert code == "beyond" and "BEYOND the tunnel" in text and "2 of the 4 configs" in text and "310 Mb/s" in text            # the tunnel is fine: the problem is past it
    assert userq.explain_users({**fine, "state": "down"})[0] == "tunnel_down"
    assert userq.explain_users({**fine, "bw_state": "drop"})[0] == "tunnel_slow"
    assert userq.explain_users({**fine, "iran": {"name": "ir1", "cpu": 91}})[0] == "iran_busy" and userq.explain_users({**fine, "foreign": {"name": "fr1", "cpu": 90}})[0] == "foreign_busy"
    assert userq.explain_users({**fine, "loss": 9.0})[0] == "tunnel_loss" and userq.explain_users({**fine, "rtt": 130.0})[0] == "tunnel_delay"
    assert userq.explain_users({**fine, "n_bad": 1, "known": 1})[1].count("the config through it has a problem") == 1


# ------------------------------------------------------------------ the panel's data
def world():
    """ir-alpha (Iran) with the tunnel nl1 forwarding 80, a foreign server, and three configs: two through the CDN name gt.cdn.example.com (80), one by the address of the Iran server."""
    with session() as db:
        ir, fr = Server(name="ir-alpha", host="203.0.113.10", role="iran", online=True), Server(name="NL1", host="198.51.100.7", role="foreign", online=True)
        db.add_all([ir, fr])
        db.flush()
        t = Tunnel(name="nl1-ir-alpha", type="backhaul", transport="tcpmux", port=9901, iran_server_id=ir.id, foreign_server_id=fr.id, state="up", forwarded_ports=[80], record={})
        db.add(t)
        db.flush()
        out = {"tunnel": t.id, "iran": ir.id, "foreign": fr.id, "configs": {}}
        t0 = int(time.time())

        for name, addr in (("France", "gt.cdn.example.com"), ("Italy", "gt.cdn.example.com"), ("Direct", "203.0.113.10")):
            c = Config(name=name, status="up", status_since=t0 - 86400, ping_ms=500.0, meta={"address": addr, "port": 80})
            db.add(c)
            db.flush()
            out["configs"][name] = c.id

    return out


def add_samples(cid, pattern_recent, ping_recent, ping_week=500.0, n_week=300):
    now = int(time.time())

    with session() as db:
        for i in range(n_week):                                                                               # the week before the last two hours
            db.add(ConfigSample(config_id=cid, ts=now - 3 * 3600 - i * 120, up=True, ping_ms=ping_week))

        for i, ch in enumerate(pattern_recent):
            db.add(ConfigSample(config_id=cid, ts=now - (len(pattern_recent) - i) * 120, up=ch == "1", ping_ms=ping_recent if ch == "1" else None))


def test_the_panel_reads_the_users_of_every_tunnel_from_the_tests_of_its_configs(monkeypatch):
    w = world()
    monkeypatch.setitem(dnscache.CACHE, "rows", [{"fqdn": "gt.cdn.example.com", "type": "A", "ips": ["203.0.113.10"], "port": None}])
    add_samples(w["configs"]["France"], "1" * 60, 520.0)                                                      # fine
    add_samples(w["configs"]["Italy"], "1" * 40 + "0" * 20, 520.0)                                           # answered two thirds of its tests
    add_samples(w["configs"]["Direct"], "1" * 60, 1600.0)                                                     # its ping tripled
    got = userq.read_all()
    ux = got[w["tunnel"]]
    assert ux["grade"] == "poor" and ux["n"] == 3 and ux["known"] == 3
    issues = {b["config"]: b["issue"] for b in ux["bad"]}
    assert issues == {"Italy": "unstable", "Direct": "slow_ping"}                                             # France is fine; the CDN name found its Iran server through the A record

    with session() as db:                                                                                     # the usual ping of a config is worked out once and kept for an hour
        meta = db.get(Config, w["configs"]["France"]).meta

    assert meta["q"]["ping_ref"] == 500.0 and meta["address"] == "gt.cdn.example.com"


def test_the_speed_of_a_config_is_compared_with_its_own_history(monkeypatch):
    from datetime import datetime, timezone

    now = int(time.time())
    hist = [[now - 86400 + i, v, 10.0] for i, v in enumerate((60.0, 55.0, 70.0, 64.0, 58.0))]
    c = Config(name="x", down_mbps=9.0, up_mbps=3.0, speed_at=datetime.fromtimestamp(now - 600, timezone.utc).isoformat(), meta={"speed_hist": hist})
    s = userq._speed_of(c, now)
    assert s["down"] == 9.0 and s["ref"] == 64.0 and 590 <= s["age"] <= 610                                  # 75th percentile of 55 58 60 64 70
    assert userq._speed_of(Config(name="y", down_mbps=9.0, speed_at=c.speed_at, meta={"speed_hist": hist[:3]}), now) is None            # fewer than four tests: the usual speed is not known
    assert userq._speed_of(Config(name="z", down_mbps=None, speed_at="", meta={"speed_hist": hist}), now) is None
    assert userq._speed_of(Config(name="w", down_mbps=9.0, speed_at="not a time", meta={"speed_hist": hist}), now) is None


def test_the_job_keeps_the_grade_with_two_looks_and_writes_the_events_and_starts_the_look(monkeypatch):
    w = world()
    monkeypatch.setitem(dnscache.CACHE, "rows", [{"fqdn": "gt.cdn.example.com", "type": "A", "ips": ["203.0.113.10"], "port": None}])
    monkeypatch.setitem(dnscache.CACHE, "at", time.time())
    monkeypatch.setattr(userq.jobs, "stopping", lambda: False)
    looked = []
    monkeypatch.setattr(userq, "_investigate_later", lambda tid: looked.append(tid))

    for name in ("France", "Italy", "Direct"):
        add_samples(w["configs"][name], "1" * 60, 520.0)

    userq.run()

    with session() as db:
        ux = db.get(Tunnel, w["tunnel"]).record["ux"]

    assert ux["grade"] == "good" and ux["known"] == 3 and looked == []                                        # the first look: the first grade, at once
    with session() as db:                                                                                    # now two of the three go bad
        for name in ("Italy", "Direct"):
            for s in db.scalars(select(ConfigSample).where(ConfigSample.config_id == w["configs"][name], ConfigSample.ts >= int(time.time()) - 7200)):
                s.up, s.ping_ms = False, None

    userq.run()

    with session() as db:
        ux = db.get(Tunnel, w["tunnel"]).record["ux"]

    assert ux["grade"] == "good" and ux["pend"] == {"grade": "poor", "n": 1} and looked == []                 # one look: not yet
    userq.run()

    with session() as db:
        ux = db.get(Tunnel, w["tunnel"]).record["ux"]
        said = [e.text for e in db.scalars(select(Event).where(Event.ref == "nl1-ir-alpha"))]

    assert ux["grade"] == "poor" and len(ux["bad"]) == 2 and looked == [w["tunnel"]]
    assert sum("the users get a poor connection" in m and "2 of the 3 configs through it have a problem" in m for m in said) == 1
    userq.run()
    assert looked == [w["tunnel"]]                                                                            # told and looked at once, not at every turn
    with session() as db:                                                                                    # they come back
        for name in ("Italy", "Direct"):
            for s in db.scalars(select(ConfigSample).where(ConfigSample.config_id == w["configs"][name], ConfigSample.ts >= int(time.time()) - 7200)):
                s.up, s.ping_ms = True, 520.0

    userq.run()
    userq.run()

    with session() as db:
        assert db.get(Tunnel, w["tunnel"]).record["ux"]["grade"] == "good"
        assert any("get a good connection again" in e.text for e in db.scalars(select(Event).where(Event.ref == "nl1-ir-alpha")))


def test_the_look_at_suffering_users_says_the_tunnel_is_fine_and_the_problem_is_beyond_it(monkeypatch):
    w = world()
    now = int(time.time())

    with session() as db:
        t = db.get(Tunnel, w["tunnel"])
        t.record = {"bw": {"down": 310.0, "ref": 305.0, "state": "ok"}, "ux": {"grade": "poor", "n": 3, "known": 3, "bad": [{"config": "Italy", "issue": "unstable", "text": "answered only 66 % of its tests in the last 2 hours"},
                                                                                                                         {"config": "Direct", "issue": "slow_ping", "text": "ping 1600 ms (usually 500)"}]}}
        t.detail = {"loss_pct": 0.0}
        t.rtt_ms = 80.0

    said = []
    monkeypatch.setattr(autopilot, "_say", lambda aid, line, done=None, ok=None: said.append((line, done, ok)))
    userq.investigate(5, w["tunnel"])
    lines = [s[0] for s in said]
    assert lines[0].startswith("nl1-ir-alpha: 2 of the 3 configs through it have a problem: Italy answered only 66 %") and "✕ Direct: ping 1600 ms (usually 500)" in lines
    assert "✓ The tunnel is up" in lines and any(ln.startswith("✓ Its bandwidth is 310 Mb/s, usually 305") for ln in lines)
    assert any(ln.startswith("Where the problem is: The tunnel is healthy") and "BEYOND the tunnel" in ln for ln in lines) and said[-1][1:] == (True, True)

    with session() as db:
        ux = db.get(Tunnel, w["tunnel"]).record["ux"]
        said_events = [e.text for e in db.scalars(select(Event).where(Event.ref == "nl1-ir-alpha"))]

    assert ux["why"]["code"] == "beyond" and ux["grade"] == "poor" and any("its users suffer - The tunnel is healthy" in m for m in said_events)
    _ = now


def test_every_new_speed_test_of_the_probe_joins_the_history_of_its_config(monkeypatch):
    answer = {"configs": [{"name": "France", "slug": "france", "port": 11001, "enabled": True, "status": "up", "ping": 480.0, "checked_at": 1, "speed": {"ok": True, "download": 55.0, "upload": 9.0, "timestamp": "2026-10-16T06:33:18+00:00"}}]}
    monkeypatch.setattr(probe, "health", lambda: answer)
    configs._sync()
    configs._sync()                                                                                           # the same test read twice: counted once
    answer["configs"][0]["speed"] = {"ok": True, "download": 61.0, "upload": 10.0, "timestamp": "2026-10-16T06:43:44+00:00"}
    configs._sync()

    with session() as db:
        c = db.scalar(select(Config).where(Config.name == "France"))
        hist = c.meta["speed_hist"]

    assert [h[1] for h in hist] == [55.0, 61.0] and c.down_mbps == 61.0
    for i in range(40):
        answer["configs"][0]["speed"] = {"ok": True, "download": 50.0 + i, "upload": 1.0, "timestamp": f"2026-10-17T06:{i:02d}:00+00:00"}
        configs._sync()

    with session() as db:
        assert len(db.scalar(select(Config).where(Config.name == "France")).meta["speed_hist"]) == 30           # the last thirty


def test_the_list_of_tunnels_carries_the_grade_without_the_pending_look(client):
    w = world()

    with session() as db:
        t = db.get(Tunnel, w["tunnel"])
        t.record = {"ux": {"grade": "fair", "n": 3, "known": 3, "bad": [], "pend": {"grade": "fair", "n": 1}}, "port_mapping": "80"}

    row = next(x for x in client.get("/api/tunnels").json() if x["id"] == w["tunnel"])
    assert row["ux"]["grade"] == "fair" and "pend" not in row["ux"] and "ux" not in row["record"] and row["record"]["port_mapping"] == "80"

def test_the_links_are_read_live_and_the_doctor_carries_them(client, monkeypatch):
    w = world()
    monkeypatch.setitem(dnscache.CACHE, "rows", [])
    assert userq.linked_state() == {"total": 3, "linked": 1, "unlinked": ["France", "Italy"]}                 # "Direct" has the address of the Iran server; the CDN name has no A record in the panel
    monkeypatch.setitem(dnscache.CACHE, "rows", [{"fqdn": "gt.cdn.example.com", "type": "A", "ips": ["203.0.113.10"], "port": None}])
    assert userq.linked_state() == {"total": 3, "linked": 3, "unlinked": []}                                  # the record of the CDN name finds its Iran server
    monkeypatch.setitem(dnscache.CACHE, "rows", [])

    with session() as db:                                                                                     # a label put by hand links a config (and a switched-off config is not counted)
        db.get(Config, w["configs"]["France"]).tunnel = "nl1-ir-alpha"
        db.get(Config, w["configs"]["Italy"]).enabled = False

    assert userq.linked_state() == {"total": 2, "linked": 2, "unlinked": []}
    j = client.get("/api/doctor").json()
    assert j["users"] == {"total": 2, "linked": 2, "unlinked": []}
