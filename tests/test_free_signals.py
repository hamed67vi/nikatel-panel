"""The free quality signals (1.69.281; the owner, 2026-11-15, «بله، بساز»): the TCP of the tunnels themselves (what the kernel counts per socket: bytes sent, bytes sent AGAIN) and the users against the same hour of the last
days, both at no cost in bytes. A signal never changes anything: it asks the bandwidth job for a speed test (`bw.next_at`), at most once in twelve hours per tunnel, and says why.
The shell script runs here against the REAL format of `ss -tin` (captured from live Iran servers on 2026-11-15)."""
import shutil
import subprocess

import pytest
from sqlalchemy import select

from nikatel import ssh
from nikatel.db import session
from nikatel.models import Event, Server, Tunnel, TunnelCheck
from nikatel.services import freesignals as fs
from nikatel.services import testbudget

NOW = 1_800_000_000
D = 86400
_N = iter(range(10, 250))

# ------------------------------------------------------------------ the real format
SS_GRE = """State Recv-Q Send-Q  Local Address:Port   Peer Address:Port Process
ESTAB 0      0      10.193.136.197:9904 10.193.136.198:48820
\t bbr wscale:13,13 rto:318 rtt:117.524/12.517 ato:48 mss:1308 pmtu:1360 rcvmss:1308 advmss:1308 cwnd:87 bytes_sent:7811898 bytes_retrans:5673 bytes_acked:7806225 bytes_received:87297643 segs_out:60228 segs_in:89035 data_segs_out:21001 data_segs_in:76011 retrans:0/17 dsack_dups:4
ESTAB 0      0      10.193.136.197:9904 10.193.136.198:48848
\t bbr wscale:13,13 rto:327 rtt:126.723/15.042 ato:46 mss:1303 pmtu:1360 cwnd:628 bytes_sent:15357013 bytes_retrans:10101 bytes_acked:15346912 bytes_received:80879230 segs_out:68456 data_segs_out:28854 retrans:0/38
ESTAB 0      0      10.193.136.197:9904 10.193.136.198:10974
\t bbr wscale:13,11 rto:616 rtt:205.204/102.602 mss:1308 pmtu:1360 rcvmss:536 advmss:1308 cwnd:10 segs_in:2 bbr:(bw:0bps,mrtt:205.204) send 509932bps lastsnd:3628 delivered:1
"""
SS_V6 = """State Recv-Q Send-Q          Local Address:Port             Peer Address:Port Process
ESTAB 0      0      [::ffff:192.0.2.67]:9925 [::ffff:198.51.100.203]:53200
\t bbr wscale:13,11 rto:452 rtt:170.939/42.379 ato:40 mss:1424 pmtu:1476 cwnd:16 bytes_sent:2750 bytes_acked:2750 bytes_received:5796 segs_out:12 segs_in:11 data_segs_out:6
"""

pytestmark_bash = pytest.mark.skipif(shutil.which("bash") is None or shutil.which("awk") is None, reason="needs bash and awk")


def _run_script(tmp_path, ss_text, keep=300, peers="10.193.136.198"):
    (tmp_path / "bin").mkdir(exist_ok=True)
    stub = tmp_path / "bin" / "ss"
    (tmp_path / "ss.txt").write_text(ss_text)
    stub.write_text('#!/bin/bash\ncat "$DIR/ss.txt"\n')
    stub.chmod(0o755)
    script = ssh._script_text("tcp_health", {"PEERS": peers, "KEEP": str(keep)})
    r = subprocess.run(["bash", "-c", script], env={"PATH": f"{tmp_path / 'bin'}:/usr/bin:/bin", "DIR": str(tmp_path)}, capture_output=True, text=True, check=False, timeout=30)
    assert r.returncode == 0, r.stderr
    return [ln for ln in r.stdout.splitlines() if ln.startswith("S ")]


