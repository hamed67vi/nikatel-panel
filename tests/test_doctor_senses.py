"""Two new senses of the Tunnel Doctor (the owner, 2026-11-11, after reading a note on bufferbloat and on `ss -ti`: "do these two and add them to the Doctor's operations and tests - and let us not spend a lot of volume on the tests"):

  latency under load   the speed test the Doctor already makes (`probe_run`, every 4 hours per tunnel, counted by testbudget) sends one more 7-byte echo every 0.1 s on a connection of its own WHILE its download streams run, and says the
                       median: a line that queues packets when it is full (bufferbloat) answers these slowly. A few hundred bytes - not one byte is added to the volume of the test (the budget counts the streams only), and a probe that moves no
                       data (the half-hourly quality job: pings only) adds nothing at all.
  TCP connections      the check of a tunnel reads `ss -ti` for the connections that are open to the tunnel's peer - what the kernel counts: segments sent again, rtt. A read: no packet is sent.

Both are TOLD (the Doctor's findings, the story of a drop, the story of a pair test); nothing is moved, restarted or rebuilt because of them."""
import contextlib
import json
import os
import socket
import stat
import subprocess
import sys
import threading
import time

import pytest
from sqlalchemy import select

from nikatel.db import session
from nikatel.models import Server, Tunnel
from nikatel.services import autopilot as ap
from nikatel.services import bandwidth, build_other, testbudget
from nikatel.services import tunnels as tn
from nikatel.ssh import SCRIPTS

NOW = 1_800_000_000


# ------------------------------------------------------------------ the probe, run for real against a little door
def probe_code() -> str:
    return SCRIPTS["probe_run"][0].split("<<'__NK_PY__'\n", 1)[1].split("\n__NK_PY__", 1)[0]


class Door:
    """The test door of a tunnel: echoes what it is sent, and streams bytes down at a limited rate (about 3 MB/s a stream). `bloated`: while a download runs its echoes are slow - a line that queues packets when it is full."""

    def __init__(self, key: str, bloated: bool):
        self.key, self.bloated, self.sent, self.active, self.closed = key.encode(), bloated, 0, 0, False
        self.srv = socket.socket()
        self.srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.srv.bind(("127.0.0.1", 0))
        self.srv.listen(32)
        self.port = self.srv.getsockname()[1]
        threading.Thread(target=self.accept, daemon=True).start()

    def accept(self):
        while not self.closed:
            try:
                c, _ = self.srv.accept()
            except OSError:
                return

            threading.Thread(target=self.handle, args=(c,), daemon=True).start()

    def handle(self, c):
        try:
            with contextlib.suppress(Exception):
                self.serve(c)
        finally:
            c.close()

    def serve(self, c):
        need, buf = len(self.key) + 1, b""

        while len(buf) < need:
            d = c.recv(need - len(buf))

            if not d:
                return

            buf += d

        mode = buf[len(self.key):]

        if mode == b"E":
            while True:
                d = c.recv(256)

                if not d:
                    break

                if self.bloated and self.active:
                    time.sleep(0.08)

                c.sendall(d)
        elif mode == b"D":
            self.active += 1

            try:
                while True:
                    c.sendall(b"x" * 65536)
                    self.sent += 65536
                    time.sleep(0.02)
            except OSError:
                pass
            finally:
                self.active -= 1

    def close(self):
        self.closed = True
        self.srv.close()


def run_probe(door: Door, secs: float, streams: int = 2, pings: int = 3) -> dict:
    out = subprocess.run([sys.executable, "-I", "-", str(door.port), str(secs), str(streams), str(pings), door.key.decode(), "2.0", "127.0.0.1", "0", "d", "0"],
                         input=probe_code(), capture_output=True, text=True, timeout=60, check=False)
    line = next(ln for ln in out.stdout.splitlines() if ln.startswith("NKPROBE "))
    return json.loads(line[8:])


