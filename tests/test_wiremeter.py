"""The wire meter (services/wiremeter.py): what a tunnel costs on the wire against what its users move, measured on the real traffic.
The owner, 2026-11-13: "check all the tunnels: is every one 1:1, is there extra overhead?" - "yes, build it"."""
import itertools
import subprocess
import sys
from pathlib import Path

import pytest

from nikatel import ssh
from nikatel.config import get_settings
from nikatel.db import session
from nikatel.models import Server, Tunnel
from nikatel.services import settings as st
from nikatel.services import wiremeter
from nikatel.ssh import SSHError

HOSTS = itertools.count(40)
NOW = 1_800_000_000


@pytest.fixture(autouse=True)
def calm(monkeypatch):
    monkeypatch.setattr(wiremeter.jobs, "stopping", lambda: False)
    from nikatel.services import autopilot

    monkeypatch.setattr(autopilot, "quiet_now", lambda: False)
    yield


def make(name, typ="backpack", transport="wssmux", record=None, ports=(8076,), enabled=True, iran_online=True, iran_enabled=True):
    n = next(HOSTS)

    with session() as db:
        ir = Server(name="ir-" + name, host=f"203.0.113.{n}", role="iran", online=iran_online, enabled=iran_enabled)
        fr = Server(name="fr-" + name, host=f"198.51.100.{n}", role="foreign", online=True)
        db.add_all([ir, fr])
        db.flush()
        t = Tunnel(name=name, type=typ, transport=transport, port=9901, iran_server_id=ir.id, foreign_server_id=fr.id, state="up", forwarded_ports=list(ports), foreign_tunnel_ip=f"10.2.{n}.2",
                   record=record or {}, secrets={}, enabled=enabled)
        db.add(t)
        db.flush()
        return t.id, ir.id


def sock(spec, loc, rem, acked, received):
    return f"SOCK {spec} {loc} {rem} {acked} {received}\n"


def read(up, cards="", socks=""):
    return f"{cards}{socks}UP {up}\n"


def card(name, rx, tx, rxp, txp):
    return f"IF {name} {rx} {tx}\nPK {name} {rxp} {txp}\n"


def test_a_read_has_the_cards_the_packets_and_the_connections():
    out = card("gre-a", 1000, 2000, 10, 20) + "PK bad x y\nPK short\n" + sock("u8076", "i:8076", "r:1", 5, 6) + "UP 99.5\n"
    got = wiremeter.parse(out)
    assert got["up"] == 99.5 and got["cards"] == {"gre-a": (1000, 2000)} and got["pk"] == {"gre-a": (10, 20)} and got["socks"] == {"u8076": {("i:8076", "r:1"): (5, 6)}}      # (a line that is not a number is not a counter)
    assert wiremeter.parse("IF lo 1 2\n") is None                                                                                   # (no clock: nothing to count with)


def test_a_reverse_proxy_costs_what_its_own_connections_moved_against_what_its_users_moved():
    tun = [{"id": 1, "users": "u8076", "side": ("sock", "s9901"), "kind": "bp:wssmux"}]
    r1 = wiremeter.parse(read(100, socks=sock("u8076", "i:8076", "r:1", 1000, 2000) + sock("s9901", "i:9901", "f:5", 10_000, 20_000)))
    r2 = wiremeter.parse(read(108, socks=sock("u8076", "i:8076", "r:1", 11_000, 32_000) + sock("s9901", "i:9901", "f:5", 21_000, 52_500)))
    got = wiremeter.totals(tun, [r1, r2])
    assert got == {1: {"users": 40_000, "wire": 43_500, "span": 8.0}}                                                               # (users 10 000 + 30 000; the tunnel 11 000 + 32 500: 1.09)


def test_a_connection_that_is_new_since_the_last_read_counts_what_it_has_and_one_that_is_gone_nothing():
    tun = [{"id": 1, "users": "u8076", "side": ("sock", "s9901"), "kind": "bp:wss"}]
    r1 = wiremeter.parse(read(100, socks=sock("u8076", "i:8076", "r:1", 100, 100) + sock("s9901", "i:9901", "f:5", 100, 100)))
    r2 = wiremeter.parse(read(108, socks=sock("u8076", "i:8076", "r:2", 700, 300) + sock("s9901", "i:9901", "f:5", 1_100, 1_100)))      # (r:1 is gone, r:2 began since the last read)
    got = wiremeter.totals(tun, [r1, r2])
    assert got[1]["users"] == 1_000 and got[1]["wire"] == 2_000


def test_a_link_costs_its_card_plus_the_header_of_every_packet():
    tun = [{"id": 2, "users": "u8076", "side": ("card", "gre-a", 24), "kind": "tcpmux"}]
    r1 = wiremeter.parse(read(10, cards=card("gre-a", 1_000_000, 500_000, 1_000, 500), socks=sock("u8076", "i:8076", "r:1", 0, 0)))
    r2 = wiremeter.parse(read(18, cards=card("gre-a", 2_000_000, 600_000, 2_000, 600), socks=sock("u8076", "i:8076", "r:1", 800_000, 200_000)))
    got = wiremeter.totals(tun, [r1, r2])
    assert got[2]["users"] == 1_000_000 and got[2]["wire"] == 1_100_000 + 24 * 1_100                                               # (inner 1 100 000 bytes in 1 100 packets, 24 bytes more on each)


