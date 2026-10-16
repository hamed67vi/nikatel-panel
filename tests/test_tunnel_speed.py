"""The speed a customer gets through a tunnel (the owner, 2026-10-16: "what speed does a user in Iran get - the closest possible to what the customer holds"):
through a config of the tunnel on the probe, or - without one - through the test door of the tunnel. Nothing here reaches a real server: the probe and the door are answered by fakes."""
import time

import pytest

from nikatel.db import session
from nikatel.models import Config, Server, Tunnel
from nikatel.services import build_other, probe
from nikatel.services import tunnel_speed as ts

PROBE = [{"name": "primary", "api_url": "http://probe.example.test:18080", "api_token": "t"}]
CAP = {"down_mbps": 400.0, "up_mbps": 380.0, "at": 1}


HOSTS = iter(range(10, 250))


def make_tunnel(name="tun-a", door=False, ports=(443,), cap=True):
    n = next(HOSTS)

    with session() as db:
        ir, fr = Server(name="ir-" + name, host=f"203.0.113.{n}", role="iran"), Server(name="fr-" + name, host=f"198.51.100.{n}", role="foreign")
        db.add_all([ir, fr])
        db.flush()
        rec = ({"test_port": 39100} if door else {}) | ({"quality": dict(CAP)} if cap else {})
        t = Tunnel(name=name, type="backpack" if door else "backhaul", iran_server_id=ir.id, foreign_server_id=fr.id, port=1, state="up", forwarded_ports=list(ports), record=rec, secrets={"test_key": "k" * 16} if door else {})
        db.add(t)
        db.flush()
        return t.id


def iran_host(tid):
    with session() as db:
        return db.get(Tunnel, tid).iran.host


def add_config(name, tunnel="", status="up", ping=100.0, address="", port=0, enabled=True):
    with session() as db:
        c = Config(name=name, slug=name.lower(), group="g", port=10800, enabled=enabled, tunnel=tunnel, status=status, ping_ms=ping, meta={"address": address, "port": port})
        db.add(c)
        db.flush()
        return c.id


def test_how_a_connection_feels_is_told_by_the_download_of_one_connection():
    assert [ts.feel(x)[0] for x in (120, 50, 49.9, 20, 8, 2, 1.9, 0, None)] == ["smooth", "smooth", "good", "good", "fair", "slow", "bad", "bad", "bad"]
    assert "4K" in ts.feel(80)[1] and "Very slow" in ts.feel(0.2)[1]


def test_the_configs_that_ride_a_tunnel_are_the_ones_with_its_name_or_its_address_and_port():
    tid = make_tunnel("tun-a", ports=(443, 8443))
    add_config("c-label", tunnel="tun-a", status="down", ping=None)
    add_config("c-up-1", tunnel="tun-a", status="up", ping=200.0)
    add_config("c-up-2", tunnel="tun-a", status="up", ping=90.0)
    add_config("c-addr", address=iran_host(tid), port=8443, status="up", ping=300.0)
    add_config("c-other-port", address=iran_host(tid), port=2096)
    add_config("c-other-tunnel", tunnel="tun-b")
    add_config("c-off", tunnel="tun-a", enabled=False)

    with session() as db:
        t = db.get(Tunnel, tid)
        names = [c.name for c in ts.riders(t, list(db.query(Config)))]

    assert names == ["c-up-2", "c-up-1", "c-addr", "c-label"]                  # the ones that are up first, the quickest first; a switched off or foreign config never


def test_the_window_learns_what_can_be_measured(monkeypatch):
    tid = make_tunnel("tun-a", door=False)
    monkeypatch.setattr(probe, "candidates", list)
    o = ts.options(tid)
    assert o["customer"]["ok"] is False and "no probe" in o["customer"]["why"] and o["door"]["ok"] is False and "no test door" in o["door"]["why"] and o["capacity"]["down_mbps"] == 400.0
    monkeypatch.setattr(probe, "candidates", lambda: PROBE)
    assert "no config is labelled" in ts.options(tid)["customer"]["why"]
    add_config("c1", tunnel="tun-a")
    o = ts.options(tid)
    assert o["customer"]["ok"] is True and [c["name"] for c in o["customer"]["configs"]] == ["c1"] and o["last"] is None
    assert ts.options(make_tunnel("tun-door", door=True))["door"]["ok"] is True
    with pytest.raises(ValueError, match="not found"):
        ts.options(9999)


