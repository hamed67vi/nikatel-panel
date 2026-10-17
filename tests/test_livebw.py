"""What the users use of every tunnel right now (services/livebw.py): the counters of the tunnel's own link, read every few seconds and turned into rates.
The owner, 2026-10-17: "show the capacity, and the bandwidth the users are using - the numbers must be real, reliable and live"."""
import itertools
import shutil
import socket
import subprocess
import sys
import time
from collections import deque

import pytest

from nikatel import ssh
from nikatel.db import session
from nikatel.models import Server, Tunnel
from nikatel.services import livebw
from nikatel.ssh import SSHError

HOSTS = itertools.count(10)
MB = 1_000_000 // 8          # bytes in a megabit


@pytest.fixture(autouse=True)
def clean(monkeypatch):
    for d in (livebw._READS, livebw._SOCKS, livebw._LIVE, livebw._HOLD, livebw._BACKOFF):
        d.clear()

    livebw._ASKED[0] = livebw._LAST[0] = 0.0
    monkeypatch.setattr(livebw.jobs, "stopping", lambda: False)                          # (an earlier test that shut the app down leaves the flag set)
    yield


def make(name="gr-a", typ="backhaul", transport="tcpmux", record=None, iran_online=True, enabled=True, iran_enabled=True):
    n = next(HOSTS)

    with session() as db:
        ir = Server(name="ir-" + name, host=f"203.0.113.{n}", role="iran", online=iran_online, enabled=iran_enabled)
        fr = Server(name="fr-" + name, host=f"198.51.100.{n}", role="foreign", online=True)
        db.add_all([ir, fr])
        db.flush()
        t = Tunnel(name=name, type=typ, transport=transport, port=9901, iran_server_id=ir.id, foreign_server_id=fr.id, state="up", forwarded_ports=[8076], foreign_tunnel_ip=f"10.2.{n}.2",
                   record=record or {}, secrets={}, enabled=enabled)
        db.add(t)
        db.flush()
        return t.id, ir.id


def answer(up, **cards):
    """What the iface_counters script prints: a line per card and the uptime."""
    return "".join(f"IF {c} {rx} {tx}\n" for c, (rx, tx) in cards.items()) + f"UP {up}\n"


def test_the_answer_of_the_script_is_read_card_by_card():
    up, cards = livebw.parse_counters("IF gr-a 1000 2000\nIF bp7 5 6\nUP 12345.67\n")
    assert up == 12345.67 and cards == {"gr-a": (1000, 2000), "bp7": (5, 6)}
    assert livebw.parse_counters("") == (None, {})
    up, cards = livebw.parse_counters("IF gr-a notanumber 2\nIF short\nUP soon\nnoise\n")
    assert up is None and cards == {}                                                    # (a line that is not a counter is not one: nothing is guessed)


def test_a_rate_is_the_newest_read_against_the_oldest_inside_the_window():
    reads = [(t, t * MB * 8, t * MB) for t in (0, 5, 10, 15, 20)]                          # 8 Mb/s down and 1 Mb/s up, steadily
    r = livebw.rate(reads)
    assert r == {"down": 8.0, "up": 1.0, "span": 15.0}                                   # (the window is 15 s: the read at 0 s is older than that)
    burst = reads[:-1] + [(20, 15 * MB * 8 + 5 * 100 * MB, 15 * MB)]                      # the last 5 s carried 100 Mb/s
    assert livebw.rate(burst)["down"] == pytest.approx((15 * 8 + 500 - 5 * 8) / 15, abs=0.01)      # a burst is averaged over the window (the read at 5 s to the one at 20 s): steady, and still live


def test_two_reads_too_close_or_a_counter_that_went_back_give_no_rate():
    assert livebw.rate([]) is None and livebw.rate([(0, 0, 0)]) is None
    assert livebw.rate([(0, 0, 0), (livebw.MIN_SPAN - 0.5, 10 * MB, 0)]) is None         # not far enough apart: a rate over 3 s is noise
    assert livebw.rate([(0, 100, 100), (10, 50, 200)]) is None and livebw.rate([(0, 100, 100), (10, 200, 50)]) is None


def test_a_slow_cadence_still_gives_a_rate_over_the_nearest_older_read():
    reads = [(0, 0, 0), (30, 30 * MB * 8, 0)]                                              # one read in 30 s (nobody looks): older than the window, but the only one
    assert livebw.rate(reads) == {"down": 8.0, "up": 0.0, "span": 30.0}


