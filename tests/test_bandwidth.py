"""The bandwidth of a tunnel, kept by the Doctor (services/bandwidth.py): the usual level, the verdict, the drop and its confirmation, the one sentence that says where the problem is,
the light measurement of a GRE tunnel (a sink on the foreign server, one connection over the link), the job, the API and the way the probe script takes the new arguments."""
import json
import time

import pytest
from sqlalchemy import select

from nikatel.config import get_settings
from nikatel.db import session
from nikatel.models import Event, Server, ServerSample, Tunnel, TunnelCheck
from nikatel.services import autopilot, bandwidth
from nikatel.ssh import SCRIPTS, SSHError, render_action

NOW = 1_800_000_000


def hist_of(values, step=3600):
    """A history of samples, the newest last."""
    return [[NOW - (len(values) - i) * step, v, None] for i, v in enumerate(values)]


def test_the_usual_level_is_the_third_quarter_of_the_last_week_and_needs_four_samples():
    assert bandwidth.reference([], NOW) is None and bandwidth.reference(hist_of([100, 100, 100]), NOW) is None
    assert bandwidth.reference(hist_of([100, 200, 300, 400]), NOW) == 300.0                         # 75th percentile of 100 200 300 400: index round(0.75 * 3) = 2
    assert bandwidth.reference(hist_of([310, 300, 305, 90, 320, 315, 100, 308]), NOW) == 310.0      # a bad hour or two does not lower what the tunnel usually gives
    old = [[NOW - 8 * 86400, 900.0, None]] * 10                                                      # a week old: not counted
    assert bandwidth.reference(old + hist_of([100, 100, 100, 100]), NOW) == 100.0


def test_the_verdict_says_ok_low_or_drop_against_the_usual_level():
    assert bandwidth.verdict(None, 300) == "none" and bandwidth.verdict(250, None) == "learning"
    assert bandwidth.verdict(300, 300) == "ok" and bandwidth.verdict(250, 300) == "ok" and bandwidth.verdict(230, 300) == "low" and bandwidth.verdict(179, 300) == "drop"
    assert bandwidth.verdict(5, 15) == "ok"                                                         # a tunnel that never gave more than 15 Mb/s: its noise is not a drop


def test_a_drop_needs_two_samples_and_ends_when_the_level_is_back():
    bw = None

    for i, v in enumerate([300, 310, 305, 315]):                                                     # four good samples teach the level
        bw, change = bandwidth.add_sample(bw, NOW + i * 5400, v, None, "link")
        assert change == "" and bw["state"] in ("learning", "ok")

    t = NOW + 4 * 5400
    assert bw["ref"] == 310.0 and bw["state"] == "learning"                                         # (the last of the four was judged before the level was known)
    bw, change = bandwidth.add_sample(bw, t, 90, None, "link")                                        # the first sample under the line: measured again soon, nobody is told
    assert change == "" and bw["suspect"] is True and bw["drop"] is None and bw["next_at"] == t + bandwidth.CONFIRM_AFTER and bw["state"] == "ok"
    bw, change = bandwidth.add_sample(bw, t + 180, 95, None, "link")                                  # the second: a drop, with what it was and what it is
    assert change == "drop" and bw["state"] == "drop" and bw["drop"] == {"since": t + 180, "from": 310.0, "now": 95.0} and bw["suspect"] is False and bw["next_at"] == t + 180 + bandwidth.WATCH_AFTER
    bw, change = bandwidth.add_sample(bw, t + 1380, 100, None, "link")                                # still down: the number follows, nothing new is announced
    assert change == "" and bw["drop"]["now"] == 100.0 and bw["drop"]["since"] == t + 180
    bw, change = bandwidth.add_sample(bw, t + 2580, 200, None, "link")                                # better, but under 80 %: the drop stays
    assert change == "" and bw["state"] == "drop"
    bw, change = bandwidth.add_sample(bw, t + 3780, 290, None, "link")                                # over 80 %: recovered
    assert change == "recovered" and bw["state"] == "ok" and bw["drop"] is None
    one, c = bandwidth.add_sample(None, t, 90, None, "link")                                          # a tunnel that is new: nothing to compare with
    assert c == "" and one["state"] == "learning" and one["ref"] is None
    bw2, _ = bandwidth.add_sample(bw, t + 5000, 305, None, "link")                                    # a sample that is only a little low is "low", not a drop
    bw2, c = bandwidth.add_sample(bw2, t + 6000, 230, None, "link")
    assert c == "" and bw2["state"] == "low" and bw2["drop"] is None and bw2["suspect"] is False


def test_the_history_keeps_sixty_samples_of_the_last_week():
    bw = None

    for i in range(75):
        bw, _ = bandwidth.add_sample(bw, NOW + i * 600, 200.0 + i % 5, None, "link")

    assert len(bw["hist"]) == bandwidth.KEEP == 60 and bw["hist"][-1][0] == NOW + 74 * 600