def test_the_verdict_says_where_the_speed_is_lost():
    g, text = ts.verdict("customer", 12.0, 8.0, 150.0, {"down_mbps": 400.0})
    assert g == "fair" and "can carry about 400 Mb/s" in text and "ONE customer connection gets 12" in text and "lost beyond the tunnel" in text
    g, text = ts.verdict("customer", 300.0, 200.0, 90.0, {"down_mbps": 400.0})
    assert g == "smooth" and "the tunnel is the limit" in text and "lost beyond" not in text
    assert "ping is high (900 ms)" in ts.verdict("customer", 30.0, 20.0, 900.0, None)[1] and "upload is much smaller" in ts.verdict("customer", 40.0, 1.0, 100.0, None)[1]
    g, text = ts.verdict("door", 5.0, 5.0, 100.0, {"down_mbps": 400.0})
    assert "lost beyond" not in text                                           # the door measures the tunnel alone: nothing "beyond" it


def test_a_slow_reverse_tcp_tunnel_is_pointed_at_the_ones_the_iran_server_dials(monkeypatch):
    """Real tests, 2026-10-16: four customer connections together got 19 - 109 Mb/s through a reverse Chisel and 526 - 730 through a forward one."""
    text = ts.verdict("door", 9.5, 9.0, 100.0, None, 1, True)[1]
    assert "foreign server dials this tunnel" in text and "Chisel forward" in text and "Paqet" in text and "Test this pair" in text
    assert "foreign server dials" not in ts.verdict("door", 9.5, 9.0, 100.0, None, 1, False)[1]              # (Backhaul over GRE, Paqet, forward Chisel: not told)
    assert "foreign server dials" not in ts.verdict("door", 80.0, 9.0, 100.0, None, 1, True)[1]             # (a fast one needs no advice)
    monkeypatch.setattr(build_other, "_probe", lambda iran, port, key, secs=0, streams=8, pings=3, to=1.2: {"latency_ms": 90.0, "down_mbps": 9.5 if streams == 1 else 40.0, "up_mbps": 20.0})
    monkeypatch.setattr("nikatel.ssh.run_action", lambda node, name, params=None, timeout=60: "SINK_OK")
    verdicts = {}

    for direction, udp in (("reverse", False), ("forward", False), ("reverse", True), ("direct", False)):
        tid = make_tunnel(f"tun-{direction}{'-udp' if udp else ''}", door=True)

        with session() as db:
            t = db.get(Tunnel, tid)
            t.record = {**t.record, "direction": direction, "udp_carrier": udp}

        verdicts[(direction, udp)] = ts.run_door(1, tid, lambda s: None)["verdict"]

    assert "foreign server dials this tunnel" in verdicts[("reverse", False)]
    assert all("foreign server dials" not in verdicts[k] for k in (("forward", False), ("reverse", True), ("direct", False)))


def test_the_customer_speed_asks_the_probe_through_each_config_and_keeps_the_best(monkeypatch):
    tid = make_tunnel("tun-a")
    for n, p in (("c1", 120.0), ("c2", 90.0), ("c3", 150.0), ("c4", 400.0)):
        add_config(n, tunnel="tun-a", ping=p)

    monkeypatch.setattr(probe, "candidates", lambda: PROBE)
    monkeypatch.setattr(probe, "request", lambda *a, **k: asked.append((a, k)) or answers.pop(0))
    monkeypatch.setattr("nikatel.services.configs.sync", lambda *a, **k: synced.append(1))
    asked, synced, said = [], [], []
    answers = [{"ok": True, "speed": {"ping": 210.0, "download": 18.4, "upload": 9.1}}, {"ok": False, "error": "config not found"}, {"ok": True, "speed": {"ping": 190.0, "download": 63.2, "upload": 22.0}}]
    res = ts.run_customer(1, tid, said.append)
    assert [a[:2] for a, _ in asked] == [("POST", "/api/speed?name=c2"), ("POST", "/api/speed?name=c1"), ("POST", "/api/speed?name=c3")]      # the quickest three, 90 ms first
    assert res["mode"] == "customer" and res["via"] == "c3" and res["down_mbps"] == 63.2 and res["up_mbps"] == 22.0 and res["ping_ms"] == 190.0 and res["grade"] == "smooth"
    assert [(r["name"], r["ok"]) for r in res["runs"]] == [("c2", True), ("c1", False), ("c3", True)] and res["runs"][1]["error"] == "config not found"
    assert res["capacity"] == {"down_mbps": 400.0, "up_mbps": 380.0, "at": 1} and res["verdict"].startswith("Smooth") and "lost beyond" not in res["verdict"]          # (a smooth line is not told it loses speed)
    assert synced == [1] and any("measures 3 of them" in s for s in said) and any("↓ 63.2" in s for s in said)
    with session() as db:
        assert db.get(Tunnel, tid).record["speed"]["down_mbps"] == 63.2 and db.get(Tunnel, tid).record["quality"]["down_mbps"] == 400.0      # kept beside the quality, which stays