def test_the_probe_pings_beside_its_download_and_a_bloated_line_shows_it():
    door = Door("k1", bloated=True)

    try:
        d = run_probe(door, 3.0)
    finally:
        door.close()

    assert d["latency_ms"] < 40 and d["loaded_n"] >= 8 and d["loaded_lost_pct"] == 0.0                         # idle: the echo comes back at once; under load: a dozen pings were answered
    assert d["loaded_ms"] >= d["latency_ms"] + 60 and d["loaded_p95_ms"] >= d["loaded_ms"]                    # ... and slowly
    b = bandwidth.bloat_of(d, NOW)
    assert b["level"] in ("warn", "bad") and "bufferbloat" in b["text"] and b["idle"] == d["latency_ms"] and b["loaded"] == d["loaded_ms"] and b["at"] == NOW


def test_a_line_that_does_not_queue_answers_the_same_under_load():
    door = Door("k2", bloated=False)

    try:
        d = run_probe(door, 2.0)
    finally:
        door.close()

    assert d["loaded_n"] >= 8 and d["loaded_ms"] < 50
    assert bandwidth.bloat_of(d, NOW)["level"] == "ok"


def test_the_pings_add_no_volume_to_the_test():
    """The owner: "do not spend a lot of volume on the tests". The streams are the volume (the budget counts them: `w_moved`); the pings are 7 bytes every 0.1 s and are not counted - the streams moved exactly what the door sent, and
    the test moved the same number of streams for the same seconds as before."""
    door = Door("k3", bloated=True)

    try:
        d = run_probe(door, 2.0)
    finally:
        door.close()

    assert 0 < d["w_moved"] <= door.sent < 40_000_000                                                            # (two streams, two seconds, ~3 MB/s each - the pings are nowhere in it)
    assert d["down_streams"] and len(d["down_streams"]) == 2                                                    # two streams, as asked: no stream was added for the pings
    script = SCRIPTS["probe_run"][0]
    assert script.count("threading.Thread(target=loaded_pings") == 1 and script.count("os.urandom(65536)") == 1       # ONE ping thread, and the streams' own bytes are untouched
    assert "MOVED.append(got)" in script and "LOADED.append" in script and "MOVED.append((" not in script         # (the pings never reach MOVED, the sum the budget reads)


def test_a_probe_that_moves_no_data_pings_nothing_beside_it():
    """The quality job of every half hour asks for pings only (secs=0): no stream, so no ping thread, no loaded fields."""
    door = Door("k4", bloated=True)

    try:
        d = run_probe(door, 0.0, pings=5)
    finally:
        door.close()

    assert d["latency_ms"] is not None and "loaded_ms" not in d and "loaded_n" not in d and "w_moved" not in d


def test_the_budget_counts_the_streams_and_not_the_pings(monkeypatch):
    spent = []
    monkeypatch.setattr(testbudget, "spend", lambda n, why: spent.append((n, why)))
    d = build_other.parse_probe("NKPROBE " + json.dumps({"latency_ms": 30.0, "down_mbps": 80.0, "w_moved": 12_345_678, "loaded_ms": 310.0, "loaded_n": 20}))
    assert spent == [(12_345_678, "probe")] and d["loaded_ms"] == 310.0


# ------------------------------------------------------------------ what the numbers mean
def test_bufferbloat_is_judged_by_how_much_worse_and_how_many_times_worse():
    ok = bandwidth.bloat(38.0, 52.0)
    assert ok["level"] == "ok" and ok["extra"] == 14.0 and ok["text"].startswith("Latency under load: 38 ms idle, 52 ms while the speed test ran (+14 ms)") and "holds up" in ok["text"]
    assert bandwidth.bloat(150.0, 260.0)["level"] == "ok"                                                         # a long line: 110 ms more is not twice as slow
    assert bandwidth.bloat(30.0, 95.0)["level"] == "warn"                                                         # twice and 60 ms more
    assert bandwidth.bloat(30.0, 55.0)["level"] == "ok"                                                           # twice but only 25 ms more: nothing a user feels
    bad = bandwidth.bloat(30.0, 300.0)                                                                            # the note's own example: 30 ms idle, 300 under a full download
    assert bad["level"] == "bad" and bad["extra"] == 270.0 and "strong bufferbloat" in bad["text"]
    assert bandwidth.bloat(30.0, 40.0, 6.0)["level"] == "warn" and "6 % of the pings" in bandwidth.bloat(30.0, 40.0, 6.0)["text"]      # pings that got no answer at all
    assert bandwidth.bloat(30.0, 40.0, 25.0)["level"] == "bad"
    assert bandwidth.bloat(None, 100.0) is None and bandwidth.bloat(30.0, None) is None and bandwidth.bloat_of({"latency_ms": 30.0}, NOW) is None and bandwidth.bloat_of(None, NOW) is None
    assert bandwidth.bloat(0.0, 5.0)["level"] == "ok"                                                             # an idle ping of 0 is read as 1 ms, never divided by