def test_a_rate_no_card_can_carry_is_dropped():
    assert livebw.rate([(0, 0, 0), (10, 10 ** 15, 0)]) is None


def test_the_window_starts_again_when_the_card_was_made_again_or_the_server_restarted():
    w = deque()
    livebw.push(w, 100.0, 1000, 1000)
    livebw.push(w, 105.0, 2000, 2000)
    assert len(w) == 2
    livebw.push(w, 110.0, 50, 60)                                                          # the card was made again: its counters are small again
    assert list(w) == [(110.0, 50, 60)]
    livebw.push(w, 5.0, 100, 100)                                                          # the server restarted: its clock is small again
    assert list(w) == [(5.0, 100, 100)]
    livebw.push(w, 5.0 + livebw.KEEP + 1, 200, 200)                                       # what is older than the window is forgotten
    assert len(w) == 1


def test_the_script_asks_only_for_cards_and_connections_by_their_names_and_gives_what_a_real_card_says():
    assert "iface_counters" in ssh.SCRIPTS
    for bad in ({"IFACES": "lo; reboot"}, {"IFACES": "lo", "SOCKS": "s80; reboot"}, {"SOCKS": "s99999999"}, {"SOCKS": "d443@example.com"}, {"SOCKS": "x80"}):
        with pytest.raises(ValueError):
            ssh._script_text("iface_counters", {"IFACES": "", "SOCKS": "", **bad})                 # a card is a name, a connection is a port (and an address): nothing else gets into the command

    assert "@IFACES@" not in ssh._script_text("iface_counters", {"IFACES": "", "SOCKS": "s8072 d443@203.0.113.9"})      # (both may be empty, and both can be several)

    if sys.platform.startswith("linux"):
        out = subprocess.run(["bash", "-c", ssh._script_text("iface_counters", {"IFACES": "lo no-such-card", "SOCKS": ""})], capture_output=True, text=True, timeout=20, check=False).stdout
        up, cards = livebw.parse_counters(out)
        assert up is not None and up > 0 and "lo" in cards and "no-such-card" not in cards      # (a card that does not exist is left out, not made up)


@pytest.mark.skipif(not sys.platform.startswith("linux") or shutil.which("ss") is None, reason="needs Linux and ss")
def test_the_script_reads_the_byte_counters_of_the_connections_of_a_port_in_both_directions():
    server = socket.socket()
    server.bind(("127.0.0.1", 0))
    server.listen(4)
    port = server.getsockname()[1]
    client = socket.create_connection(("127.0.0.1", port))
    peer, _ = server.accept()
    client.sendall(b"x" * 60_000)                      # (a little: it all fits in the buffers, nobody has to read it for the counters to count it)
    peer.sendall(b"y" * 40_000)
    time.sleep(0.5)

    try:
        for spec, who in ((f"s{port}", "the accepted connection (the foreign server dialled in)"), (f"d{port}@127.0.0.1", "the connection this side opened (a forward tunnel)")):
            out = subprocess.run(["bash", "-c", ssh._script_text("iface_counters", {"IFACES": "", "SOCKS": spec})], capture_output=True, text=True, timeout=20, check=False).stdout
            conns = livebw.parse_socks(out).get(spec) or {}
            assert len(conns) == 1, (who, out)
            (acked, received), = conns.values()
            sent, got = (40_000, 60_000) if spec.startswith("s") else (60_000, 40_000)                 # (what that end sent, and what it received)
            assert acked >= sent and received >= got, (who, acked, received)
    finally:
        client.close()
        peer.close()
        server.close()