def test_a_step_that_cannot_be_counted_is_dropped_for_both_sides():
    tun = [{"id": 2, "users": "u8076", "side": ("card", "gre-a", 24), "kind": "tcpmux"}]
    a = wiremeter.parse(read(10, cards=card("gre-a", 5_000, 5_000, 50, 50)))
    back = wiremeter.parse(read(18, cards=card("gre-a", 100, 100, 1, 1), socks=sock("u8076", "i:8076", "r:1", 900, 900)))             # (the card was made again: its counters went back)
    clock = wiremeter.parse(read(18, cards=card("gre-a", 9_000, 9_000, 90, 90), socks=sock("u8076", "i:8076", "r:1", 900, 900)))
    same = wiremeter.parse(read(10, cards=card("gre-a", 9_000, 9_000, 90, 90)))                                                      # (the clock did not grow: the server restarted)
    gone = wiremeter.parse(read(18, socks=sock("u8076", "i:8076", "r:1", 900, 900)))                                                 # (the card is not in the read)
    assert wiremeter.totals(tun, [a, back]) == {} and wiremeter.totals(tun, [a, same]) == {} and wiremeter.totals(tun, [a, gone]) == {}
    assert wiremeter.totals(tun, [a, clock])[2]["users"] == 1_800                                                                     # (a good step is counted)


def test_the_ring_keeps_a_day_at_most_160_samples_and_no_empty_sample():
    ring = wiremeter.add_sample(None, NOW, 10, 11)
    assert ring == [[NOW, 10, 11]]
    ring = wiremeter.add_sample(ring, NOW + 600, 0, 0)
    assert ring == [[NOW, 10, 11]]                                                                                                    # (nothing moved: nothing kept)
    ring = wiremeter.add_sample(ring, NOW + 24 * 3600 + 1, 5, 6)
    assert ring == [[NOW + 24 * 3600 + 1, 5, 6]]                                                                                      # (the first one is older than a day)
    big = [[NOW + i, 1, 1] for i in range(200)]
    assert len(wiremeter.add_sample(big, NOW + 200, 1, 1)) == wiremeter.MAX_RING
    assert wiremeter.add_sample([["x"], "y", [1, 2]], NOW, 1, 1) == [[NOW, 1, 1]]                                                     # (a damaged ring is not trusted)


def test_the_ratio_is_all_the_wire_over_all_the_users_and_none_until_fifty_megabytes():
    few = [[NOW, 10_000_000, 11_000_000]]
    s = wiremeter.summary(few, NOW, "bp:wss", None)
    assert s["ratio"] is None and s["users_gb"] == 0.01 and s["samples"] == 1 and s["model"] == 1.08
    ring = [[NOW, 40_000_000, 50_000_000], [NOW + 600, 60_000_000, 62_000_000]]                                                      # (a quiet read must not count as much as a busy one: 112 / 100, not the mean of 1.25 and 1.03)
    s = wiremeter.summary(ring, NOW + 700, "tcpmux", 24)
    assert s["ratio"] == 1.12 and s["users_gb"] == 0.1 and s["wire_gb"] == 0.11 and s["hdr"] == 24 and s["kind"] == "tcpmux"
    assert wiremeter.summary([[NOW - 100_000, 90_000_000, 99_000_000]], NOW, "tcp")["samples"] == 0                                  # (older than a day: not in the ratio)


def test_the_plan_reads_the_card_of_a_link_the_connections_of_a_proxy_and_leaves_out_what_has_no_counter(monkeypatch):
    gre, _ = make("gr-a", "backhaul", "tcpmux", {"link": "gue"})
    wg, _ = make("wg-a", "backhaul", "tcpmux", {"link": "wg"})
    rev, _ = make("rv-a", "backpack", "wssmux")
    fwd, _ = make("fw-a", "chisel", "fwss", {"direction": "forward"})
    make("ud-a", "backpack", "kcp", {"udp_carrier": True})
    make("pq-a", "paqet", "paqet")
    make("dr-a", "backpack", "l3:pck", {"kind": "backpack_direct"})
    make("np-a", "backpack", "wssmux", ports=())
    make("of-a", "backpack", "wssmux", enabled=False)
    make("dn-a", "backpack", "wssmux", iran_online=False)
    make("ds-a", "backpack", "wssmux", iran_enabled=False)
    make("test-1", "backpack", "wssmux")
    monkeypatch.setattr(wiremeter, "plan_for", lambda t: type("P", (), {"iface": "gre-a" if t.id in (gre, wg) else ""})())
    got = {t["id"]: t for e in wiremeter.plan() for t in e["tunnels"]}
    assert set(got) == {gre, rev, fwd}, got                                                                                          # (the link of the wg tunnel has no known header, the others have no tunnel-side counter or are not running)
    assert got[gre]["side"] == ("card", "gre-a", 32) and got[gre]["users"] == "u8076" and got[gre]["kind"] == "gue:tcpmux"
    assert got[rev]["side"] == ("sock", "s9901") and got[rev]["kind"] == "bp:wssmux"
    assert got[fwd]["side"][0] == "sock" and got[fwd]["side"][1].startswith("d9901@") and got[fwd]["kind"] == "chisel:fwdtls"


