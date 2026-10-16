"""The bandwidth of a tunnel, kept by the Doctor (services/bandwidth.py): the usual level, the verdict, the drop and its confirmation, the one sentence that says where the problem is,
the light measurement of a GRE tunnel (a sink on the foreign server, one connection over the link), the job, the API and the way the probe script takes the new arguments."""
import json
import time

import pytest
from sqlalchemy import select

from nikatel.config import get_settings
from nikatel.db import session
from nikatel.models import Event, Server, ServerSample, Tunnel
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
    return {"down": down, "at": NOW - 5400, "ref": down, "state": "ok", "drop": None, "suspect": False, "next_at": NOW - 10, "hist": hist_of([down] * n, 5400)}


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
    assert probe["HOST"] == peer and probe["PORT"] == "39777" and probe["STREAMS"] == "1" and probe["DIR"] == "d" and probe["BYTES"] == "40000000" and probe["KEY"] == sink["KEY"]

    with session() as db:
        t = db.get(Tunnel, tid)
        assert t.record["bw_sink"] == {"name": f"bw{tid}", "ip": peer, "port": 39777} and t.secrets["bw_key"] == sink["KEY"]

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