def test_a_busy_probe_is_waited_for_and_no_answer_at_all_is_an_error(monkeypatch):
    tid = make_tunnel("tun-a")
    add_config("c1", tunnel="tun-a")
    monkeypatch.setattr(probe, "candidates", lambda: PROBE)
    monkeypatch.setattr("nikatel.services.configs.sync", lambda *a, **k: None)
    slept = []
    monkeypatch.setattr(ts.time, "sleep", slept.append)
    seq = [{"ok": False, "error": "another speed test is running"}, {"ok": True, "speed": {"ping": 100.0, "download": 25.0, "upload": 5.0}}]
    monkeypatch.setattr(probe, "request", lambda *a, **k: seq.pop(0))
    res = ts.run_customer(1, tid, lambda s: None)
    assert slept == [20] and res["down_mbps"] == 25.0 and res["grade"] == "good"
    monkeypatch.setattr(probe, "request", lambda *a, **k: {"ok": False, "error": "speed test failed"})
    with pytest.raises(RuntimeError, match="no config answered"):
        ts.run_customer(1, tid, lambda s: None)

    monkeypatch.setattr(probe, "request", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("no probe answers (primary: timeout)")))
    with pytest.raises(RuntimeError, match="no config answered"):
        ts.run_customer(1, tid, lambda s: None)


def test_the_door_measures_one_connection_and_then_four_with_the_sink_kept_current(monkeypatch):
    tid = make_tunnel("tun-door", door=True)
    calls, actions = [], []
    monkeypatch.setattr(build_other, "_probe", lambda iran, port, key, secs=0, streams=8, pings=3, to=1.2: calls.append((port, key, secs, streams, pings)) or ({"latency_ms": 81.2, "down_mbps": 44.5, "up_mbps": 31.0} if streams == 1 else {"down_mbps": 160.0, "up_mbps": 120.0}))
    monkeypatch.setattr("nikatel.ssh.run_action", lambda node, name, params=None, timeout=60: actions.append((name, params["PORT"])) or "SINK_OK")
    said = []
    res = ts.run_door(1, tid, said.append)
    assert [(c[0], c[3]) for c in calls] == [(39100, 1), (39100, 4)] and calls[0][2] == 6 and actions == [("sink_install", "39100")]
    assert res["mode"] == "door" and res["down_mbps"] == 44.5 and res["up_mbps"] == 31.0 and res["ping_ms"] == 81.2 and [(r["name"], r["down_mbps"]) for r in res["runs"]] == [("1 connection", 44.5), ("4 connections", 160.0)]
    assert any("Four connections" in s for s in said) and "lost beyond" not in res["verdict"]
    monkeypatch.setattr(build_other, "_probe", lambda *a, **k: {"error": "connection refused"})
    with pytest.raises(RuntimeError, match="no data passes"):
        ts.run_door(1, tid, lambda s: None)

    with pytest.raises(ValueError, match="no test door"):
        ts.run_door(1, make_tunnel("tun-nodoor"), lambda s: None)


def test_the_api_starts_the_test_as_an_action_and_keeps_the_result(client, monkeypatch):
    tid = make_tunnel("tun-api")
    add_config("c1", tunnel="tun-api")
    monkeypatch.setattr(probe, "candidates", lambda: PROBE)
    monkeypatch.setattr(probe, "request", lambda *a, **k: {"ok": True, "speed": {"ping": 120.0, "download": 31.0, "upload": 12.0}})
    monkeypatch.setattr("nikatel.services.configs.sync", lambda *a, **k: None)
    o = client.get(f"/api/tunnels/{tid}/speed").json()
    assert o["customer"]["ok"] is True and o["door"]["ok"] is False and o["last"] is None
    assert client.get("/api/tunnels/9999/speed").status_code == 404
    assert client.post(f"/api/tunnels/{tid}/speed", json={"mode": "door"}).status_code == 400
    assert client.post(f"/api/tunnels/{tid}/speed", json={"mode": "nonsense"}).status_code == 400
    r = client.post(f"/api/tunnels/{tid}/speed", json={"mode": "customer"})
    assert r.status_code == 202
    aid, end, a = r.json()["action_id"], time.time() + 15, {}

    while time.time() < end:
        a = client.get(f"/api/actions/{aid}").json()

        if a.get("done"):
            break

        time.sleep(0.1)

    assert a["done"] and a["ok"] is True and any("↓ 31.0" in s for s in a["story"])
    last = client.get(f"/api/tunnels/{tid}/speed").json()["last"]
    assert last["mode"] == "customer" and last["down_mbps"] == 31.0 and last["via"] == "c1" and last["grade"] == "good" and last["capacity"]["down_mbps"] == 400.0