def test_the_one_sentence_names_the_most_likely_place_of_the_problem():
    base = {"now": 90.0, "ref": 310.0, "loss": 0.0, "live": 5.0, "iran": {"name": "ir1", "cpu": 20}, "foreign": {"name": "fr1", "cpu": 30},
            "rtt": 80.0, "rtt_ref": 78.0, "same_iran": {"total": 4, "dropped": 1}, "same_foreign": {"total": 1, "dropped": 1}}
    code, text = bandwidth.explain(base)
    assert code == "this_only" and "Only this tunnel is slower" in text and "90 Mb/s, usually 310" in text
    code, text = bandwidth.explain({**base, "iran": {"name": "ir1", "cpu": 93}})
    assert code == "iran_busy" and "ir1" in text and "93 %" in text
    assert bandwidth.explain({**base, "foreign": {"name": "fr1", "cpu": 91}})[0] == "foreign_busy"
    code, text = bandwidth.explain({**base, "same_iran": {"total": 4, "dropped": 3}})
    assert code == "iran_wide" and "3 of the 4 tunnels" in text and "ir1" in text
    assert bandwidth.explain({**base, "same_iran": {"total": 2, "dropped": 1}})[0] == "this_only"                       # one of two is not "together"
    code, text = bandwidth.explain({**base, "same_foreign": {"total": 3, "dropped": 2}})
    assert code == "foreign_wide" and "fr1" in text
    code, text = bandwidth.explain({**base, "live": 80.0})
    assert code == "full" and "full of users" in text
    assert bandwidth.explain({**base, "loss": 12.0})[0] == "loss" and "12 %" in bandwidth.explain({**base, "loss": 12.0})[1]
    code, text = bandwidth.explain({**base, "rtt": 150.0})
    assert code == "latency" and "78 to 150 ms" in text
    assert bandwidth.explain({**base, "loss": None, "rtt": None, "rtt_ref": None})[0] == "this_only"                      # what cannot be measured explains nothing
    assert bandwidth.explain({**base, "iran": {"name": "ir1", "cpu": 95}, "loss": 20.0})[0] == "iran_busy"                # the cheapest explanation first


# ------------------------------------------------------------------ a tunnel in the database
HOSTS = iter(range(10, 200))


def make_tunnel(name="gr-a", typ="backhaul", state="up", door=False, bw=None, record=None, secrets=None):
    n = next(HOSTS)

    with session() as db:
        ir, fr = Server(name="ir-" + name, host=f"203.0.113.{n}", role="iran", online=True), Server(name="fr-" + name, host=f"198.51.100.{n}", role="foreign", online=True)
        db.add_all([ir, fr])
        db.flush()
        rec = {**({"test_port": 39100} if door else {}), **({"bw": bw} if bw else {}), **(record or {})}
        t = Tunnel(name=name, type=typ, transport="tcpmux", port=9901, iran_server_id=ir.id, foreign_server_id=fr.id, state=state, forwarded_ports=[8076], foreign_tunnel_ip=f"10.2.{n}.2" if not door else "",
                   record=rec, secrets={**({"test_key": "k" * 16} if door else {}), **(secrets or {})})
        db.add(t)
        db.flush()
        return t.id, f"10.2.{n}.2", ir.id, fr.id


def good_history(down=300.0, n=6):
    return {"m": bandwidth.METHOD, "how": "link", "down": down, "at": NOW - 5400, "ref": down, "state": "ok", "drop": None, "suspect": False, "next_at": NOW - 10, "hist": hist_of([down] * n, 5400)}


def test_a_sample_is_stored_with_an_event_when_a_drop_is_confirmed_and_when_it_ends():
    tid, _, _, _ = make_tunnel(bw=good_history())
    res = bandwidth._store(tid, 80.0, None, "link", 81.0)
    assert res[1] == "" and res[0]["suspect"] is True
    res = bandwidth._store(tid, 85.0, None, "link", 81.0)
    assert res[1] == "drop"

    with session() as db:
        rec = db.get(Tunnel, tid).record
        said = [e.text for e in db.scalars(select(Event).where(Event.domain == "tunnels"))]

    assert rec["bw"]["state"] == "drop" and rec["bw"]["drop"]["from"] == 300.0 and rec["bw"]["down"] == 85.0 and rec["bw"]["ping"] == 81.0
    assert sum("bandwidth dropped to 85.0 Mb/s (usually 300.0)" in m for m in said) == 1
    assert bandwidth._store(tid, 290.0, None, "link", 80.0)[1] == "recovered"

    with session() as db:
        assert any("bandwidth is back" in e.text for e in db.scalars(select(Event).where(Event.domain == "tunnels")))

    assert bandwidth._store(99999, 1.0, None, "link", None) is None                                   # a tunnel that is gone