def test_the_servers_to_read_are_the_iran_servers_with_a_card_or_the_connections_of_each_tunnel():
    gre, gre_sid = make("gr-a")
    direct, direct_sid = make("bp-d", typ="backpack", transport="l3:udp", record={"inner_net": 7})
    rev, rev_sid = make("bp-rev", typ="backpack", transport="wssmux")                     # a reverse proxy: no card of its own - its connections on the control port are counted
    chrev, chrev_sid = make("ch-rev", typ="chisel", transport="ws", record={"direction": "reverse"})
    chfwd, chfwd_sid = make("ch-fwd", typ="chisel", transport="fws", record={"direction": "forward"})
    make("bp-udp", typ="backpack", transport="udp", record={"udp_carrier": True})          # UDP, QUIC, KCP and raw sockets have no connection to count: no number ...
    make("pq-a", typ="paqet", transport="kcp", record={"direction": "direct"})             # ... and Paqet is one of them
    make("test-x")                                                                         # a temporary test tunnel: never listed, never read
    make("gr-off", iran_online=False)
    make("gr-dis", enabled=False)
    make("gr-ird", iran_enabled=False)

    with session() as db:
        fwd_host = db.get(Tunnel, chfwd).foreign.host

    got = {t["server_id"]: (t["cards"], t["socks"]) for t in livebw.targets()}
    assert got == {gre_sid: ({"gr-a": [gre]}, {}),                                          # the GRE link by the name of the tunnel
                   direct_sid: ({"bp7": [direct]}, {}),                                     # the L3 link of a BackPack Direct tunnel by its bp<N>
                   rev_sid: ({}, {"s9901": [rev]}), chrev_sid: ({}, {"s9901": [chrev]}),   # the connections that came in on the control port (the foreign server dials the Iran server)
                   chfwd_sid: ({}, {f"d9901@{fwd_host}": [chfwd]})}                         # the connections the Iran server opened to the foreign server (a forward tunnel)
    assert all(t["node"].host.startswith("203.0.113.") for t in livebw.targets())          # (always the Iran side: that is where the users' traffic enters)


def sockline(spec, loc, rem, acked, received):
    return f"SOCK {spec} {loc} {rem} {acked} {received}\n"


def test_the_connections_in_the_answer_are_read_by_tunnel():
    out = (sockline("s8072", "10.0.0.1:8072", "203.0.113.9:40001", 100, 200) + sockline("s8072", "10.0.0.1:8072", "203.0.113.9:40002", 5, 6)
           + sockline("d443@203.0.113.9", "10.0.0.1:50000", "203.0.113.9:443", 7, 8) + "SOCK short\nSOCK a b c d e\nUP 1.0\n")
    assert livebw.parse_socks(out) == {"s8072": {("10.0.0.1:8072", "203.0.113.9:40001"): (100, 200), ("10.0.0.1:8072", "203.0.113.9:40002"): (5, 6)},
                                       "d443@203.0.113.9": {("10.0.0.1:50000", "203.0.113.9:443"): (7, 8)}}      # (a line that is not a connection is not one)
    assert livebw.parse_socks("") == {}


def test_what_the_connections_moved_is_the_growth_of_each_and_never_goes_back():
    a, b = ("l:1", "r:1"), ("l:1", "r:2")
    assert livebw.sock_step(None, {a: (100, 200)}) == (0, 0)                                  # the first sight: there is no "since"
    assert livebw.sock_step({a: (100, 200)}, {a: (150, 260)}) == (50, 60)
    assert livebw.sock_step({a: (100, 200)}, {a: (150, 260), b: (10, 20)}) == (60, 80)       # a new connection counts what it has: it began since the last read
    assert livebw.sock_step({a: (100, 200), b: (10, 20)}, {a: (150, 260)}) == (50, 60)       # one that is gone says nothing more
    assert livebw.sock_step({a: (100, 200)}, {a: (30, 40)}) == (30, 40)                       # counters that went back: a new connection on the same ports


def test_a_round_counts_the_connections_of_a_reverse_proxy_into_the_numbers_of_the_tunnel(monkeypatch):
    tid, _ = make("bp-rev", typ="backpack", transport="wssmux")
    a, b = ("10.0.0.1:9901", "203.0.113.9:40001"), ("10.0.0.1:9901", "203.0.113.9:40002")
    outs = iter([sockline("s9901", *a, 1_000, 5_000) + "UP 100.0\n",
                 sockline("s9901", *a, 1_000 + 2_500_000, 5_000 + 50_000_000) + sockline("s9901", *b, 0, 12_500_000) + "UP 105.0\n",      # a grew, b is new: 62.5 MB down, 2.5 MB up in 5 s
                 sockline("s9901", *b, 2_500_000, 12_500_000 + 62_500_000) + "UP 110.0\n"])                                                  # a is gone, b grew by the same again
    seen = []
    monkeypatch.setattr(livebw, "run_light", lambda node, name, params=None, timeout=8: (seen.append(params), next(outs))[1])
    target = livebw.targets()[0]
    livebw._read(target)
    assert livebw.view() == {}                                                                # the baseline
    livebw._read(target)
    v = livebw.view()[str(tid)]
    assert v["down"] == 100.0 and v["up"] == 4.0 and v["via"] == "connections"                # (62.5 MB in 5 s = 100 Mb/s, 2.5 MB = 4 Mb/s)
    livebw._read(target)
    v = livebw.view()[str(tid)]
    assert v["down"] == 100.0 and v["up"] == 4.0 and v["span"] == 10.0                        # (125 MB in 10 s: the connection that closed did not make the number go back)
    assert seen[0] == {"IFACES": "", "SOCKS": "s9901"}