def make_tunnel(name: str) -> int:
    with session() as db:
        n = len(db.scalars(select(Server)).all()) + 70
        ir, fr = Server(name=f"ir-{name}", host=f"203.0.113.{n}", role="iran", online=True), Server(name=f"fr-{name}", host=f"198.51.100.{n}", role="foreign", online=True)
        db.add_all([ir, fr])
        db.flush()
        t = Tunnel(name=name, type="backpack", transport="tcpmux", port=9908, iran_server_id=ir.id, foreign_server_id=fr.id, state="up", forwarded_ports=[8072], record={})
        db.add(t)
        db.flush()
        return t.id


def bw_of(tid: int) -> dict:
    with session() as db:
        return dict((db.get(Tunnel, tid).record or {}).get("bw") or {})


def test_the_record_of_a_tunnel_keeps_the_last_reading_of_the_latency_under_load():
    tid = make_tunnel("ds-st1")
    bandwidth._store(tid, 120.0, None, "link", 40.0, None, bandwidth.bloat_of({"latency_ms": 40.0, "loaded_ms": 400.0}, NOW))
    b = bw_of(tid)["bloat"]
    assert b["level"] == "bad" and b["idle"] == 40.0 and b["loaded"] == 400.0 and bw_of(tid)["down"] == 120.0
    bandwidth._store(tid, 118.0, None, "link", 41.0)                                                              # a sample with no pings beside it: the last reading stays
    assert bw_of(tid)["bloat"] == b
    bandwidth._store(tid, 119.0, None, "link", 40.0, None, bandwidth.bloat_of({"latency_ms": 40.0, "loaded_ms": 52.0}, NOW + 1))
    assert bw_of(tid)["bloat"]["level"] == "ok"                                                                   # a new one replaces it
    tid2 = make_tunnel("ds-st2")
    bandwidth._store(tid2, 120.0, None, "link", 40.0)
    assert "bloat" not in bw_of(tid2)                                                                             # nothing read, nothing kept: a record is as it was


def test_the_door_measurement_of_a_backpack_tunnel_passes_the_reading_on():
    tid = make_tunnel("ds-nq")
    bandwidth.note_quality(tid, {"down_mbps": 200.0, "up_mbps": 150.0, "latency_ms": 30.0, "loaded_ms": 70.0, "loaded_lost_pct": 0.0})
    assert bw_of(tid)["bloat"]["level"] == "ok" and bw_of(tid)["bloat"]["loaded"] == 70.0


def test_the_story_of_a_drop_says_the_latency_under_load_when_it_is_known():
    tid = make_tunnel("ds-fc")
    assert bandwidth._facts(tid)["bloat"] is None
    bandwidth._store(tid, 120.0, None, "link", 40.0, None, {**bandwidth.bloat_of({"latency_ms": 40.0, "loaded_ms": 400.0}, int(time.time()))})
    f = bandwidth._facts(tid)
    said = []
    base = {"iran": {"name": "ir", "cpu": 10.0}, "foreign": {"name": "fr", "cpu": 12.0}, "same_iran": {"total": 1, "dropped": 0}, "same_foreign": {"total": 1, "dropped": 0}, "loss": 0.0, "rtt": 40.0, "rtt_ref": 40.0, "live": None}
    bandwidth._say_checks(said.append, {**base, "bloat": f["bloat"]})
    assert f["bloat"]["level"] == "bad" and any(ln.startswith("✕ Latency under load: 40 ms idle, 400 ms") for ln in said)
    said.clear()
    bandwidth._say_checks(said.append, {**base, "bloat": bandwidth.bloat(40.0, 52.0) | {"at": NOW}})
    assert any(ln.startswith("✓ Latency under load") for ln in said)
    said.clear()
    bandwidth._say_checks(said.append, base)                                                                      # (a look with no reading: no line, as before)
    assert not any("under load" in ln for ln in said)
    old = {**f["bloat"], "at": int(time.time()) - bandwidth.BLOAT_FRESH - 60}

    with session() as db:
        t = db.get(Tunnel, tid)
        rec = dict(t.record)
        rec["bw"] = {**rec["bw"], "bloat": old}
        t.record = rec

    assert bandwidth._facts(tid)["bloat"] is None                                                                 # a reading of yesterday is not told as today's