def test_the_quality_job_feeds_the_bandwidth_of_a_tunnel_with_a_test_door(monkeypatch):
    tid, _, _, _ = make_tunnel("bp-a", "backpack", door=True)
    started = []
    monkeypatch.setattr(bandwidth, "_investigate_later", lambda t: started.append(t))
    bandwidth.note_quality(tid, {"down_mbps": None})                                                   # nothing measured: nothing stored
    bandwidth.note_quality(tid, {"down_mbps": 120.5, "up_mbps": 30.0, "latency_ms": 85.0})

    with session() as db:
        bw = db.get(Tunnel, tid).record["bw"]

    assert bw["down"] == 120.5 and bw["up"] == 30.0 and bw["how"] == "door" and bw["state"] == "learning" and started == []


def fake_world(monkeypatch, answers=None, sink="SINK_OK"):
    """The foreign server's sink and the Iran server's probe, recorded: (the calls, the answers still to give)."""
    calls = []
    answers = list(answers if answers is not None else [{"latency_ms": 81.0, "loss_pct": 0.0, "down_mbps": 310.5}])

    def run_action(node, name, params=None, timeout=60):
        calls.append(("action", name, dict(params or {})))
        return sink

    def run_script(node, name, params=None, timeout=40):
        calls.append(("script", name, dict(params or {})))
        a = answers.pop(0) if answers else {"error": "refused"}

        if isinstance(a, Exception):
            raise a

        return "noise\nNKPROBE " + json.dumps(a) + "\n"

    monkeypatch.setattr(bandwidth, "run_action", run_action)
    monkeypatch.setattr(bandwidth, "run_script", run_script)
    monkeypatch.setattr(bandwidth, "_server_busy", lambda sid, now: False)
    monkeypatch.setattr(get_settings(), "read_only", False)
    return calls


def test_a_gre_tunnel_is_measured_over_its_link_through_a_sink_on_the_inner_address(monkeypatch):
    tid, peer, _, _ = make_tunnel()
    calls = fake_world(monkeypatch, answers=[{"latency_ms": 81.0, "loss_pct": 0.0, "down_mbps": 310.5}, {"latency_ms": 80.0, "loss_pct": 0.0, "down_mbps": 305.0}])
    bw, change = bandwidth.measure(tid)
    assert bw["down"] == 310.5 and bw["how"] == "link" and bw["state"] == "learning" and change == "" and bw["ping"] == 81.0
    sink = next(c for c in calls if c[1] == "sink_install")[2]
    assert sink["NAME"] == f"bw{tid}" and sink["PORT"] == "39777" and sink["BIND"] == peer and len(sink["KEY"]) == 16                       # (bound to the inner address: reachable only through the link)
    probe = next(c for c in calls if c[1] == "probe_run")[2]
    assert probe["HOST"] == peer and probe["PORT"] == "39777" and probe["STREAMS"] == "2" and probe["SECS"] == "3" and probe["DIR"] == "d" and probe["BYTES"] == "60000000" and probe["KEY"] == sink["KEY"]

    with session() as db:
        t = db.get(Tunnel, tid)
        assert t.record["bw_sink"] == {"name": f"bw{tid}", "ip": peer, "port": 39777, "v": bandwidth.SINK_V} and t.secrets["bw_key"] == sink["KEY"]

    calls.clear()
    bandwidth.measure(tid)                                                                               # the second time the sink is there: only the probe runs
    assert [c[1] for c in calls] == ["probe_run"]


def test_a_sink_that_died_is_put_back_once_and_a_tunnel_that_cannot_be_reached_says_so(monkeypatch):
    tid, _, _, _ = make_tunnel()
    calls = fake_world(monkeypatch, answers=[{"error": "connection refused"}, {"latency_ms": 90.0, "loss_pct": 0.0, "down_mbps": 250.0}])
    bw, _ = bandwidth.measure(tid)
    assert bw["down"] == 250.0 and [c[1] for c in calls] == ["sink_install", "probe_run", "sink_install", "probe_run"]               # first install, the probe fails, install again, the probe passes
    calls = fake_world(monkeypatch, answers=[{"error": "connection refused"}, {"error": "connection refused"}])

    with pytest.raises(RuntimeError, match="does not answer through the link"):
        bandwidth.measure(tid)

    calls = fake_world(monkeypatch, answers=[SSHError("timeout after 45s"), {"latency_ms": 90.0, "loss_pct": 0.0, "down_mbps": 200.0}])
    assert bandwidth.measure(tid)[0]["down"] == 200.0

    fake_world(monkeypatch, sink="SINK_DOWN")
    t2, _, _, _ = make_tunnel("gr-b")

    with pytest.raises(RuntimeError, match="did not start"):
        bandwidth.measure(t2)