def test_no_connection_a_long_gap_or_a_test_of_the_panel_gives_no_wrong_number(monkeypatch):
    tid, _ = make("bp-rev", typ="backpack", transport="wssmux")
    a = ("10.0.0.1:9901", "203.0.113.9:40001")
    outs = iter([sockline("s9901", *a, 0, 1_000_000) + "UP 100.0\n", sockline("s9901", *a, 2_500_000, 63_500_000) + "UP 105.0\n",
                 "UP 110.0\n",                                                                # nothing connected (or a kernel that does not count): no number to give
                 sockline("s9901", *a, 0, 5_000_000_000) + "UP 115.0\n",                      # it is back, with a lot behind it (it began somewhere before): the baseline again
                 sockline("s9901", *a, 0, 5_000_000_000 + 62_500_000) + "UP 120.0\n",
                 sockline("s9901", *a, 0, 9_000_000_000) + "UP 400.0\n",                      # a gap of 280 s: what is new now began somewhere in it - the baseline again
                 sockline("s9901", *a, 0, 9_000_000_000 + 62_500_000) + "UP 405.0\n"])
    monkeypatch.setattr(livebw, "run_light", lambda node, name, params=None, timeout=8: next(outs))
    target = livebw.targets()[0]
    livebw._read(target)
    livebw._read(target)
    assert livebw.view()[str(tid)]["down"] == 100.0
    livebw._LIVE.clear()
    livebw._read(target)                                                                       # nothing connected
    assert livebw.view() == {} and not livebw._SOCKS and not livebw._READS[(target["server_id"], "sock:s9901")]
    livebw._read(target)
    assert livebw.view() == {}                                                                 # (5 GB behind it is not a rate: the first sight counts nothing)
    livebw._read(target)
    assert livebw.view()[str(tid)]["down"] == 100.0                                            # (and the next one is: 62.5 MB in 5 s)
    livebw._LIVE.clear()
    livebw._read(target)
    assert livebw.view() == {}
    livebw._read(target)
    assert livebw.view()[str(tid)]["down"] == 100.0

    livebw.hold(tid, 60)                                                                        # a test of the panel is on the tunnel: nothing of it is the users'
    livebw._sock_update(target["server_id"], "s9901", [tid], {a: (1, 2)}, 500.0, time.time())
    assert (target["server_id"], "sock:s9901") not in livebw._SOCKS and not livebw._READS[(target["server_id"], "sock:s9901")]


def test_a_round_turns_the_counters_into_the_numbers_of_the_tunnel(monkeypatch):
    tid, _ = make("gr-a")
    outs = iter([answer(100.0, **{"gr-a": (0, 0)}), answer(105.0, **{"gr-a": (62_500_000, 12_500_000)}), answer(110.0, **{"gr-a": (125_000_000, 25_000_000)})])
    seen = []
    monkeypatch.setattr(livebw, "run_light", lambda node, name, params=None, timeout=8: (seen.append((node.host, name, params)), next(outs))[1])
    target = livebw.targets()[0]
    livebw._read(target)
    assert livebw.view() == {}                                                              # one read is a baseline, not a rate
    livebw._read(target)
    assert livebw.view()[str(tid)]["down"] == 100.0 and livebw.view()[str(tid)]["up"] == 20.0 and livebw.view()[str(tid)]["span"] == 5.0
    livebw._read(target)
    v = livebw.view()[str(tid)]
    assert v["down"] == 100.0 and v["span"] == 10.0 and 0 <= v["age"] < 5                    # (over 10 s now: the window grows to 15 s)
    assert seen[0][1:] == ("iface_counters", {"IFACES": "gr-a", "SOCKS": ""}) and seen[0][0].startswith("203.0.113.")