@pytestmark_bash
def test_the_script_reads_the_counters_of_the_sockets_that_have_sent_data(tmp_path):
    lines = _run_script(tmp_path, SS_GRE)
    assert lines == ["S 10.193.136.198 9904 48820 7811898 5673 117.524", "S 10.193.136.198 9904 48848 15357013 10101 126.723"]          # (the socket that has sent nothing is not a line)


@pytestmark_bash
def test_a_socket_that_never_resent_a_byte_says_zero_and_an_ipv6_mapped_address_is_read(tmp_path):
    assert _run_script(tmp_path, SS_V6, peers="198.51.100.203") == ["S 198.51.100.203 9925 53200 2750 0 170.939"]


@pytestmark_bash
def test_the_script_keeps_a_cap_of_sockets_per_far_end(tmp_path):
    assert len(_run_script(tmp_path, SS_GRE, keep=1)) == 1


def test_the_script_takes_only_addresses():
    for bad in ({"PEERS": "10.0.0.1; reboot", "KEEP": "300"}, {"PEERS": "", "KEEP": "300"}, {"PEERS": "10.0.0.1", "KEEP": "x"}):
        with pytest.raises(ValueError):
            ssh._script_text("tcp_health", bad)


# ------------------------------------------------------------------ the pure helpers
def test_parse_reads_the_lines_and_ignores_the_rest():
    out = "S 10.0.0.2 9904 48820 7811898 5673 117.5\nS 10.0.0.2 9904 48848 100 0 -\nNOW 123\nS broken\nS 10.0.0.2 x y 1 2 3\n"
    assert fs.parse(out) == {"10.0.0.2": {(9904, 48820): (7811898, 5673, 117.5), (9904, 48848): (100, 0, None)}}


def test_the_first_read_has_nothing_to_compare_with_and_a_socket_counts_what_it_sent_since_the_last_read():
    cur = {(9904, 1): (1000, 10, 100.0), (9904, 2): (5000, 0, 120.0), (22, 77): (9999, 9999, 10.0)}
    assert fs.step(None, cur, {9904}) == (0, 0, [100.0, 120.0])                                              # (the lifetime counters of a socket are not "since the last read"; the other port is not the tunnel's)
    nxt = {(9904, 1): (4000, 40, 100.0), (9904, 2): (5000, 0, 120.0), (9904, 3): (700, 7, None), (22, 77): (99999, 99999, 10.0)}
    assert fs.step(cur, nxt, {9904}) == (3000 + 0 + 700, 30 + 0 + 7, [100.0, 120.0])                         # (a new socket counts what it has; the other port is still not counted)
    assert fs.step({(9904, 1): (4000, 40, 1.0)}, {(9904, 1): (50, 0, 1.0)}, {9904}) == (50, 0, [1.0])        # (a counter that went back: the port was used again by a new connection)
    assert fs.step({}, {(1, 9904): (800, 8, 1.0)}, {9904}) == (800, 8, [1.0])                                # (the tunnel's port may be the PEER's: a forward tunnel dials it)


def test_a_share_is_judged_only_when_the_tunnel_sent_enough():
    assert fs.share([(1, 1_000_000, 500_000)]) is None                                                       # (an idle tunnel says nothing)
    assert fs.share([(1, 2_000_000, 100_000), (2, 3_000_000, 100_000)]) == pytest.approx(0.04)


def test_the_users_are_compared_with_the_same_hours_of_other_days():
    h = 3600
    rows = [(NOW - d * D + k * 60, 40.0, 5.0) for d in (1, 2, 3) for k in range(-30, 31)]                    # (45 Mb/s at this hour for three days)
    rows += [(NOW - 7 * h + k * 60, 300.0, 50.0) for k in range(60)]                                          # (a big load at another hour of today: not part of "usual")
    now_ok = [(NOW - 60 * i, 38.0, 5.0) for i in range(12)]
    now_low = [(NOW - 60 * i, 8.0, 2.0) for i in range(12)]
    cur, usual, days = fs.same_hour(rows + now_low, NOW)
    assert len(cur) == 12 and len(usual) == 183 and days == 3 and fs.users_fell(cur, usual, days) is True
    cur, usual, days = fs.same_hour(rows + now_ok, NOW)
    assert fs.users_fell(cur, usual, days) is False
    assert fs.users_fell([8.0] * 5, usual, days) is False                                                    # (too few readings now)
    assert fs.users_fell(cur, usual[:30], days) is False and fs.users_fell([8.0] * 12, usual, 1) is False   # (too few readings of the usual, or from one day only)
    assert fs.users_fell([1.0] * 12, [5.0] * 100, 3) is False                                                # (a usual of 5 Mb/s is no traffic to fall from)