def test_what_cannot_be_measured_is_refused_with_a_sentence(monkeypatch):
    fake_world(monkeypatch)
    down, _, _, _ = make_tunnel("gr-d", state="down")
    gre, _, _, _ = make_tunnel("gr-e")
    nolink, _, _, _ = make_tunnel("pk-a", "paqet")                                                       # no GRE link and no test door

    with pytest.raises(ValueError, match="not up"):
        bandwidth.measure(down)

    with pytest.raises(ValueError, match="no GRE link or test door"):
        bandwidth.measure(nolink)

    monkeypatch.setattr(bandwidth, "_server_busy", lambda sid, now: True)

    with pytest.raises(ValueError, match="busy"):
        bandwidth.measure(gre)

    monkeypatch.setattr(bandwidth, "_server_busy", lambda sid, now: False)
    monkeypatch.setattr(get_settings(), "read_only", True)

    with pytest.raises(ValueError, match="read-only"):
        bandwidth.measure(gre)

    with pytest.raises(ValueError, match="not found"):
        bandwidth.measure(424242)


def test_a_server_that_is_busy_is_not_measured():
    _, _, ir, fr = make_tunnel("gr-f")
    now = int(time.time())

    with session() as db:
        db.add_all([ServerSample(server_id=ir, ts=now - 30, cpu=92.0, ram=40.0, disk=30.0, load1=0.5, rx_mbps=1.0, tx_mbps=2.0, conn=7), ServerSample(server_id=fr, ts=now - 30, cpu=10.0, ram=40.0, disk=30.0, load1=0.5, rx_mbps=1.0, tx_mbps=2.0, conn=7)])

    assert bandwidth._server_busy(ir, now) is True and bandwidth._server_busy(fr, now) is False
    assert bandwidth._server_busy(ir, now + 3600) is False                                               # an old sample says nothing


def test_the_job_measures_one_tunnel_per_turn_the_one_that_waited_longest(monkeypatch):
    old, _, _, _ = make_tunnel("gr-old", bw={**good_history(), "at": NOW - 9000, "next_at": NOW - 3000})
    new, _, _, _ = make_tunnel("gr-new", bw={**good_history(), "at": NOW - 7000, "next_at": NOW - 500})
    wait, _, _, _ = make_tunnel("gr-wait", bw={**good_history(), "at": NOW - 100, "next_at": NOW + 5000})
    never, _, _, _ = make_tunnel("gr-never")
    make_tunnel("pk-b", "paqet")                                                                           # no link: not this job's
    make_tunnel("gr-down", state="down")
    monkeypatch.setattr(bandwidth.time, "time", lambda: NOW)
    monkeypatch.setattr(bandwidth.jobs, "stopping", lambda: False)                                       # (an earlier test that shut the app down leaves the flag set)
    assert bandwidth._due(NOW) == [never, old, new]                                                        # never measured first, then the oldest; the one that must wait stays out
    done = []
    monkeypatch.setattr(bandwidth, "measure", lambda tid: done.append(tid) or ({"down": 300.0, "ref": 300.0, "state": "ok"}, ""))
    monkeypatch.setattr(get_settings(), "read_only", False)
    bandwidth.run()
    assert done == [never]
    monkeypatch.setattr(get_settings(), "read_only", True)
    bandwidth.run()
    assert done == [never]                                                                                 # read-only: nothing is put on a server
    _ = wait


def test_a_tunnel_that_could_not_be_measured_is_left_alone_for_a_while_and_a_drop_is_looked_at(monkeypatch):
    bad, _, _, _ = make_tunnel("gr-bad")
    monkeypatch.setattr(get_settings(), "read_only", False)

    def boom(tid):
        raise ValueError("a server of this tunnel is busy right now")

    monkeypatch.setattr(bandwidth, "measure", boom)
    monkeypatch.setattr(bandwidth.time, "time", lambda: NOW)
    monkeypatch.setattr(bandwidth.jobs, "stopping", lambda: False)
    bandwidth.run()

    with session() as db:
        bw = db.get(Tunnel, bad).record["bw"]

    assert bw["next_at"] == NOW + bandwidth.RETRY_AFTER and "busy" in bw["error"]
    assert bandwidth._due(NOW) == []                                                                       # (and it is not asked again at once)
    looked = []
    monkeypatch.setattr(bandwidth, "measure", lambda tid: ({"down": 90.0, "ref": 300.0, "state": "drop"}, "drop"))
    monkeypatch.setattr(bandwidth, "_investigate_later", lambda tid: looked.append(tid))
    monkeypatch.setattr(bandwidth.time, "time", lambda: NOW + 10_000)
    bandwidth.run()
    assert looked == [bad]