def test_the_pair_test_says_the_latency_under_load_and_keeps_it(monkeypatch):
    said = []

    class B:
        aid, test_port, measure, quality = 1, 39100, "full", {}

    def fake(iran, port, key, secs=0, streams=8, pings=3, to=1.2):
        return {"latency_ms": 80.0, "jitter_ms": 1.0, "loss_pct": 0.0, "down_mbps": 400.0, "up_mbps": 380.0, "loaded_ms": 310.0} if streams == 8 else {"down_mbps": 55.0, "up_mbps": 41.0}

    monkeypatch.setattr(build_other, "_probe", fake)
    monkeypatch.setattr(build_other, "_say", lambda aid, line, **k: said.append(line))
    b = B()
    build_other._measure(b, None, "k")
    assert b.quality["loaded_ms"] == 310.0 and any(ln.startswith("Quality: 80.0 ms") and ", 310.0 ms under load" in ln for ln in said)
    said.clear()
    b2 = B()
    monkeypatch.setattr(build_other, "_probe", lambda iran, port, key, secs=0, streams=8, pings=3, to=1.2: {"latency_ms": 80.0, "jitter_ms": 1.0, "loss_pct": 0.0, "down_mbps": 400.0, "up_mbps": 380.0})
    build_other._measure(b2, None, "k")
    assert "loaded_ms" not in b2.quality and not any("under load" in ln for ln in said)                           # a probe without the pings: the line is as it was


# ------------------------------------------------------------------ the TCP connections
CANNED = """ESTAB 0 0 10.2.1.1:41234 10.2.1.2:8443
\t cubic wscale:7,7 rto:204 rtt:3.5/1.2 ato:40 mss:1448 pmtu:1500 rcvmss:1448 advmss:1448 cwnd:10 bytes_sent:1000 bytes_retrans:2896 bytes_acked:998 segs_out:400 segs_in:300 data_segs_out:380 send 33Mbps lastsnd:12 retrans:0/2 rcv_space:14480
ESTAB 0 0 10.2.1.1:41236 10.2.1.2:8443
\t cubic wscale:7,7 rto:208 rtt:5.5/2.2 ato:40 mss:1448 pmtu:1500 cwnd:10 segs_out:200 segs_in:150 data_segs_out:190 send 20Mbps retrans:1/5 rcv_space:14480
ESTAB 0 0 10.2.1.1:41238 10.2.1.2:8443
\t cubic wscale:7,7 rto:204 rtt:4.5/1.0 ato:40 mss:1448 pmtu:1500 cwnd:10 segs_out:100 segs_in:90 data_segs_out:95 send 20Mbps rcv_space:14480
"""


def ss_line() -> str:
    script = SCRIPTS["tunnel_side"][0]
    return next(ln for ln in script.splitlines() if ln.startswith('if [ -n "@PEER@" ] && command -v ss'))


def run_line(tmp_path, canned: str) -> str:
    stub = tmp_path / "ss"
    stub.write_text("#!/bin/sh\ncat <<'EOF'\n" + canned + "EOF\n")
    stub.chmod(stub.stat().st_mode | stat.S_IEXEC)
    out = subprocess.run(["bash", "-c", ss_line().replace("@PEER@", "10.2.1.2")], env={"PATH": f"{tmp_path}:/usr/bin:/bin"}, capture_output=True, text=True, timeout=15, check=False)
    return out.stdout.strip()