def test_a_test_of_the_panel_on_the_link_is_not_the_users_use(monkeypatch):
    tid, sid = make("gr-a")
    ups = itertools.count(100, 5)
    state = {"rx": 0}

    def read(node, name, params=None, timeout=8):
        state["rx"] += 62_500_000                                                          # 100 Mb/s, every read
        return answer(float(next(ups)), **{"gr-a": (state["rx"], 0)})

    monkeypatch.setattr(livebw, "run_light", read)
    target = livebw.targets()[0]
    livebw._read(target)
    livebw._read(target)
    assert livebw.view()[str(tid)]["down"] == 100.0
    before = livebw._LIVE[tid]["at"]

    with livebw.quiet(tid, 60):
        livebw._read(target)
        livebw._read(target)
        assert livebw._LIVE[tid]["at"] == before and not livebw._READS[(sid, "gr-a")]      # nothing is counted while the test runs, and the window is empty

    livebw._HOLD[tid] = 0.0                                                                # (the few seconds after the test are over)
    livebw._read(target)
    assert livebw._LIVE[tid]["at"] == before                                              # one read after the test is a new baseline
    livebw._read(target)
    assert livebw._LIVE[tid]["at"] > before and livebw.view()[str(tid)]["down"] == 100.0


def test_a_server_that_does_not_answer_is_asked_less_and_less(monkeypatch):
    make("gr-a")
    calls = []

    def boom(node, name, params=None, timeout=8):
        calls.append(time.time())
        raise SSHError("ssh connection lost")

    monkeypatch.setattr(livebw, "run_light", boom)
    target = livebw.targets()[0]
    livebw._read(target)
    livebw._read(target)                                                                   # (the second one is inside the pause: it does not ask)
    assert len(calls) == 1 and livebw._BACKOFF[target["server_id"]][0] == 1
    livebw._BACKOFF[target["server_id"]] = (4, 0.0)
    livebw._read(target)
    assert len(calls) == 2 and livebw._BACKOFF[target["server_id"]][1] - time.time() <= 60.0      # (never more than a minute)


def test_the_job_reads_fast_only_while_somebody_looks_and_slowly_otherwise(monkeypatch):
    make("gr-a")
    calls = []
    monkeypatch.setattr(livebw, "run_light", lambda node, name, params=None, timeout=8: (calls.append(1), answer(100.0 + len(calls), **{"gr-a": (len(calls) * 1000, 0)}))[1])
    livebw.run()
    assert len(calls) == 1                                                                 # (the first turn after the start reads: the baseline is ready when the page opens)
    livebw.run()
    assert len(calls) == 1                                                                 # nobody looks and the last read is recent: nothing is asked of the servers
    livebw.ask()
    livebw.run()
    livebw.run()
    assert len(calls) == 3                                                                 # somebody looks: every turn reads
    livebw._ASKED[0] = time.time() - livebw.ACTIVE_FOR - 1
    livebw.run()
    assert len(calls) == 3                                                                 # he went away: back to the slow pace


def test_what_belongs_to_a_tunnel_that_is_gone_goes_too(monkeypatch):
    tid, sid = make("gr-a")
    livebw._LIVE[tid] = {"down": 1.0, "up": 1.0, "span": 5.0, "at": time.time()}
    livebw._LIVE[987654] = {"down": 9.0, "up": 9.0, "span": 5.0, "at": time.time()}
    livebw._READS[(sid, "gr-a")] = deque()
    livebw._READS[(sid, "old-card")] = deque()
    monkeypatch.setattr(livebw, "run_light", lambda *a, **k: answer(100.0))
    livebw.run()
    assert set(livebw._LIVE) == {tid} and set(livebw._READS) == {(sid, "gr-a")}


def test_the_page_gets_only_what_is_recent_and_how_old_it_is():
    now = time.time()
    livebw._LIVE[1] = {"down": 5.0, "up": 1.0, "span": 15.0, "at": now - 3}
    livebw._LIVE[2] = {"down": 6.0, "up": 1.0, "span": 15.0, "at": now - livebw.VIEW_FOR - 5}
    v = livebw.view()
    assert set(v) == {"1"} and 2.5 <= v["1"]["age"] <= 4 and v["1"]["down"] == 5.0


def test_the_live_route_marks_that_somebody_looks_and_gives_the_numbers(client):
    tid, _ = make("gr-a")
    livebw._LIVE[tid] = {"down": 42.5, "up": 3.0, "span": 15.0, "at": time.time() - 2}
    assert livebw._ASKED[0] == 0.0
    r = client.get("/api/tunnels/live").json()
    assert r["ok"] is True and r["live"][str(tid)]["down"] == 42.5 and r["live"][str(tid)]["up"] == 3.0 and abs(r["at"] - time.time()) < 5
    assert time.time() - livebw._ASKED[0] < 5                                              # (the fast pace starts: the servers are read every 5 s for the next 45 s)
    assert client.get("/api/tunnels/live").status_code == 200 and client.get("/api/tunnels/1").status_code in (200, 404)      # (the route is not taken for a tunnel id)