def test_the_look_at_a_drop_writes_the_story_and_keeps_the_sentence(monkeypatch):
    tid, _, ir, _ = make_tunnel("gr-g", bw={**good_history(), "state": "drop", "down": 90.0, "drop": {"since": NOW - 600, "from": 300.0, "now": 90.0}})
    other, _, _, _ = make_tunnel("gr-h", bw={**good_history(), "state": "ok"})
    now = int(time.time())

    with session() as db:
        db.add(ServerSample(server_id=ir, ts=now - 20, cpu=96.0, ram=30.0, disk=30.0, load1=0.5, rx_mbps=1.0, tx_mbps=2.0, conn=7))
        t = db.get(Tunnel, tid)
        t.rx_mbps, t.rtt_ms, t.detail = 4.0, 82.0, {"loss_pct": 0.0}

    said = []
    monkeypatch.setattr(autopilot, "_say", lambda aid, line, done=None, ok=None: said.append((line, done, ok)))
    bandwidth.investigate(7, tid)
    lines = [s[0] for s in said]
    assert lines[0].startswith("gr-g: the bandwidth dropped to 90.0 Mb/s; it usually gives 300.0")
    assert any(ln.startswith("✕ The iran server ir-gr-g: CPU 96 %") for ln in lines) and any(ln.startswith("✓ The foreign server") for ln in lines)
    assert any("Where the problem is: The Iran server ir-gr-g is busy (CPU 96 %)" in ln for ln in lines) and said[-1][1:] == (True, True)

    with session() as db:
        why = db.get(Tunnel, tid).record["bw"]["why"]
        said_events = [e.text for e in db.scalars(select(Event).where(Event.ref == "gr-g"))]

    assert why["code"] == "iran_busy" and "busy" in why["text"] and any("bandwidth dropped - The Iran server" in m for m in said_events)
    _ = other


def test_the_measure_button_runs_as_an_action_and_the_api_shows_the_bandwidth(client, monkeypatch):
    tid, _, _, _ = make_tunnel("gr-i", bw=good_history())
    j = client.get(f"/api/tunnels/{tid}/bandwidth").json()
    assert j["ok"] and j["name"] == "gr-i" and j["down"] == 300.0 and len(j["hist"]) == 6 and j["state"] == "ok"
    assert client.get("/api/tunnels/9999/bandwidth").status_code == 404
    listed = next(t for t in client.get("/api/tunnels").json() if t["id"] == tid)
    assert listed["bw"]["down"] == 300.0 and "hist" not in listed["bw"] and "bw" not in listed["record"] and "quality" not in listed["record"]          # the list carries the number, not the history
    monkeypatch.setattr(bandwidth, "measure_start", lambda t: 55)
    assert client.post(f"/api/tunnels/{tid}/bandwidth").json() == {"ok": True, "action_id": 55}
    monkeypatch.setattr(bandwidth, "measure_start", lambda t: (_ for _ in ()).throw(ValueError("tunnel not found")))
    assert client.post("/api/tunnels/9999/bandwidth").status_code == 400


def test_the_measure_story_says_what_it_found_and_looks_at_a_drop_in_the_same_story(monkeypatch):
    tid, _, _, _ = make_tunnel("gr-j", bw=good_history())
    said, looked = [], []
    monkeypatch.setattr(autopilot, "_say", lambda aid, line, done=None, ok=None: said.append(line))
    monkeypatch.setattr(autopilot, "_start", lambda label, fn, *a, **k: fn(1, *a))                      # (run the work here, not in a thread)
    monkeypatch.setattr(bandwidth, "measure", lambda t: ({"down": 305.0, "ref": 300.0, "state": "ok"}, ""))
    bandwidth.measure_start(tid)
    assert said[-1] == "Done." and any(ln.startswith("Bandwidth: 305.0 Mb/s, usually 300") for ln in said)
    said.clear()
    monkeypatch.setattr(bandwidth, "measure", lambda t: ({"down": 80.0, "ref": 300.0, "state": "drop"}, "drop"))
    monkeypatch.setattr(bandwidth, "investigate", lambda aid, t, finish=True: looked.append((aid, t, finish)))
    bandwidth.measure_start(tid)
    assert looked == [(1, tid, True)] and any("usually 300" in ln for ln in said)

    with pytest.raises(ValueError, match="not found"):
        bandwidth.measure_start(8765)


def test_a_tunnel_that_is_removed_takes_its_bandwidth_sink_with_it(monkeypatch):
    from nikatel.services import builder

    tid, _, _, _ = make_tunnel("gr-k", record={"bw_sink": {"name": "bw1", "ip": "10.2.1.2", "port": 39777}})
    other, _, _, _ = make_tunnel("gr-l")
    ran = []
    monkeypatch.setattr(get_settings(), "read_only", False)
    monkeypatch.setattr(builder, "run_action", lambda node, name, params=None, timeout=90: ran.append((node.host, name, dict(params or {}))) or "REMOVED")
    monkeypatch.setattr(bandwidth, "run_action", lambda node, name, params=None, timeout=60: ran.append((node.host, name, dict(params or {}))) or "REMOVED")
    builder.delete_tunnel(tid)
    removed = [r for r in ran if r[1] == "bw_sink_remove"]
    assert len(removed) == 1 and removed[0][0].startswith("198.51.100.") and removed[0][2] == {"NAME": f"bw{tid}"}                  # on the foreign server, by the name the sink was given
    ran.clear()
    builder.delete_tunnel(other)
    assert not [r for r in ran if r[1] == "bw_sink_remove"]                                              # a tunnel that never had a sink: nothing to remove