# ------------------------------------------------------------------ the asks
@pytest.fixture(autouse=True)
def _clean():
    for d in (fs._PREV, fs._WIN, fs._BAD, fs._FELL, fs._LAST_A, fs.VIEW):
        d.clear()


def tunnel(name, bw=None, state="up"):
    n = next(_N)

    with session() as db:
        ir, fr = Server(name=f"fs-ir-{name}", host=f"203.0.113.{n}", role="iran", online=True), Server(name=f"fs-fr-{name}", host=f"198.51.100.{n}", role="foreign", online=True)
        db.add_all([ir, fr])
        db.flush()
        t = Tunnel(name=name, type="backhaul", transport="tcpmux", port=9901, iran_server_id=ir.id, foreign_server_id=fr.id, state=state, forwarded_ports=[8076], foreign_tunnel_ip=f"10.2.{n}.2", record={**({"bw": bw} if bw else {})}, secrets={})
        db.add(t)
        db.flush()
        return fs.Watch(t.id, name, ir.id, f"10.2.{n}.2", {9901})


def measured(next_at):
    return {"m": 2, "how": "link", "down": 300.0, "at": NOW - 3 * D, "ref": 300.0, "state": "ok", "drop": None, "suspect": False, "hist": [], "next_at": next_at}


def record_of(w):
    with session() as db:
        return dict(db.get(Tunnel, w.id).record or {})


def test_two_reads_in_a_row_that_resend_too_much_ask_for_a_speed_test_and_say_why():
    w = tunnel("fs-bad", bw=measured(NOW + 9 * D))
    fs.evaluate_tcp(w, {(9901, 1): (1_000_000, 1_000, 100.0)}, NOW)                                          # (the first read: nothing to compare)
    fs.evaluate_tcp(w, {(9901, 1): (6_000_000, 501_000, 100.0)}, NOW + 300)                                  # (10 % resent: the first bad evaluation)
    assert "fl" not in record_of(w)                                                                          # (one bad read is a blip)
    fs.evaluate_tcp(w, {(9901, 1): (11_000_000, 1_001_000, 100.0)}, NOW + 600)
    rec = record_of(w)
    assert rec["bw"]["next_at"] == NOW + 600 and rec["fl"]["asked"] == NOW + 600 and "sent" in rec["fl"]["why"] and "again" in rec["fl"]["why"]

    with session() as db:
        said = [e.text for e in db.scalars(select(Event))]

    assert any("a speed test is asked to confirm it" in m and "fs-bad" in m for m in said)
    assert fs.ask(w.id, "again", NOW + 700) is False                                                         # (once in twelve hours)
    assert fs.ask(w.id, "again", NOW + 13 * 3600) is True