def test_the_test_door_sends_random_data_so_a_compressing_tunnel_does_not_look_faster_than_it_is():
    """A pile of the same byte (what the door used to send) is squeezed to nothing by BackPack: it showed 1 - 2.8 Gb/s between two ordinary servers. Real customers carry encrypted traffic that cannot be squeezed."""
    from nikatel.ssh import SCRIPTS, render_action

    sink = render_action("sink_install", {"NAME": "fi-ir1", "PORT": "39101", "KEY": "abcdef0123456789", "BIND": ""})
    probe_script = SCRIPTS["probe_run"][0]
    assert "c.sendall(os.urandom(65536))" in sink and "POOL" not in sink and 'b"x" * 65536' not in sink
    assert "s.sendall(os.urandom(65536))" in probe_script and "POOL" not in probe_script and 'b"x" * 65536' not in probe_script          # new random bytes for every block: a pool of the same blocks is found again by a compressor with a window of a few MB

def test_the_pair_test_measures_one_connection_beside_the_eight(monkeypatch):
    """The owner, 2026-10-16: "the closest to what the customer holds": a phone downloads over ONE connection, so the pair test says that number too."""
    said, calls = [], []

    class B:                                                    # the little of a Build that _measure uses
        aid, test_port, measure, quality = 1, 39100, "full", {}

    def fake(iran, port, key, secs=0, streams=8, pings=3, to=1.2):
        calls.append((streams, secs))
        return {"latency_ms": 80.0, "jitter_ms": 1.0, "loss_pct": 0.0, "down_mbps": 400.0, "up_mbps": 380.0} if streams == 8 else {"down_mbps": 55.0, "up_mbps": 41.0}

    monkeypatch.setattr(build_other, "_probe", fake)
    monkeypatch.setattr(build_other, "_say", lambda aid, line, **k: said.append(line))
    b = B()
    build_other._measure(b, None, "k")
    assert calls == [(8, 4), (1, 4)] and b.quality["down1_mbps"] == 55.0 and b.quality["up1_mbps"] == 41.0 and b.quality["down_mbps"] == 400.0 and b.quality["score"] > 0
    assert any(line.startswith("One connection: ↓55.0 ↑41.0") for line in said)
    calls.clear()
    b2 = B()
    b2.measure, b2.quality = "latency", {}
    build_other._measure(b2, None, "k")
    assert calls == [(8, 0)] and "down1_mbps" not in b2.quality                    # (only the latency run, no seconds of speed)

def test_the_score_counts_what_a_customer_downloads_and_not_the_upload_of_a_reverse_tunnel():
    """Real tests, 2026-10-16: a tunnel whose foreign server dials the Iran server gives a customer about 10 Mb/s per connection (the middle of the route holds back the flow of the party that dials) while its "upload" read 2100; a tunnel that the Iran server dials gave 600 and 38."""
    slow_reverse = {"latency_ms": 75.0, "jitter_ms": 1.0, "loss_pct": 0.0, "down_mbps": 124.0, "up_mbps": 2117.0, "down1_mbps": 10.0}
    fast_forward = {"latency_ms": 75.0, "jitter_ms": 1.0, "loss_pct": 0.0, "down_mbps": 621.0, "up_mbps": 38.0, "down1_mbps": 109.0}
    assert build_other.quality_score(fast_forward) >= build_other.quality_score(slow_reverse) + 15
    base = {"latency_ms": 75.0, "jitter_ms": 1.0, "loss_pct": 0.0, "down_mbps": 100.0}
    assert build_other.quality_score({**base, "up_mbps": 5000.0}) == build_other.quality_score({**base, "up_mbps": 100.0})        # a huge upload never lifts a small download
    assert build_other.quality_score({**base, "down1_mbps": 500.0}) > build_other.quality_score({**base, "down1_mbps": 5.0})       # one connection counts too
    assert build_other.quality_score({"latency_ms": 20, "jitter_ms": 0, "loss_pct": 0, "down_mbps": 900, "up_mbps": 900}) == 100