def test_the_probe_script_takes_a_host_a_byte_limit_and_a_direction():
    script = SCRIPTS["probe_run"][0]
    rules = SCRIPTS["probe_run"][1]
    assert "'@HOST@' '@BYTES@' '@DIR@'" in script and "socket.create_connection((H, P)" in script and "(not LIM or n < LIM)" in script and "(not LIM or sent < LIM)" in script
    assert '"d" not in DIRS' in script and '"u" not in DIRS' in script and 'H = (sys.argv[7] if len(sys.argv) > 7 else "") or "127.0.0.1"' in script
    import re

    assert all(re.fullmatch(rules[k], v) for k, v in (("HOST", ""), ("HOST", "10.2.1.2"), ("BYTES", ""), ("BYTES", "40000000"), ("DIR", ""), ("DIR", "d"), ("DIR", "du")))
    assert not any(re.fullmatch(rules[k], v) for k, v in (("HOST", "example.com"), ("HOST", "1.2.3"), ("BYTES", "4e7"), ("DIR", "x"), ("DIR", "dd")))
    text = render_action("sink_install", {"NAME": "bw7", "PORT": "39777", "KEY": "abcdef0123456789", "BIND": "10.2.1.2"})
    assert "ExecStart=/usr/bin/python3 /usr/local/sbin/nikatel-sink-bw7.py 39777 abcdef0123456789 10.2.1.2" in text
    gone = render_action("bw_sink_remove", {"NAME": "bw7"})
    assert "n='bw7'" in gone and "nikatel-sink-$n.service" in gone and "nikatel-sink-$n.py" in gone

    with pytest.raises(ValueError):
        render_action("bw_sink_remove", {"NAME": "other1"})

    with pytest.raises(ValueError):
        render_action("sink_install", {"NAME": "bw7", "PORT": "39777", "KEY": "abcdef0123456789", "BIND": "10.2.1.2; id"})


def test_a_sample_of_another_method_or_another_kind_of_tunnel_starts_the_history_again():
    """Real run, 2026-10-16: 1.60.1 measures with two connections, 1.60.0 with one: the two scales must never be mixed (a mixed level made a tunnel that was back look dropped)."""
    old = {**good_history(300.0), "m": 1}
    bw, change = bandwidth.add_sample(old, NOW, 600.0, None, "link")
    assert change == "" and len(bw["hist"]) == 1 and bw["state"] == "learning" and bw["ref"] is None and bw["m"] == bandwidth.METHOD       # the old samples are forgotten
    nomark = {k: v for k, v in good_history(300.0).items() if k != "m"}
    assert len(bandwidth.add_sample(nomark, NOW, 600.0, None, "link")[0]["hist"]) == 1                                                        # (a record without a method is the first one)
    kept = bandwidth.add_sample(good_history(300.0), NOW, 290.0, None, "link")[0]
    assert len(kept["hist"]) == 7 and kept["state"] == "ok"                                                                                  # the same method: the history goes on
    door = bandwidth.add_sample(good_history(300.0), NOW, 120.0, None, "door")[0]
    assert len(door["hist"]) == 1 and door["how"] == "door" and door["state"] == "learning"                                                  # a tunnel rebuilt as another kind: another scale


def test_one_tunnel_is_never_measured_twice_at_once(monkeypatch):
    tid, _, _, _ = make_tunnel("gr-m")
    inside = []

    def slow(t):
        inside.append(t)

        with pytest.raises(ValueError, match="being measured right now"):
            bandwidth.measure(t)                                                                          # (a click while the job measures it)

        return {"down": 1.0}, ""

    monkeypatch.setattr(bandwidth, "_measure", slow)
    assert bandwidth.measure(tid) == ({"down": 1.0}, "") and inside == [tid]
    assert bandwidth.measure(tid) == ({"down": 1.0}, "")                                                  # (and it is free again afterwards)