def test_a_clean_line_an_idle_tunnel_and_a_used_up_budget_ask_for_nothing():
    clean, idle, broke = tunnel("fs-clean", bw=measured(NOW + 9 * D)), tunnel("fs-idle", bw=measured(NOW + 9 * D)), tunnel("fs-broke", bw=measured(NOW + 9 * D))

    for w in (clean, idle, broke):
        fs.evaluate_tcp(w, {(9901, 1): (1_000_000, 100, 100.0)}, NOW)

    for i in range(1, 4):
        fs.evaluate_tcp(clean, {(9901, 1): (1_000_000 + i * 50_000_000, 100 + i * 50_000, 100.0)}, NOW + 300 * i)         # (0.1 %: a clean line)
        fs.evaluate_tcp(idle, {(9901, 1): (1_000_000 + i * 10_000, 100 + i * 9_000, 100.0)}, NOW + 300 * i)              # (90 % resent - of 30 KB: nothing to judge)

    assert "fl" not in record_of(clean) and "fl" not in record_of(idle)
    testbudget.spend(80_000_000_000, "probe", NOW)                                                           # (the day's volume of the Doctor's tests is used)

    for i in range(1, 4):
        fs.evaluate_tcp(broke, {(9901, 1): (1_000_000 + i * 50_000_000, 100 + i * 5_000_000, 100.0)}, NOW + 300 * i)

    assert "fl" not in record_of(broke) and record_of(broke)["bw"]["next_at"] == NOW + 9 * D                 # (it says nothing and moves nothing while the budget is used)


def test_users_that_carry_under_half_of_their_usual_for_two_looks_ask_for_a_speed_test():
    w = tunnel("fs-users", bw=measured(NOW + 9 * D))

    with session() as db:
        db.add_all([TunnelCheck(tunnel_id=w.id, ts=NOW - d * D + k * 60, ok=True, rtt_ms=80.0, rx_mbps=40.0, tx_mbps=5.0, error="") for d in (1, 2, 3) for k in range(-30, 31)])
        db.add_all([TunnelCheck(tunnel_id=w.id, ts=NOW - 60 * i, ok=True, rtt_ms=80.0, rx_mbps=8.0, tx_mbps=2.0, error="") for i in range(13)])

    fs.evaluate_users(w, NOW)
    assert "fl" not in record_of(w) and fs._FELL[w.id] == 1
    fs.evaluate_users(w, NOW + 10)
    assert fs._FELL[w.id] == 1                                                                               # (looked at once in 15 minutes: the second call is too soon)
    fs._LAST_A.clear()
    fs.evaluate_users(w, NOW)
    rec = record_of(w)
    assert rec["fl"]["asked"] == NOW and "usually carry" in rec["fl"]["why"] and rec["bw"]["next_at"] == NOW


def test_a_far_end_with_more_sockets_than_the_script_keeps_is_not_judged():
    w = tunnel("fs-cap", bw=measured(NOW + 9 * D))
    full = {(9901, 1000 + i): (1_000_000 * (i + 1), 500_000 * (i + 1), 100.0) for i in range(fs.PER_PEER)}          # (300 sockets: a plain Backhaul with a socket per user connection)
    fs.evaluate_tcp(w, full, NOW)
    fs.evaluate_tcp(w, full, NOW + 300)
    assert w.id not in fs._PREV and w.id not in fs._WIN and fs.VIEW[w.id]["capped"] is True and "fl" not in record_of(w)


def test_a_tunnel_that_is_not_up_is_not_asked_for_anything():
    w = tunnel("fs-down", bw=measured(NOW + 9 * D), state="down")
    assert fs.ask(w.id, "x", NOW) is False and fs.ask(999_999, "x", NOW) is False


def test_the_job_reads_each_iran_server_once_and_stores_the_first_read_without_judging(monkeypatch):
    w1, w2 = tunnel("fs-j1", bw=measured(NOW + 9 * D)), tunnel("fs-j2", bw=measured(NOW + 9 * D))
    calls = []
    monkeypatch.setattr(fs.jobs, "stopping", lambda: False)
    monkeypatch.setattr(fs, "_load", lambda: ([w1, w2], {w1.iran_id: object(), w2.iran_id: object()}))
    monkeypatch.setattr(fs, "_read_one", lambda node, peers: calls.append(list(peers)) or {peers[0]: {(9901, 1): (1_000_000, 10, 100.0)}})
    fs.run()
    assert len(calls) == 2 and fs._PREV[w1.id] and w1.id not in fs._WIN                                      # (one read per Iran server; the first read only remembers)
    fs.run()
    assert w1.id in fs._WIN and fs.VIEW[w1.id]["retrans_pct"] is None                                        # (the second read judges: nothing was sent in between - too little to say)