@pytest.mark.skipif(not os.path.exists("/usr/bin/awk") and not os.path.exists("/bin/awk"), reason="no awk on this machine")
def test_the_check_script_reads_the_counters_of_ss_for_real(tmp_path):
    assert run_line(tmp_path, CANNED) == "TCPI 3 7 700 4.5 5.5"                      # 3 connections, 2 + 5 segments sent again (the third has no "retrans:" at all), 400 + 200 + 100 sent, rtt 3.5 / 5.5 / 4.5
    assert run_line(tmp_path, "") == ""                                               # no connection open to the peer: nothing is printed


def test_the_tcp_read_is_made_by_the_full_check_only_and_asks_no_new_parameter():
    script, rules = SCRIPTS["tunnel_side"]
    assert script.index("ss -tinH state established dst '@PEER@'") > script.index('if [ "@QUICK@" = 1 ]; then echo "NOW $(date +%s)"; exit 0; fi')          # after the quick exit: the sentinel and the quick look never run it
    assert set(rules) == {"UNITS", "IFACE", "PEER", "PORTS", "TARGETS", "QUICK"} and script.count("timeout 3 ss -tinH") == 1                        # (no new parameter, and `ss` cannot hang the check)
    assert "-w" not in ss_line().split("awk")[0] and "ping" not in ss_line() and "curl" not in ss_line()                                           # a read of the kernel's counters: nothing is sent


def test_the_tcp_line_is_parsed_and_a_bad_one_is_ignored():
    s = tn.parse_side("UNIT x active\nTCPI 3 7 700 4.5 5.5\nNOW 5\n")
    assert s.tcpi == (3, 7, 700, 4.5, 5.5) and s.now == 5
    assert tn.parse_side("TCPI 3 x 700 4.5 5.5\n").tcpi is None and tn.parse_side("TCPI 3 7\n").tcpi is None and tn.parse_side("NOW 5\n").tcpi is None


def test_the_share_of_segments_sent_again_is_worked_out_between_two_checks():
    first = tn.tcp_read(None, (4, 10, 1000, 48.0, 90.0), NOW)
    assert first == {"n": 4, "retr": 10, "segs": 1000, "rtt": 48.0, "rtt_max": 90.0, "at": NOW, "pct": None}                       # the first look has nothing to compare with
    second = tn.tcp_read(first, (4, 40, 2000, 51.0, 120.0), NOW + 60)
    assert second["pct"] == 3.0 and second["retr"] == 40                                                                          # 30 of the 1000 segments of the minute
    assert tn.tcp_read(second, (4, 41, 2100, 51.0, 80.0), NOW + 120)["pct"] is None                                                # only 100 segments: too few to say a ratio
    assert tn.tcp_read(second, (2, 3, 150, 51.0, 80.0), NOW + 120)["pct"] is None                                                  # the connections were made again: the counters started over
    assert tn.tcp_read(second, (4, 50, 3000, 51.0, 80.0), NOW + 60 + tn.MAX_RATE_GAP + 1)["pct"] is None                           # the checks are too far apart: not "now"
    assert tn.tcp_read(second, None, NOW + 120) is None and tn.tcp_read(second, (0, 0, 0, 0.0, 0.0), NOW + 120) is None             # no connection open: no reading


def test_the_finding_says_loss_when_segments_are_sent_again_and_stays_quiet_when_there_is_no_reading():
    good = tn.tcp_finding({"n": 3, "retr": 5, "segs": 5000, "rtt": 48.0, "rtt_max": 60.0, "at": NOW, "pct": 0.4}, NOW + 30)
    assert good == {"ok": True, "text": "3 TCP connections to the peer, RTT 48 ms (at most 60): 0.4 % of the segments were sent again in the last minute"}
    bad = tn.tcp_finding({"n": 1, "retr": 90, "segs": 1000, "rtt": 48.0, "rtt_max": 60.0, "at": NOW, "pct": 6.5}, NOW + 30)
    assert bad["ok"] is False and bad["text"].startswith("1 TCP connection to the peer") and bad["text"].endswith("6.5 % of the segments were sent again in the last minute - the path loses packets")
    life = tn.tcp_finding({"n": 2, "retr": 50, "segs": 1000, "rtt": 48.0, "rtt_max": 60.0, "at": NOW, "pct": None}, NOW + 30)       # no interval yet: what the connections counted since they opened
    assert life["ok"] is False and "5 % of the segments were sent again since these connections opened" in life["text"]
    assert tn.tcp_finding({"n": 2, "retr": 50, "segs": 1000, "rtt": 1.0, "rtt_max": 1.0, "at": NOW, "pct": 1.0}, NOW + tn.TCPI_FRESH + 1) is None          # an old reading is not told
    assert tn.tcp_finding(None, NOW) is None and tn.tcp_finding({"n": 0, "at": NOW}, NOW) is None and tn.tcp_finding({"n": 2, "at": NOW, "pct": None, "segs": 0}, NOW) is None