def test_the_traffic_users_carry_is_the_middle_of_the_last_ten_minutes():
    """Real run, 2026-10-16: the Doctor called a tunnel "full of users" because the minute that held five tests of the panel itself averaged 104 Mb/s."""
    tid, _, _, _ = make_tunnel("gr-n", bw={**good_history(), "state": "drop", "down": 90.0, "drop": {"since": NOW - 600, "from": 300.0, "now": 90.0}})
    now = int(time.time())

    with session() as db:
        for i, v in enumerate([2.0, 3.0, 400.0, 2.5, 3.5, 2.8]):                                         # one minute holds a test of the panel itself
            db.add(TunnelCheck(tunnel_id=tid, ts=now - 60 * i, ok=True, rtt_ms=80.0, rx_mbps=v, tx_mbps=1.0, error=""))

        db.add(TunnelCheck(tunnel_id=tid, ts=now - 3000, ok=True, rtt_ms=80.0, rx_mbps=900.0, tx_mbps=1.0, error=""))        # (an hour old: not counted)

    assert bandwidth._facts(tid)["live"] == 3.0
    other, _, _, _ = make_tunnel("gr-o", bw={**good_history(), "state": "drop", "down": 90.0, "drop": {"since": NOW - 600, "from": 300.0, "now": 90.0}})

    with session() as db:
        db.add_all([TunnelCheck(tunnel_id=other, ts=now - 60 * i, ok=True, rtt_ms=80.0, rx_mbps=50.0, tx_mbps=1.0, error="") for i in range(4)])

    assert bandwidth._facts(other)["live"] is None                                                       # fewer than five readings: nothing is said about the users


# ------------------------------------------------------------------ a tunnel that answers every check and moves nothing (the owner, 2026-10-16: "it shows connected but has no bandwidth")
def test_a_test_that_moved_nothing_is_a_stall_with_or_without_a_usual_level_and_a_sign_of_life_ends_it():
    assert bandwidth.verdict(0.0, None) == "drop" and bandwidth.verdict(0.2, 300) == "drop" and bandwidth.verdict(0.6, None) == "learning" and bandwidth.verdict(5, 15) == "ok"
    bw, change = bandwidth.add_sample(None, NOW, 0.0, None, "door")                                  # the first test: measured again soon before anybody is told
    assert change == "" and bw["suspect"] is True and bw["drop"] is None and bandwidth.stalled(bw, NOW + 10) == ""
    bw, change = bandwidth.add_sample(bw, NOW + 180, 0.0, None, "door")
    assert change == "drop" and bw["state"] == "drop" and bw["drop"] == {"since": NOW + 180, "from": 0.0, "now": 0.0}
    assert bandwidth.stalled(bw, NOW + 200) == "no data moves through the tunnel under load: the last tests carried 0 Mb/s"
    assert bandwidth.stalled(bw, NOW + 180 + bandwidth.STALL_FRESH + 1) == "" and bandwidth.stalled(None, NOW) == "" and bandwidth.stalled({}, NOW) == ""        # an old stall says nothing about now
    bw, change = bandwidth.add_sample(bw, NOW + 1500, 3.0, None, "door")                              # 3 Mb/s: almost nothing, the drop stays (but it is no stall any more)
    assert change == "" and bw["drop"] is not None and bandwidth.stalled(bw, NOW + 1500) == ""
    bw, change = bandwidth.add_sample(bw, NOW + 2700, 40.0, None, "door")                             # a sign of life, though the usual level is not known yet
    assert change == "recovered" and bw["drop"] is None


def test_the_look_at_a_stall_says_the_data_path_is_stalled():
    base = {"now": 0.0, "ref": 0.0, "loss": 0.0, "live": 0.0, "iran": {"name": "ir1", "cpu": 20}, "foreign": {"name": "fr1", "cpu": 30}, "rtt": 80.0, "rtt_ref": 78.0, "same_iran": {"total": 3, "dropped": 1}, "same_foreign": {"total": 1, "dropped": 1}}
    code, text = bandwidth.explain(base)
    assert code == "stalled" and "no data at all" in text and "stalled" in text and "another tunnel type" in text
    assert bandwidth.explain({**base, "iran": {"name": "ir1", "cpu": 95}})[0] == "iran_busy"          # (a server that is on its knees explains it first)


def test_a_stalled_tunnel_is_measured_again_after_its_repair_and_only_then(monkeypatch):
    stalled, _, _, _ = make_tunnel("st-a", bw={**good_history(), "down": 0.0, "state": "drop", "drop": {"since": int(time.time()) - 300, "from": 300.0, "now": 0.0}, "at": int(time.time()) - 200})
    fine, _, _, _ = make_tunnel("st-b", bw=good_history())
    done = []
    monkeypatch.setattr(bandwidth, "measure", lambda tid: done.append(tid) or ({}, ""))
    bandwidth.recheck(stalled)
    bandwidth.recheck(fine)
    bandwidth.recheck(424242)
    assert done == [stalled]                                                                           # a tunnel that is not stalled, or not there, is left alone
    monkeypatch.setattr(bandwidth, "measure", lambda tid: (_ for _ in ()).throw(ValueError("a server of this tunnel is busy right now")))
    bandwidth.recheck(stalled)                                                                         # (a refusal is only logged)