def test_a_round_reads_each_server_three_times_and_keeps_the_ring_and_the_summary(monkeypatch):
    t1, _ = make("rv-b", "backpack", "wssmux")
    outs = iter([read(100, socks=sock("u8076", "i:8076", "r:1", 0, 0) + sock("s9901", "i:9901", "f:5", 0, 0)),
                 read(108, socks=sock("u8076", "i:8076", "r:1", 30_000_000, 30_000_000) + sock("s9901", "i:9901", "f:5", 33_000_000, 33_000_000)),
                 read(116, socks=sock("u8076", "i:8076", "r:1", 60_000_000, 60_000_000) + sock("s9901", "i:9901", "f:5", 66_000_000, 66_000_000))])
    asked = []
    monkeypatch.setattr(wiremeter, "run_light", lambda node, name, params, timeout=8: asked.append((name, params)) or next(outs))
    monkeypatch.setattr(wiremeter.time, "sleep", lambda s: None)
    monkeypatch.setattr(get_settings(), "read_only", False)
    wiremeter.run()
    assert [a[0] for a in asked] == ["wire_counters"] * 3 and asked[0][1]["SOCKS"] == "s9901 u8076" and asked[0][1]["IFACES"] == ""
    with session() as db:
        rec = db.get(Tunnel, t1).record
    assert rec["wire"]["ratio"] == 1.1 and rec["wire"]["users_gb"] == 0.12 and rec["wire"]["kind"] == "bp:wssmux" and rec["wire"]["samples"] == 1
    assert list(st.get("wiremeter")["rings"]) == [str(t1)] and st.get("wiremeter")["rings"][str(t1)][0][1] == 120_000_000


def test_a_server_that_does_not_answer_is_left_alone_and_a_read_only_panel_does_nothing(monkeypatch):
    t1, _ = make("rv-c", "backpack", "wssmux")
    calls = []

    def dead(node, name, params, timeout=8):
        calls.append(1)
        raise SSHError("no route")

    monkeypatch.setattr(wiremeter, "run_light", dead)
    monkeypatch.setattr(wiremeter.time, "sleep", lambda s: None)
    monkeypatch.setattr(get_settings(), "read_only", False)
    wiremeter.run()
    assert calls == [1]                                                                                                              # (the first read failed: the round for that server ends, nothing is stored)
    with session() as db:
        assert "wire" not in (db.get(Tunnel, t1).record or {})
    assert st.get("wiremeter")["rings"] == {}
    monkeypatch.setattr(get_settings(), "read_only", True)
    wiremeter.run()
    assert calls == [1]


def test_the_rings_of_tunnels_that_are_gone_are_dropped():
    t1, _ = make("rv-d", "backpack", "wssmux")
    st.put("wiremeter", {"rings": {str(t1): [[NOW, 1, 1]], "999999": [[NOW, 2, 2]]}})
    wiremeter.store([{"server_id": 1, "tunnels": [{"id": t1, "kind": "bp:wss", "side": ("sock", "s9901")}]}], {}, NOW + 10)
    assert list(st.get("wiremeter")["rings"]) == [str(t1)]


def test_the_script_is_reviewed_takes_only_names_and_ports_and_gives_the_packets_of_a_real_card():
    assert "wire_counters" in ssh.SCRIPTS
    for bad in ({"IFACES": "lo; reboot"}, {"SOCKS": "s80; reboot"}, {"SOCKS": "d443@example.com"}, {"SOCKS": "x80"}, {"SOCKS": "u80,$(id)"}):
        with pytest.raises(ValueError):
            ssh._script_text("wire_counters", {"IFACES": "", "SOCKS": "", **bad})

    assert "@IFACES@" not in ssh._script_text("wire_counters", {"IFACES": "", "SOCKS": "s8072 d443@203.0.113.9 u80,443"})

    if sys.platform.startswith("linux"):
        out = subprocess.run(["bash", "-c", ssh._script_text("wire_counters", {"IFACES": "lo no-such-card", "SOCKS": ""})], capture_output=True, text=True, timeout=20, check=False).stdout
        got = wiremeter.parse(out)
        assert got is not None and "lo" in got["cards"] and "lo" in got["pk"] and "no-such-card" not in got["pk"]                  # (the packet counters come with the byte counters; a card that does not exist is left out)


def test_the_page_says_the_wire_cost_in_the_tip_and_in_the_big_meter():
    app = (Path(__file__).resolve().parent.parent / "nikatel" / "web" / "app.js").read_text(encoding="utf-8")
    assert "const wireText = t =>" in app and "Wire cost: " in app and "still collecting" in app
    assert 'uxTip(t) + wireTip(t)' in app and "wireText(t) ? `<div class=\"bwbig-w\">" in app
    assert "w.ratio >= 1.35" in app                                                                                                  # (the same line as overhead.HEAVY)