def test_the_check_keeps_the_reading_in_the_detail_of_the_tunnel(monkeypatch):
    tid = make_tunnel("ds-ck")
    answers = iter(["TCPI 2 4 1000 40.0 55.0\n", "TCPI 2 44 2000 41.0 70.0\n"])

    def fake_query(node, plan, is_iran, *a, **k):
        units = plan.iran_units if is_iran else plan.foreign_units
        text = "".join(f"UNIT {u} active\n" for u in units)
        return tn.parse_side(text + (next(answers) if is_iran else "") + f"NOW {int(time.time())}\n")

    monkeypatch.setattr(tn, "_query", fake_query)
    tn.check_tunnel(tid)

    with session() as db:
        first = dict(db.get(Tunnel, tid).detail["tcpi"])

    assert first["n"] == 2 and first["retr"] == 4 and first["pct"] is None

    with session() as db:                                                                                                          # (the second check comes a minute later)
        t = db.get(Tunnel, tid)
        t.detail = {**t.detail, "tcpi": {**t.detail["tcpi"], "at": first["at"] - 60}}

    tn.check_tunnel(tid)

    with session() as db:
        second = dict(db.get(Tunnel, tid).detail["tcpi"])

    assert second["retr"] == 44 and second["pct"] == 4.0 and second["rtt_max"] == 70.0                                           # 40 of the 1000 segments between the two checks


# ------------------------------------------------------------------ the Doctor's findings
def test_the_diagnosis_lists_the_tcp_reading_and_the_latency_under_load(monkeypatch):
    tid = make_tunnel("ds-dg")
    now = int(time.time())

    with session() as db:
        t = db.get(Tunnel, tid)
        t.last_check_at = now - 5
        t.detail = {"iran_units": {"u": "active"}, "foreign_units": {"u": "active"}, "iran_error": "", "foreign_error": "", "data_code": "200",
                    "tcpi": {"n": 2, "retr": 60, "segs": 1000, "rtt": 48.0, "rtt_max": 70.0, "at": now - 20, "pct": 6.0}}
        t.record = {"bw": {"bloat": {"level": "bad", "text": "Latency under load: 30 ms idle, 300 ms while the speed test ran (+270 ms) - strong bufferbloat: when the line is full every request waits behind the queue", "at": now - 3600}}}

    monkeypatch.setattr(ap, "check_tunnel", lambda tunnel_id, fresh_ext=False: None)
    monkeypatch.setattr(ap, "_ping_loss", lambda node, host: 0.0)
    monkeypatch.setattr(ap, "_dial", lambda node, host, port: True)
    monkeypatch.setattr(ap, "_path_scan", lambda tunnel_id: {"where": "none", "text": "no loss found", "rows": []})
    monkeypatch.setattr(ap.ai, "advise", lambda *a, **k: None)
    f = ap.diagnose(tid)["findings"]
    tcp = next(x for x in f if "TCP connections to the peer" in x["text"])
    load = next(x for x in f if x["text"].startswith("Latency under load"))
    assert tcp["ok"] is False and "6 % of the segments were sent again in the last minute - the path loses packets" in tcp["text"] and load["ok"] is False and "strong bufferbloat" in load["text"]

    with session() as db:                                                                                                          # an old reading, and none: neither line
        t = db.get(Tunnel, tid)
        t.detail = {**t.detail, "tcpi": {**t.detail["tcpi"], "at": now - 5000}}
        t.record = {"bw": {"bloat": {**t.record["bw"]["bloat"], "at": now - 13 * 3600}}}

    f = ap.diagnose(tid)["findings"]
    assert not any("TCP connections to the peer" in x["text"] or x["text"].startswith("Latency under load") for x in f)