# ------------------------------------------------------------------ the first round, the tunnels with a door, the loss counted with UDP
def test_the_first_round_measures_a_few_tunnels_that_were_never_measured_in_one_turn(monkeypatch):
    fresh = [make_tunnel(f"fb-{c}")[0] for c in "abcde"]
    old, _, _, _ = make_tunnel("fb-old", bw={**good_history(), "at": NOW - 9000, "next_at": NOW - 3000})
    done = []
    monkeypatch.setattr(bandwidth, "measure", lambda tid: done.append(tid) or ({"down": 300.0, "ref": 300.0, "state": "ok"}, ""))
    monkeypatch.setattr(bandwidth.time, "time", lambda: NOW)
    monkeypatch.setattr(bandwidth.jobs, "stopping", lambda: False)
    monkeypatch.setattr(bandwidth, "_server_busy", lambda sid, now: False)
    monkeypatch.setattr(get_settings(), "read_only", False)
    bandwidth.run()
    assert done == fresh[: bandwidth.FIRST_BATCH] and old not in done                                  # (a dash on the page for twenty minutes after an update was too long)


def test_a_tunnel_with_a_test_door_and_an_unstable_one_are_in_the_watch_too(monkeypatch):
    door, _, _, _ = make_tunnel("dw-door", "backpack", door=True)
    unstable, _, _, _ = make_tunnel("dw-deg", state="degraded")
    make_tunnel("dw-down", state="down")
    make_tunnel("dw-none", "paqet")                                                                    # no link and no door
    monkeypatch.setattr(bandwidth.time, "time", lambda: NOW)
    assert sorted(bandwidth._due(NOW)) == sorted([door, unstable])                                     # (the door is measured by its quality test; the job repeats it to confirm and to watch a drop)
    assert bandwidth._no_sample(door) is True and bandwidth._no_sample(424242) is False


def test_the_loss_of_a_link_that_drops_ping_is_counted_with_udp_packets_through_a_sink_that_echoes_them(monkeypatch):
    tid, _, _, _ = make_tunnel("gr-udp")
    calls = fake_world(monkeypatch, answers=[{"latency_ms": 81.0, "loss_pct": 0.0, "down_mbps": 310.5, "udp_loss_pct": 6.7}, {"latency_ms": 80.0, "loss_pct": 0.0, "down_mbps": 305.0}, {"latency_ms": 80.0, "loss_pct": 0.0, "down_mbps": 300.0}])
    bw, _ = bandwidth.measure(tid)
    probe = next(c for c in calls if c[1] == "probe_run")[2]
    assert probe["UDPN"] == str(bandwidth.UDP_PACKETS) == "30" and bw["loss"] == {"pct": 6.7, "at": bw["at"]}
    nxt, _ = bandwidth.add_sample(bw, bw["at"] + 600, 300.0, None, "link", 80.0, None)                 # a test without UDP keeps the number for a while ...
    assert nxt["loss"] == {"pct": 6.7, "at": bw["at"]}
    gone, _ = bandwidth.add_sample(bw, bw["at"] + bandwidth.LOSS_FRESH + 1, 300.0, None, "link", 80.0, None)
    assert gone["loss"] is None                                                                        # ... and then forgets it
    clean, _ = bandwidth.add_sample(bw, bw["at"] + 600, 300.0, None, "link", 80.0, 0.0)
    assert clean["loss"]["pct"] == 0.0                                                                 # a new number replaces it, a clean 0 included

    with session() as db:                                                                              # a sink of the old version has no UDP echo: it is put again
        t = db.get(Tunnel, tid)
        t.record = {**t.record, "bw_sink": {**t.record["bw_sink"], "v": 1}}

    calls.clear()
    bandwidth.measure(tid)
    assert [c[1] for c in calls] == ["sink_install", "probe_run"]
    calls.clear()
    bandwidth.measure(tid)
    assert [c[1] for c in calls] == ["probe_run"]                                                      # the new one stays


def test_the_probe_and_the_sink_echo_udp_and_take_the_new_argument():
    import re

    probe, rules = SCRIPTS["probe_run"]
    assert "'@UDPN@'" in probe and "UDPN = int(sys.argv[10] or 0)" in probe and 'out["udp_loss_pct"]' in probe and "import socket, threading, time, json, sys, os, select" in probe
    assert all(re.fullmatch(rules["UDPN"], v) for v in ("", "0", "30", "120")) and not any(re.fullmatch(rules["UDPN"], v) for v in ("1000", "3x", "-1", "30; id"))
    sink = render_action("sink_install", {"NAME": "bw7", "PORT": "39777", "KEY": "abcdef0123456789", "BIND": "10.2.1.2"})
    assert "def echo_udp():" in sink and "socket.SOCK_DGRAM" in sink and "if d[:len(KEY)] == KEY:" in sink and "u.sendto(d, a)" in sink